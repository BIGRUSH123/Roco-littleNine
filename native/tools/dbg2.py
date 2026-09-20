"""临时调试：打印 spec_0001 双方 index-1 完整摘要。"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import battle_from_spec, gate_digest, run_python  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
py_d, py_w = run_python(spec)
r = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rd = r["turns"][1]
pd = py_d[1]
print("PY  players:", json.dumps(pd["players"], ensure_ascii=False)[:1500])
print()
print("RUST players:", json.dumps(rd["players"], ensure_ascii=False)[:1500])
print()
print("PY  turn0 active:", pd["players"][0]["active_index"], pd["players"][1]["active_index"])
print("RUST turn0 active:", r["turns"][0]["players"][0]["active_index"], r["turns"][0]["players"][1]["active_index"])

# 追踪 Python 回合1事件
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
rec = battle.execute_turn(a, b)
print()
print("PY turn1 events:")
for ev in (rec.turn_start_events
           + (rec.action_a.events if rec.action_a else [])
           + (rec.action_b.events if rec.action_b else [])
           + rec.turn_end_events):
    print("  ", ev)
print("A used:", rec.action_a.skill_name if rec.action_a else None)
print("B used:", rec.action_b.skill_name if rec.action_b else None)

# 跑到 turn 3 前后的完整事件
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a2 = RuleAgent("A", battle.player_a)
b2 = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a2.choose_lead(battle)
battle.player_b.active_index = b2.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while battle.turn < 12 and not battle.is_finished:
    rec = battle.execute_turn(a2, b2)
    print(f"--- turn {battle.turn} ---")
    print("A used:", rec.action_a.skill_name if rec.action_a else None,
          "| B used:", rec.action_b.skill_name if rec.action_b else None)
    for ev in (rec.turn_start_events
               + (rec.action_a.events if rec.action_a else [])
               + (rec.action_b.events if rec.action_b else [])
               + rec.turn_end_events):
        print("   ", ev)
    a1 = battle.player_a.team[1]
    print("  A1 perm power:", a1._modifiers.get("skill.迫近攻击.power"),
          "| skill1 mods:", dict(sorted(a1.skills[1]._modifiers.items())))
    for ev in (rec.turn_start_events
               + (rec.action_a.events if rec.action_a else [])
               + (rec.action_b.events if rec.action_b else [])
               + rec.turn_end_events):
        print("   ", ev)
    print("A1 effects py:", json.dumps(rec and py_d and None or None))
    r2 = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
    print("PY  turn5 A1 effects:", json.dumps(py_d[5]["players"][0]["sprites"][1]["effects"], ensure_ascii=False))
    print("RUST turn5 A1 effects:", json.dumps(r2["turns"][5]["players"][0]["sprites"][1]["effects"], ensure_ascii=False))
    print("PY  turn5 A1 modifiers:", json.dumps(py_d[5]["players"][0]["sprites"][1]["modifiers"], ensure_ascii=False))
    print("RUST turn5 A1 modifiers:", json.dumps(r2["turns"][5]["players"][0]["sprites"][1]["modifiers"], ensure_ascii=False))
