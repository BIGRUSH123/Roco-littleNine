# -*- coding: utf-8 -*-
"""validate_v2.py — RuleAgentV2 对比验证：胜率 + 对局长度。"""
import collections
import io
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

_log = io.open(ROOT / "native" / "tools" / "_v2_validate_log.txt", "w", encoding="utf-8")


def out(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _log.write(s + "\n")
    _log.flush()


def play(spec_pair, agent_a_cls, agent_b_cls, factory, max_turns=200):
    """返回 (winner, turns, end_kind)。"""
    from backend.sim.agent import RuleAgent

    ta, tb, ia, ib, seed = spec_pair
    random.seed(seed + 1)
    import numpy as np
    np.random.seed((seed + 1) % (2**32 - 1))
    p1 = factory.build_player("A", ta, item=ia)
    p2 = factory.build_player("B", tb, item=ib)
    battle = factory.build_battle(p1, p2)
    a = agent_a_cls("A", p1)
    b = agent_b_cls("B", p2)
    battle.player_a.active_index = a.choose_lead(battle)
    battle.player_b.active_index = b.choose_lead(battle)
    battle._invalidate_ctx_team_cache()
    while not battle.is_finished and battle.turn < max_turns:
        battle.execute_turn(a, b)
    return battle.winner or "draw", battle.turn, battle.is_finished


def main() -> None:
    from backend.engine.ai.train import _load_sprite_skills, _random_item, _random_teams
    from backend.sim.agent import RuleAgent
    from backend.sim.agent_v2 import RuleAgentV2
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    sprite_skills = _load_sprite_skills()

    # 生成 100 个 6v6 对阵（配对种子，双方同阵容）
    matchups = []
    for seed in range(1000, 1000 + 100):
        random.seed(seed)
        ta, tb, ia, ib = _random_teams(factory, sprite_skills)
        matchups.append((ta, tb, ia, ib, seed))

    # 1. V2 vs 旧 RuleAgent（A=V2，B=旧）
    results = []
    for m in matchups:
        results.append(play(m, RuleAgentV2, RuleAgent, factory))
    wins = sum(1 for w, _, _ in results if w == "A")
    draws = sum(1 for w, _, _ in results if w == "draw")
    turns = [t for _, t, _ in results]
    dec = sum(1 for _, _, fin in results if fin)
    out(f"V2 vs 旧RuleAgent: {wins}胜 {draws}平 {100 - wins - draws}负 "
        f"胜率={wins / len(results):.0%} | mean_turns={sum(turns) / len(turns):.0f} "
        f"decisive={dec / len(results):.0%}")

    # 2. V2 vs V2（内战）：对局长度
    results2 = [play(m, RuleAgentV2, RuleAgentV2, factory) for m in matchups]
    turns2 = [t for _, t, _ in results2]
    dec2 = sum(1 for _, _, fin in results2 if fin)
    capped = sum(1 for t in turns2 if t >= 200)
    out(f"V2 vs V2 内战: mean_turns={sum(turns2) / len(turns2):.0f} "
        f"median={sorted(turns2)[len(turns2) // 2]} decisive={dec2 / len(results2):.0%} "
        f"打满200={capped}")
    out(f"   回合直方图: {sorted(collections.Counter(min(t // 10 * 10, 200) for t in turns2).items())}")

    # 3. 旧 vs 旧 基线（对局长度）
    results3 = [play(m, RuleAgent, RuleAgent, factory) for m in matchups[:40]]
    turns3 = [t for _, t, _ in results3]
    dec3 = sum(1 for _, _, fin in results3 if fin)
    out(f"旧 vs 旧 基线(40局): mean_turns={sum(turns3) / len(turns3):.0f} "
        f"decisive={dec3 / len(results3):.0%}")


if __name__ == "__main__":
    main()
