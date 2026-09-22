# -*- coding: utf-8 -*-
"""阵容专精专家的对战检验：**专用专家 vs 通用专家**，同队镜像、交替执先手（成对协议）。

为什么这么测：固定同一支队（两侧阵容/道具/策略配置完全相同），只换一侧的 agent 类，
成对两局交换先手 → 量到的是"这条专精规则集"的效应，不是阵容或侧别差异。

用法（远端）:
    python native/tools/eval_team_expert.py --team 星陨队 --games 240
    python native/tools/eval_team_expert.py --team 魔偶雨天队 --games 240 --seed 7
    python native/tools/eval_team_expert.py --team 星陨队 --games 40 --shards 8 --shard 0  # 分片

产物：stdout 一行汇总 + `--json-out` 的逐项统计（胜负平、均回合、动作分布、专家规则命中）。
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai import train as T  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team, load_meta_teams, spec_from_team, strategy_from_team,
)
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402

ensure_hash_seed()

from backend.sim.agent_v2 import RuleAgentV2  # noqa: E402
from backend.sim.experts import expert_for_team  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--opponent", default="",
                    help="对手队（默认同队镜像）；给定则打「专精队 vs 该队的通用专家」，仍成对交换先手")
    ap.add_argument("--games", type=int, default=240, help="总对局数（每对两局，交换先手）")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--rules", default="",
                    help="只开这些专精规则（逗号分隔；空=全开，用于逐条量测）")
    ap.add_argument("--json-out", default="")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    factory = SimFactory()
    meta = [t for t in load_meta_teams() if t.get("name") == args.team]
    if not meta:
        raise SystemExit(f"meta 阵容里没有 {args.team!r}")
    team = meta[0]
    opp_name = args.opponent or args.team
    opp_meta = [t for t in load_meta_teams() if t.get("name") == opp_name]
    if not opp_meta:
        raise SystemExit(f"meta 阵容里没有 {opp_name!r}")
    opp_team = opp_meta[0]
    expert_cls = expert_for_team(args.team)
    if expert_cls is None:
        raise SystemExit(f"{args.team!r} 还没有专精专家（见 backend/sim/experts/__init__.py）")
    general_cls = RuleAgentV2

    pairs = max(1, args.games // 2)
    mine = [p for p in range(pairs) if p % max(1, args.shards) == args.shard]
    wins = losses = draws = turns_sum = 0
    stats: collections.Counter = collections.Counter()
    rule_hits: collections.Counter = collections.Counter()
    t0 = time.time()
    for pair in mine:
        rng = random.Random(args.seed * 1000003 + pair * 131)
        sa, _ = spec_from_team(team, rng)
        sb, _ = spec_from_team(opp_team, rng)
        ia, ib = item_from_team(team, sa), item_from_team(opp_team, sb)
        st_a = strategy_from_team(team, rng)
        st_b = strategy_from_team(opp_team, rng)
        for offset in (0, 1):
            expert_is_a = (offset == 0)          # 成对：第二局专用专家换到 B 侧
            random.seed(args.seed * 1000003 + pair * 131 + offset)
            p1 = factory.build_player("A", sa, item=copy.deepcopy(ia))
            p2 = factory.build_player("B", sb, item=copy.deepcopy(ib))
            battle = factory.build_battle(p1, p2)
            a_cls = expert_cls if expert_is_a else general_cls
            b_cls = general_cls if expert_is_a else expert_cls
            if args.rules == "none":
                rules = []                      # 显式"一条规则都不开"（对照组）
            elif args.rules:
                rules = [x for x in args.rules.split(",") if x.strip()]
            else:
                rules = None                    # 省略 = 全开
            kw = {"rules": rules} if rules is not None else {}
            a1 = (a_cls("A", p1, strategy=st_a, **kw) if a_cls is expert_cls
                  else a_cls("A", p1, strategy=st_a))
            a2 = (b_cls("B", p2, strategy=st_b, **kw) if b_cls is expert_cls
                  else b_cls("B", p2, strategy=st_b))
            for tag, agent in (("A", a1), ("B", a2)):
                role = "expert" if ((tag == "A") == expert_is_a) else "general"
                real = agent.choose_action

                def spy(b, _real=real, _role=role, _agent=agent):
                    act = _real(b)
                    stats[f"{_role}:{act.kind}"] += 1
                    if _role == "expert":
                        rule = getattr(_agent, "last_rule", "") or ""
                        if rule:
                            rule_hits[rule] += 1
                        if getattr(_agent, "last_setup", None):
                            rule_hits["setup"] += 1
                    return act
                agent.choose_action = spy
            turns = 0
            while not battle.is_finished and turns < args.max_turns:
                battle.execute_turn(a1, a2)
                turns += 1
            turns_sum += turns
            outcome, _reason = battle_outcome_a(battle, args.max_turns)
            if outcome == 0:
                draws += 1
                continue
            expert_won = (outcome > 0) == expert_is_a
            if expert_won:
                wins += 1
            else:
                losses += 1

    games = wins + losses + draws
    decisive = wins + losses
    wr = wins / max(1, decisive)
    half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / max(1, decisive))
    tag = f"{args.team} vs {opp_name}" if args.opponent else args.team
    print(f"=== {tag}：专用专家 vs 通用专家，{games} 局（成对交换先手），平 {draws} ===")
    print(f"  专用胜率 {wr:.3f} [{wr - half:.3f},{wr + half:.3f}] "
          f"（决出局 {decisive}：{wins}胜 {losses}负）  平均 {turns_sum / max(1, games):.1f} 回合 "
          f"{time.time() - t0:.0f}s")
    score = (wins + 0.5 * draws) / max(1, games)
    print(f"  平局记 0.5 的分数: 专用 {score:.3f} vs 通用 {1 - score:.3f} Δ={score * 2 - 1:+.3f}")
    for role in ("expert", "general"):
        sub = collections.Counter({k.split(":", 1)[1]: v for k, v in stats.items()
                                   if k.startswith(f"{role}:")})
        tot = sum(sub.values()) or 1
        print(f"  {role} 动作分布:", {k: round(v / tot, 3) for k, v in sub.most_common()})
    if rule_hits:
        print("  专家规则命中:", dict(rule_hits.most_common()))
    print("判定：CI 下界 > 0.5 才算专精确实更强。")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({
            "team": args.team, "opponent": opp_name,
            "expert": expert_cls.__name__, "games": games,
            "wins": wins, "losses": losses, "draws": draws, "win_rate": wr, "ci_half": half,
            "mean_turns": turns_sum / max(1, games),
            "action_mix": {k: round(v / max(1, sum(stats.values())), 4) for k, v in stats.items()},
            "rule_hits": dict(rule_hits),
        }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
