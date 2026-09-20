"""dbg_act2 — 解码打印某回合双方动作、事件与队伍状态。

用法：env\\python.exe native/tools/dbg_act2.py <spec> <turn>
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
        print(f"{lbl} {ar.kind} {ar.skill_name}")
        for e in ar.events:
            print("   ", e)
for lbl, p in (("A", battle.player_a), ("B", battle.player_b)):
    act = p.active
    print(f"{lbl} active: {act.name} {act.current_hp}/{act.max_hp}")
    for i, s in enumerate(p.team):
        print(f"   {lbl}[{i}] {s.name} {s.current_hp}/{s.max_hp}")
