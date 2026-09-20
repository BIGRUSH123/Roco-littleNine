"""临时调试12：打印 py turn 8 详细事件。"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while battle.turn < 9 and not battle.is_finished:
    rec = battle.execute_turn(a, b)
    if battle.turn in (7, 8):
        print(f"=== turn {battle.turn} ===")
        print("A action:", rec.action_a.kind, rec.action_a.skill_name if rec.action_a else "")
        print("B action:", rec.action_b.kind, rec.action_b.skill_name if rec.action_b else "")
        for ev in (rec.turn_start_events
                   + (rec.action_a.events if rec.action_a else [])
                   + (rec.action_b.events if rec.action_b else [])
                   + rec.turn_end_events):
            print("   ", ev)
        print("A active:", battle.player_a.active.name, battle.player_a.active.current_hp, "hp")
        print("B active:", battle.player_b.active.name, battle.player_b.active.current_hp, "hp")
