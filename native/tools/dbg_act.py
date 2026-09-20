"""dbg_act — 打印 py 对局某回合双方的动作与关键状态。

用法：env\\python.exe native/tools/dbg_act.py <spec> <turn>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
turn = int(sys.argv[2])
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
rec = battle.execute_turn(a, b)
print("turn", battle.turn)
for lbl, ar in (("A", rec.action_a), ("B", rec.action_b)):
    if ar:
        print(f"{lbl} {ar.kind} {ar.skill_name} | events: {ar.events[:5]}")
ba = battle.player_b.active
print(f"B active: {ba.name} hp={ba.current_hp}/{ba.max_hp} e={ba.energy}")
for i, s in enumerate(battle.player_b.team):
    print(f"  B[{i}] {s.name} hp={s.current_hp}/{s.max_hp} e={s.energy} fainted={s.is_fainted}")
