# -*- coding: utf-8 -*-
"""native/tools/tune_ev_params.py — 校准期望值层的常数（E5 之后的调参轮）。

为什么调参而不是继续加机制：EV 层与旧启发式在 300 局配对上打成平手
（0.463 [0.403,0.524]），而它的关键常数全是手设的（`buff_credit` / `switch_position_weight` /
`kill_bonus` / `status_tempo_penalty` / `defense_cooldown_penalty` …），旧启发式那几条阈值
则是社区经验磨过的。本工具用**配对胜率**当目标，做一轮"随机搜索 + 逐轮淘汰"。

口径（避免自欺）：
  - 目标 = 对**旧启发式**（`ev_decide=False`）的配对胜率，**相邻两局交换 A/B**；
  - 同一轮里所有候选共用同一批对手/阵容（同 seed → 同 matchups），只比决策差异；
  - 噪声带 ≈ ±0.06 @ n=300 → 逐轮加大局数：n=100 筛选 → n=200 → n=400；
  - **最终用没参与搜索的 seed 复核**（`--confirm-seeds`），并报 95% CI；
  - 判定规则：确认集上 **CI 下界 > 0.5** 才算"确实更强"，否则如实报告"无提升"。
  - 每轮结果落盘 `checkpoints/ev_tuning.json`（含每次评估的胜率/局数/参数）。

用法（项目根、项目解释器）：
    env\\python.exe native/tools/tune_ev_params.py --configs 24 --seed 2026
    env\\python.exe native/tools/tune_ev_params.py --confirm-only    # 只复核已存的最优参数
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai import train as T  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.sim import belief as belief_mod  # noqa: E402
from backend.sim import ev  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

OUT = Path("checkpoints") / "ev_tuning.json"

# ── 待校准的参数与搜索范围（下限、上限、是否对数刻度）──
EV_SPACE: dict[str, tuple[float, float, bool]] = {
    "kill_bonus": (0.15, 1.20, False),
    "buff_credit": (0.0, 0.45, False),
    "switch_position_weight": (0.0, 0.90, False),
    "status_tempo_penalty": (0.0, 0.10, False),
    "defense_cooldown_penalty": (0.0, 0.30, False),
    "countered_penalty": (0.0, 0.20, False),
    "energy_weight": (0.0, 0.06, False),
    "risk_lambda": (0.0, 0.60, False),
}
BELIEF_SPACE: dict[str, tuple[float, float, bool]] = {
    "switch_base": (-2.0, 0.0, False),
    "w_lethal": (0.4, 3.2, False),
    "w_tick_doom": (0.5, 3.5, False),
    "w_countered": (0.0, 1.6, False),
    "w_low_energy": (0.0, 1.6, False),
    "w_hp_low": (0.0, 1.8, False),
    "w_just_entered": (-2.4, 0.0, False),
}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", type=int, default=24, help="首轮随机候选数")
    ap.add_argument("--games", type=int, default=100, help="首轮每候选局数（逐轮 ×2）")
    ap.add_argument("--rounds", type=int, default=3, help="淘汰轮数（每轮保留前 1/3）")
    ap.add_argument("--seed", type=int, default=2026, help="搜索用 seed（决定对手/阵容）")
    ap.add_argument("--confirm-seeds", default="7,99,2027", help="最终复核用的、未参与搜索的 seed")
    ap.add_argument("--confirm-games", type=int, default=400)
    ap.add_argument("--space", default="ev", choices=("ev", "belief", "both"),
                    help="调 EV 常数 / 信念先验 / 两者（两者时先 EV 后信念）")
    ap.add_argument("--start-from", default="", help="从该 JSON 的最优参数出发（继续调）")
    ap.add_argument("--confirm-only", action="store_true", help="只复核已存的最优参数")
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--optimal-frac", type=float, default=0.95)
    ap.add_argument("--max-turns", type=int, default=40)
    return ap.parse_args()


# ══════════════════════════════════════════════════════════════════
# 一组配对对局：候选（EV 层） vs 旧启发式
# ══════════════════════════════════════════════════════════════════

def _play_pair_games(factory, args, meta, seed: int, games: int,
                     ev_params: ev.EVParams,
                     belief_params: belief_mod.BeliefParams) -> tuple[int, int]:
    """返回 (候选胜局, 有效局)。相邻两局交换 A/B 抵消先手优势。"""
    rng = random.Random(seed)
    random.seed(seed)
    wins = 0
    decisive = 0
    for g in range(games):
        cand_team = "A" if g % 2 == 0 else "B"
        meta_i = rng.randrange(len(meta)) if (meta and rng.random() < args.meta_frac) else None
        if meta_i is not None:
            specs_a, _ = spec_from_team(meta[meta_i], rng)
            specs_b, _ = spec_from_team(meta[meta_i], rng)
            item_a = item_from_team(meta[meta_i], specs_a)
            item_b = item_from_team(meta[meta_i], specs_b)
        else:
            specs_a, specs_b, item_a, item_b = T._random_teams(
                factory, dict(SPRITE_RANDOM_POOL),
                optimal_frac=args.optimal_frac, meta_frac=0.0)
        p1 = factory.build_player("A", specs_a, item=item_a)
        p2 = factory.build_player("B", specs_b, item=item_b)
        battle = factory.build_battle(p1, p2)

        cand_agent = RuleAgentV2(cand_team, p1 if cand_team == "A" else p2,
                                 strategy=TeamStrategy(default=SpriteStrategy()),
                                 ev_params=ev_params, belief_params=belief_params)
        opp_team = "B" if cand_team == "A" else "A"
        legacy_agent = RuleAgentV2(opp_team, p2 if cand_team == "A" else p1,
                                   strategy=TeamStrategy(
                                       default=SpriteStrategy(ev_decide=False)))
        agent_a = cand_agent if cand_team == "A" else legacy_agent
        agent_b = legacy_agent if cand_team == "A" else cand_agent

        turns = 0
        while not battle.is_finished and turns < args.max_turns:
            battle.execute_turn(agent_a, agent_b)
            turns += 1
        outcome, _reason = battle_outcome_a(battle, args.max_turns)
        if outcome == 0:
            continue
        decisive += 1
        if (outcome > 0) == (cand_team == "A"):
            wins += 1
    return wins, decisive


def _ci(wins: int, n: int) -> tuple[float, float, float]:
    if n <= 0:
        return 0.5, 0.0, 1.0
    p = wins / n
    half = 1.96 * math.sqrt(max(1e-9, p * (1 - p)) / n)
    return p, max(0.0, p - half), min(1.0, p + half)


def _sample_config(rng: random.Random, space: dict, base: dict, radius: float) -> dict:
    """围绕当前最优做局部扰动（比在全空间均匀撒点省样本：手设默认本就不差）。"""
    out = dict(base)
    for name, (lo, hi, _log) in space.items():
        span = (hi - lo) * radius
        out[name] = round(min(hi, max(lo, base.get(name, (lo + hi) / 2) + rng.uniform(-span, span))), 4)
    return out


def _params_from(cfg: dict) -> tuple[ev.EVParams, belief_mod.BeliefParams]:
    ev_keys = {f.name for f in fields(ev.EVParams)}
    bl_keys = {f.name for f in fields(belief_mod.BeliefParams)}
    ev_cfg = {k: v for k, v in cfg.items() if k in ev_keys}
    bl_cfg = {k: v for k, v in cfg.items() if k in bl_keys}
    return ev.EVParams(**ev_cfg), belief_mod.BeliefParams(**bl_cfg)


def _search(factory, args, meta, space: dict, base_cfg: dict, label: str,
            log: list) -> dict:
    """局部扰动 + 逐轮淘汰（每轮保留前 1/3，局数翻倍、扰动半径减半）。"""
    rng = random.Random(args.seed + 17)
    candidates = [_sample_config(rng, space, base_cfg, 0.35) for _ in range(args.configs)]
    best_cfg, best_p, best_n = dict(base_cfg), 0.0, 0
    games = args.games
    for rnd in range(args.rounds):
        radius = 0.35 / (2 ** rnd)
        scored = []
        for cfg in candidates:
            ev_p, bl_p = _params_from(cfg)
            t0 = time.time()
            wins, n = _play_pair_games(factory, args, meta, args.seed, games, ev_p, bl_p)
            p, lo, hi = _ci(wins, n)
            scored.append((p, wins, n, cfg))
            log.append({"stage": label, "round": rnd, "games": n, "wins": wins,
                        "win_rate": p, "ci": [lo, hi], "cfg": cfg,
                        "secs": round(time.time() - t0, 1)})
            print(f"    [{label} r{rnd}] n={n} 胜率 {p:.3f} [{lo:.3f},{hi:.3f}] "
                  f"{time.time() - t0:.0f}s cfg={ {k: cfg[k] for k in space} }")
        scored.sort(key=lambda x: -x[0])
        best_cfg, best_p, best_n = scored[0][3], scored[0][0], scored[0][2]
        keep = max(2, len(scored) // 3)
        seeds = [cfg for _p, _w, _n, cfg in scored[:keep]]
        candidates = seeds + [_sample_config(rng, space, best_cfg, radius)
                              for _ in range(max(0, args.configs // 3))]
        games *= 2
        print(f"    [{label} r{rnd}] 保留 {keep} 个（最优 {scored[0][0]:.3f}）→ 下轮 n={games}、半径 {radius / 2:.2f}")
    print(f"  {label} 最优（搜索集 n={best_n}，胜率 {best_p:.3f}）：")
    print(f"    {json.dumps({k: best_cfg[k] for k in space}, ensure_ascii=False)}")
    return best_cfg


def _confirm(factory, args, meta, cfg: dict, log: list) -> tuple[float, int, list]:
    """用未参与搜索的 seed 复核（多 seed 合并）。"""
    ev_p, bl_p = _params_from(cfg)
    wins = n = 0
    per_seed = []
    for s in [int(x) for x in args.confirm_seeds.split(",") if x.strip()]:
        w, nn = _play_pair_games(factory, args, meta, s, args.confirm_games, ev_p, bl_p)
        per_seed.append((s, w, nn))
        wins += w
        n += nn
        print(f"    复核 seed={s}: {w}/{nn} = {w / max(1, nn):.3f}")
    p, lo, hi = _ci(wins, n)
    log.append({"stage": "confirm", "seeds": per_seed, "wins": wins, "games": n,
                "win_rate": p, "ci": [lo, hi], "cfg": cfg})
    print(f"  复核合计 {wins}/{n} = {p:.3f} [{lo:.3f},{hi:.3f}]")
    return p, n, per_seed


def main() -> None:
    ensure_hash_seed()
    args = parse_args()
    factory = SimFactory()
    meta = load_meta_teams()
    log: list[dict] = []

    base_cfg: dict = {**asdict(ev.EVParams()), **asdict(belief_mod.BeliefParams())}
    if args.start_from and Path(args.start_from).exists():
        saved = json.loads(Path(args.start_from).read_text(encoding="utf-8"))
        base_cfg.update(saved.get("best_cfg") or {})
        print(f"从 {args.start_from} 的最优参数出发")

    if args.confirm_only:
        cfg = base_cfg
        print("=== 仅复核 ===")
        _confirm(factory, args, meta, cfg, log)
    else:
        spaces = []
        if args.space in ("ev", "both"):
            spaces.append(("EV", EV_SPACE))
        if args.space in ("belief", "both"):
            spaces.append(("信念", BELIEF_SPACE))
        for label, space in spaces:
            print(f"=== 校准 {label} 常数（{len(space)} 个参数，{args.configs} 个随机候选）===")
            base_cfg = _search(factory, args, meta, space, base_cfg, label, log)
        print("=== 未参与搜索的 seed 上复核 ===")
        _confirm(factory, args, meta, base_cfg, log)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"args": vars(args), "best_cfg": base_cfg, "log": log},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"结果已写入 {OUT}")
    print("判定：复核 CI 下界 > 0.5 才算确实更强；否则如实报告无提升（不要改默认值）。")


if __name__ == "__main__":
    main()
