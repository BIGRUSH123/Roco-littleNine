# -*- coding: utf-8 -*-
"""打印规划层在某个具体局面里给每个候选打的分（带技能名），用于解释"为什么选它"。

用法: python dbg_plan_scores.py --team 星陨队 --turn 58 [--seed 2026]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path("/mnt/workspace/roco_remote")
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team, load_meta_teams, spec_from_team, strategy_from_team,
)
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402

ensure_hash_seed()

from backend.sim import plan as plan_mod  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402


def label(act, player) -> str:
    if act.kind == "skill":
        s = player.active
        name = s.skills[act.skill_index].name if act.skill_index < len(s.skills) else "?"
        return f"skill[{act.skill_index}]={name}"
    if act.kind == "switch":
        return f"switch→{player.team[act.switch_index].name}"
    if act.kind == "item":
        return "item"
    return act.kind


def dump(tag: str, battle, agent, player) -> None:
    from backend.sim.action import Action
    s = player.active
    opp = battle.get_opponent(tag).active
    st = agent._st(s)
    table = agent._attack_table(battle, s, opp)
    cands = agent._plan_candidates(battle, s, opp, table, st)
    rng = random.Random(12345)
    picked, info = plan_mod.choose(battle, tag, cands, plies=max(1, st.plan_depth),
                                   k_responses=3, rng=rng)
    values = info.get("values") or {}
    rows = []
    for act in cands:
        rows.append((values.get(str(act), float("nan")), label(act, player)))
    rows.sort(reverse=True)
    print(f"===== {tag} 侧：{s.name} HP {s.current_hp}/{s.max_hp} E {s.energy} "
          f"（对手 {opp.name} HP {opp.current_hp}/{opp.max_hp}）")
    print(f"      技能表: {[sk.name for sk in s.skills]}")
    print(f"      可打伤害: {[(s.skills[i].name, d, c) for i, d, c in table]}")
    for v, name in rows:
        print(f"      {v:+.4f}  {name}   ← 选中" if picked is not None and label(picked, player) == name else f"      {v:+.4f}  {name}")
    print(f"      规划层选中: {label(picked, player) if picked else None}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="星陨队")
    ap.add_argument("--turn", type=int, default=58)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()

    factory = SimFactory()
    meta = load_meta_teams()
    team = [t for t in meta if t.get("name") == a.team][0]
    rng = random.Random(a.seed)
    random.seed(a.seed)
    sa, _ = spec_from_team(team, rng)
    sb, _ = spec_from_team(team, rng)
    ia, ib = item_from_team(team, sa), item_from_team(team, sb)
    st = strategy_from_team(team, rng)
    p1 = factory.build_player("A", sa, item=ia)
    p2 = factory.build_player("B", sb, item=ib)
    battle = factory.build_battle(p1, p2)
    a1 = RuleAgentV2("A", p1, strategy=st)
    a2 = RuleAgentV2("B", p2, strategy=st)
    for _ in range(a.turn - 1):
        if battle.is_finished:
            break
        battle.execute_turn(a1, a2)
    print(f"（已推进到第 {battle.turn} 回合，finished={battle.is_finished}）")
    dump("A", battle, a1, p1)
    dump("B", battle, a2, p2)


if __name__ == "__main__":
    main()
