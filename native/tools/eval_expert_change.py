# -*- coding: utf-8 -*-
"""native/tools/eval_expert_change.py — 专家层**单条规则**的 A/B 验收（同局对照）。

设计要点（上一版在这里写错过，教训值得留档）：
  规则开关必须是**逐策略实例**的（`SpriteStrategy.trade_margin / anti_switch_loop /
  defend_threshold`），让同一局的两侧用不同的值 —— 这样"同一套阵容、同一批随机数、
  只差一条规则"才是真正的对照。旧版把开关设在模块级常量上，一局里两侧读到的是同一个
  值，只能比较"整局新口径 vs 整局旧口径"两个镜像，量到的是**先手/侧别偏差**，不是规则
  差异（那版报出的 0.536 / 0.505 之类的数字全部作废）。

成对协议：每对两局复用**同一套阵容**，第一局新口径执 A、第二局新口径执 B。
95% CI 用正态近似；只有 **CI 下界 > 0.5** 才算改动有效（另给平局记 0.5 的分数，
那是训练门控 `eval_score_for_candidate` 的口径 —— 只看决出局会漏掉"把输局拖成平局"）。

可 A/B 的改动：
  trade      交换价值（`trade_margin`：0.15 新 ｜ 1e9 = 永不换命 = 旧）
  antiloop   换人空转（`anti_switch_loop`：True 新 ｜ False 旧）
  defend     被重击就防御（`defend_threshold`：0.30 新 ｜ 0.0 = 关闭 = 旧）

用法：
    env\\python.exe native/tools/eval_expert_change.py --games 2000 --ab defend --seeds 2026,7,99
    env\\python.exe native/tools/eval_expert_change.py --games 2000 --ab both
"""
from __future__ import annotations

import argparse
import collections
import copy
import dataclasses
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402

from backend.engine.ai import train as T  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.agent_v3 import RuleAgentV3  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

NEW_VALUES = {"trade": 0.15, "antiloop": True, "defend": 0.30, "status": True,
              "plan": 1, "switchcap": 5, "setup": 6.0}
OLD_VALUES = {"trade": 1e9, "antiloop": False, "defend": 0.0, "status": False,
              "plan": 0, "switchcap": 0, "setup": 0.0}
_AB_FIELDS = {"trade": "trade_margin", "antiloop": "anti_switch_loop",
              "defend": "defend_threshold", "status": "status_counter",
              "plan": "plan_depth", "switchcap": "max_consecutive_switches",
              "setup": "setup_min_gain"}
# `both` = 三条蒸馏规则（trade+antiloop+defend，语义固定，便于与历史数字对照）；
# `all` = 再加状态反制（status_counter）。
_BUNDLES = {"both": ["trade", "antiloop", "defend"],
            "no_defend": ["trade", "antiloop"],
            "all": ["trade", "antiloop", "defend", "status"],
            # 当前出厂默认（防御层默认关闭 + 状态反制默认开启）
            "shipped": ["trade", "antiloop", "status"],
            # E2 规划层：把"最高即时伤害贪心"换成 1 回合 rollout + 效果感知叶子
            # （`backend/sim/plan.py` + `backend/sim/value.py`）
            "plan": ["plan"],
            # 反僵局两条（2026-09-22 第八批）：连续换人上限 + 打不动先增益自己
            "antistall": ["switchcap", "setup"]}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=600, help="总对局数（每对两局，交换执 A/B）")
    ap.add_argument("--ab", default="both",
                    choices=("trade", "antiloop", "defend", "status",
                             "both", "no_defend", "all", "shipped", "plan",
                             "switchcap", "setup", "antistall", "v3"))
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--seeds", default="", help="多 seed 复核（逗号分隔；非空时忽略 --seed）")
    ap.add_argument("--teams", default="random", choices=("random", "cycle"),
                    help="random=按 meta-frac 抽（默认）｜cycle=逐套 meta 阵容轮转，每队样本量相等")
    ap.add_argument("--only-team", default="",
                    help="只打名字含该子串的 meta 阵容（单队高样本 A/B）")
    ap.add_argument("--per-team", action="store_true", help="打印逐阵容战绩分解")
    ap.add_argument("--json-out", default="", help="把聚合 + 逐阵容结果写成 JSON（分片批量跑用）")
    return ap.parse_args()


def _ab_strategy(base: TeamStrategy, new: bool, mode: str) -> TeamStrategy:
    """把 base 的策略逐只复制，只改本次 A/B 的那几个字段（其余角色/血线设置保持不变）。

    `both` = 三条蒸馏规则；`no_defend` = 去掉防御层；`all` = 再加状态反制。
    `v3` = 整层比较（规则集不同，策略配置两侧共用同一份）。
    """
    if mode == "v3":
        return base
    vals = NEW_VALUES if new else OLD_VALUES
    keys = _BUNDLES.get(mode) or [mode]
    overrides = {_AB_FIELDS[k]: vals[k] for k in keys}
    return TeamStrategy(
        name=base.name,
        sprites={n: dataclasses.replace(sp, **overrides) for n, sp in base.sprites.items()},
        default=dataclasses.replace(base.default, **overrides),
    )


def _draw_roster(factory, args, meta, rng, team_index=None):
    """抽一套阵容 + 两侧基准策略（成对的两局复用同一套），返回 (roster, 阵容标签)。

    `team_index` 给定 → 强制用该 meta 阵容（逐阵容实验：每队样本量均等；随机抽签下
    40 支队每队只有几对局，噪声比效应大一个量级，看不出"这条规则对哪支队有利"）。
    """
    if team_index is not None and meta:
        i = team_index % len(meta)
        sa, _ = spec_from_team(meta[i], rng)
        sb, _ = spec_from_team(meta[i], rng)
        ia, ib = item_from_team(meta[i], sa), item_from_team(meta[i], sb)
        st_a = st_b = strategy_from_team(meta[i], rng)
        return (sa, sb, ia, ib, st_a, st_b), (meta[i].get("name") or f"#{i}")
    if meta and rng.random() < args.meta_frac:
        i = rng.randrange(len(meta))
        sa, _ = spec_from_team(meta[i], rng)
        sb, _ = spec_from_team(meta[i], rng)
        ia, ib = item_from_team(meta[i], sa), item_from_team(meta[i], sb)
        st_a = st_b = strategy_from_team(meta[i], rng)
        label = meta[i].get("name") or f"#{i}"
    else:
        sa, sb, ia, ib = T._random_teams(factory, dict(SPRITE_RANDOM_POOL),
                                        optimal_frac=0.95, meta_frac=0.0, rng=rng)
        st_a = st_b = TeamStrategy(default=SpriteStrategy())
        label = "随机阵容"
    return (sa, sb, ia, ib, st_a, st_b), label


def _play(factory, args, mode: str, roster, new_is_a: bool,
          stats: collections.Counter) -> tuple[int, int, int]:
    """同一套阵容、同一批随机数：一侧新口径、另一侧旧口径。

    返回 (A 方是否胜, 是否平, 回合数)。
    """
    sa, sb, ia, ib, st_a, st_b = roster
    # 道具是可变对象（uses/last_use_turn）——成对的两局必须各用一份深拷贝，
    # 否则第二局拿到的是"已用过进化之力"的道具状态（这个坑在 §4d 踩过一次）。
    p1 = factory.build_player("A", sa, item=copy.deepcopy(ia))
    p2 = factory.build_player("B", sb, item=copy.deepcopy(ib))
    battle = factory.build_battle(p1, p2)
    # `v3` 比的是整层规则集（V3 vs V2）；其余 mode 两侧都是 V2，只差一个字段
    new_cls, old_cls = (RuleAgentV3, RuleAgentV2) if mode == "v3" else (RuleAgentV2, RuleAgentV2)
    a1 = (new_cls if new_is_a else old_cls)(
        "A", p1, strategy=_ab_strategy(st_a, new_is_a, mode))
    a2 = (old_cls if new_is_a else new_cls)(
        "B", p2, strategy=_ab_strategy(st_b, not new_is_a, mode))
    for team, agent in (("A", a1), ("B", a2)):
        real = agent.choose_action

        def spy(b, _real=real, _t=team):
            act = _real(b)
            stats[f"{_t}:{act.kind}"] += 1
            return act
        agent.choose_action = spy
    turns = 0
    while not battle.is_finished and turns < args.max_turns:
        battle.execute_turn(a1, a2)
        turns += 1
    outcome, _reason = battle_outcome_a(battle, args.max_turns)
    if outcome == 0:
        return 0, 1, turns
    return (1 if outcome > 0 else 0), 0, turns


def main() -> None:
    ensure_hash_seed()
    args = parse_args()
    factory = SimFactory()
    meta = load_meta_teams()
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()] or [args.seed]
    per_seed = max(2, args.games // len(seeds))
    # 两侧打的是同一批对局（交换执 A/B），所以旧侧的战绩就是新侧的镜像：
    # old_wins = new_losses，old_draws = new_draws。只统计新侧，避免两份计数漂移
    # （旧版分开累加 new_games/old_games，算出来的"平局记 0.5 分数"能超过 1.0）。
    new_wins = new_losses = new_draws = draws = turns_sum = games = 0
    stats: collections.Counter = collections.Counter()
    per_team: dict[str, list[int]] = {}      # 阵容标签 -> [新胜, 新负, 平]
    only = args.only_team.strip()
    forced_index = None
    if only:
        hits = [i for i, t in enumerate(meta) if only in (t.get("name") or "")]
        if not hits:
            raise SystemExit(f"--only-team {only!r} 在 meta 阵容里没有匹配项")
        forced_index = hits[0]
        print(f"[只打阵容] {meta[forced_index].get('name')}（共 {len(hits)} 个匹配，取第一个）")
    t0 = time.time()
    pair_no = -1
    for seed in seeds:
        rng = random.Random(seed)
        for pair_start in range(0, per_seed, 2):
            # 成对：同一套阵容连打两局，第二局把新口径换到另一侧
            pair_no += 1
            if forced_index is not None:
                roster, label = _draw_roster(factory, args, meta, rng,
                                             team_index=forced_index)
            elif args.teams == "cycle":
                roster, label = _draw_roster(factory, args, meta, rng,
                                             team_index=pair_no)
            else:
                roster, label = _draw_roster(factory, args, meta, rng)
            bucket = per_team.setdefault(label, [0, 0, 0])
            for offset in (0, 1):
                if pair_start + offset >= per_seed:
                    break
                new_is_a = (offset == 0)
                random.seed(seed * 1000003 + pair_start * 131 + offset)
                win_a, draw, turns = _play(factory, args, args.ab, roster,
                                           new_is_a, stats)
                games += 1
                turns_sum += turns
                draws += draw
                if draw:
                    new_draws += 1
                    bucket[2] += 1
                    continue
                if (win_a == 1) == new_is_a:
                    new_wins += 1
                    bucket[0] += 1
                else:
                    new_losses += 1
                    bucket[1] += 1
    decisive = new_wins + new_losses
    wr = new_wins / max(1, decisive)
    half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / max(1, decisive))
    print(f"=== A/B {args.ab}：{games} 局（种子 {seeds}），平 {draws} === "
          f"新口径胜率 {wr:.3f} [{wr - half:.3f},{wr + half:.3f}] "
          f"（决出局 {decisive}：新 {new_wins}胜 {new_losses}负）"
          f" 平均 {turns_sum / max(1, games):.1f} 回合 {time.time() - t0:.0f}s")
    # 平局记 0.5 的分数（训练门控口径）：只看决出局会漏掉"把输局拖成平局"这类改动。
    score_new = (new_wins + 0.5 * new_draws) / max(1, games)
    score_old = (new_losses + 0.5 * new_draws) / max(1, games)
    print(f"  平局记 0.5 的分数（每侧 {games} 局，含平 {new_draws}）："
          f"新口径 {score_new:.3f} vs 旧口径 {score_old:.3f} Δ={score_new - score_old:+.3f}")
    kinds = collections.Counter({k.split(":", 1)[1]: v for k, v in stats.items()})
    total = sum(kinds.values()) or 1
    print("  动作分布:", {k: round(v / total, 3) for k, v in kinds.most_common()})
    if args.per_team or args.teams == "cycle" or forced_index is not None:
        # 逐阵容：胜率 <0.5 表示这条新口径**对这支队有害**。异质性本身就是结论——
        # 全局一个值时看总胜率，逐阵容看它是不是"几队大赚、几队大亏"相互抵消。
        print("  === 逐阵容（新口径胜率；<0.5 = 该规则对这队有害）===")
        rows = []
        for label, (w, l, d) in per_team.items():
            dec = w + l
            wr_t = w / dec if dec else 0.0
            half_t = 1.96 * math.sqrt(max(1e-9, wr_t * (1 - wr_t)) / dec) if dec else 0.0
            rows.append((wr_t, label, w, l, d, half_t))
        for wr_t, label, w, l, d, half_t in sorted(rows):
            bar = "▁" * int(round(wr_t * 20)) + "▔" * (20 - int(round(wr_t * 20)))
            print(f"    {label[:16]:18s} {w:3d}胜 {l:3d}负 平{d:3d}  "
                  f"{wr_t:.3f}±{half_t:.3f}  {bar}")
        spread = [r[0] for r in rows if (r[2] + r[3]) >= 10]
        if len(spread) >= 2:
            print(f"    逐阵容胜率跨度: {min(spread):.3f} ~ {max(spread):.3f} "
                  f"（std={statistics.pstdev(spread):.3f}，n≥10 的 {len(spread)} 支队）")
    print("判定：CI 下界 > 0.5 才算改动有效。")
    if args.json_out:
        payload = {
            "ab": args.ab, "games": games, "draws": draws, "seeds": seeds,
            "max_turns": args.max_turns, "teams_mode": args.teams,
            "only_team": only,
            "new_wins": new_wins, "new_losses": new_losses, "new_draws": new_draws,
            "win_rate": wr, "ci_half": half,
            "score_delta": score_new - score_old,
            "mean_turns": turns_sum / max(1, games),
            "per_team": {k: {"胜": v[0], "负": v[1], "平": v[2]} for k, v in per_team.items()},
            "action_mix": {k: round(v / total, 4) for k, v in kinds.most_common()},
        }
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  JSON 已写入 {args.json_out}")


if __name__ == "__main__":
    main()
