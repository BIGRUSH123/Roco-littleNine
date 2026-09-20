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
import math
import random
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
from backend.sim.factory import SimFactory  # noqa: E402

NEW_VALUES = {"trade": 0.15, "antiloop": True, "defend": 0.30}
OLD_VALUES = {"trade": 1e9, "antiloop": False, "defend": 0.0}
_AB_FIELDS = {"trade": "trade_margin", "antiloop": "anti_switch_loop",
              "defend": "defend_threshold"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=600, help="总对局数（每对两局，交换执 A/B）")
    ap.add_argument("--ab", default="both",
                    choices=("trade", "antiloop", "defend", "both", "no_defend"))
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--seeds", default="", help="多 seed 复核（逗号分隔；非空时忽略 --seed）")
    return ap.parse_args()


def _ab_strategy(base: TeamStrategy, new: bool, mode: str) -> TeamStrategy:
    """把 base 的策略逐只复制，只改本次 A/B 的那几个字段（其余角色/血线设置保持不变）。

    `both` = 三条一起（当前默认口径）；`no_defend` = 去掉防御层（用于判断防御该不该留）。
    """
    vals = NEW_VALUES if new else OLD_VALUES
    if mode == "both":
        keys = list(_AB_FIELDS)
    elif mode == "no_defend":
        keys = ["trade", "antiloop"]
    else:
        keys = [mode]
    overrides = {_AB_FIELDS[k]: vals[k] for k in keys}
    return TeamStrategy(
        name=base.name,
        sprites={n: dataclasses.replace(sp, **overrides) for n, sp in base.sprites.items()},
        default=dataclasses.replace(base.default, **overrides),
    )


def _draw_roster(factory, args, meta, rng):
    """抽一套阵容 + 两侧基准策略（成对的两局复用同一套）。"""
    if meta and rng.random() < args.meta_frac:
        i = rng.randrange(len(meta))
        sa, _ = spec_from_team(meta[i], rng)
        sb, _ = spec_from_team(meta[i], rng)
        ia, ib = item_from_team(meta[i], sa), item_from_team(meta[i], sb)
        st_a = st_b = strategy_from_team(meta[i], rng)
    else:
        sa, sb, ia, ib = T._random_teams(factory, dict(SPRITE_RANDOM_POOL),
                                        optimal_frac=0.95, meta_frac=0.0, rng=rng)
        st_a = st_b = TeamStrategy(default=SpriteStrategy())
    return sa, sb, ia, ib, st_a, st_b


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
    a1 = RuleAgentV2("A", p1, strategy=_ab_strategy(st_a, new_is_a, mode))
    a2 = RuleAgentV2("B", p2, strategy=_ab_strategy(st_b, not new_is_a, mode))
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
    new_wins = old_wins = draws = turns_sum = games = 0
    new_games = old_games = new_draws = old_draws = 0
    stats: collections.Counter = collections.Counter()
    t0 = time.time()
    for seed in seeds:
        rng = random.Random(seed)
        for pair_start in range(0, per_seed, 2):
            # 成对：同一套阵容连打两局，第二局把新口径换到另一侧
            roster = _draw_roster(factory, args, meta, rng)
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
                new_side = new_is_a
                if new_side:
                    new_games += 1
                    new_draws += draw
                else:
                    old_games += 1
                    old_draws += draw
                if draw:
                    continue
                new_won = (win_a == 1) == new_side
                if new_won:
                    new_wins += 1
                else:
                    old_wins += 1
    decisive = new_wins + old_wins
    wr = new_wins / max(1, decisive)
    half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / max(1, decisive))
    print(f"=== A/B {args.ab}：{games} 局（种子 {seeds}），平 {draws} === "
          f"新口径胜率 {wr:.3f} [{wr - half:.3f},{wr + half:.3f}] "
          f"({new_wins}胜 / {old_wins}负) 平均 {turns_sum / max(1, games):.1f} 回合 "
          f"{time.time() - t0:.0f}s")
    # 平局记 0.5 的分数（训练门控口径）：只看决出局会漏掉"把输局拖成平局"这类改动。
    score_new = new_wins + 0.5 * new_draws
    score_old = old_wins + 0.5 * old_draws
    print(f"  平局记 0.5 的分数：新口径 {score_new / max(1, new_games):.3f}"
          f"（{new_games} 局，含平 {new_draws}）"
          f" vs 旧口径 {score_old / max(1, old_games):.3f}"
          f"（{old_games} 局，含平 {old_draws}）"
          f" Δ={(score_new / max(1, new_games)) - (score_old / max(1, old_games)):+.3f}")
    kinds = collections.Counter({k.split(":", 1)[1]: v for k, v in stats.items()})
    total = sum(kinds.values()) or 1
    print("  动作分布:", {k: round(v / total, 3) for k, v in kinds.most_common()})
    print("判定：CI 下界 > 0.5 才算改动有效。")


if __name__ == "__main__":
    main()
