# -*- coding: utf-8 -*-
"""诊断"奉献（devotion）"的结算方式：池子会不会累积、什么时候被消耗。

用法: python dbg_devotion.py --team 虫 --turns 25
"""
from __future__ import annotations

import argparse
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

from backend.sim.agent_v2 import RuleAgentV2  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402


def dump_dev(p, tag: str) -> str:
    dev = getattr(p, "devotion", None)
    if dev is None:
        return f"{tag}: <无 devotion 属性>"
    if isinstance(dev, dict):
        return f"{tag}: {dict(list(dev.items())[:6])}"
    return f"{tag}: {dev!r}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="虫")
    ap.add_argument("--turns", type=int, default=25)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()

    factory = SimFactory()
    team = [t for t in load_meta_teams() if t.get("name") == a.team][0]
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

    print("道具:", getattr(ia, "name", ia), "| A 队:", [s.name for s in p1.team])
    for t in range(a.turns):
        if battle.is_finished:
            break
        before_a, before_b = dump_dev(p1, "A前"), dump_dev(p2, "B前")
        rec = battle.execute_turn(a1, a2)
        act_a = f"{rec.action_a.kind}:{rec.action_a.skill_name}" if rec.action_a else "-"
        act_b = f"{rec.action_b.kind}:{rec.action_b.skill_name}" if rec.action_b else "-"
        print(f"T{t+1:02d} A {act_a:16s} | B {act_b:16s}")
        print(f"     {before_a}")
        print(f"     {dump_dev(p1, 'A后')}")
    print("结束:", battle.is_finished)


if __name__ == "__main__":
    main()
