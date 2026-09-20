"""dbg_lead — 对比 py calc_damage 与 rust estimate_damage 的选分差异。

用法：env\\python.exe native/tools/dbg_lead.py <spec_path> <team>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.battleskill import SkillUse  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
team = sys.argv[2].upper()
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
la = a.choose_lead(battle)
battle.player_a.active_index = la
lb = b.choose_lead(battle)
battle.player_b.active_index = lb

player = battle.get_player(team)
opp = battle.get_opponent(team).active
resolver = battle._resolver
print(f"A lead={la} B lead={lb}; opponent={opp.name} hp={opp.current_hp}")
for i, sp in enumerate(player.team):
    if sp.is_fainted:
        continue
    max_dmg = 0
    best = ""
    for sk in sp.skills:
        if not sk.is_attack:
            continue
        dmg, _ = resolver.calc_damage(
            sp, opp, SkillUse(battle_skill=sk), battle.globals,
            attacker_team=team,
        )
        if dmg > max_dmg:
            max_dmg = dmg
            best = sk.name
    score = max_dmg + sp.current_hp * 0.05
    print(f"  [{i}] {sp.name} hp={sp.current_hp} max_dmg={max_dmg} ({best}) score={score:.1f}")
