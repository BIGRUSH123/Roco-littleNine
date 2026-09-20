"""dbg_t0 — 探测 py 构造/首发后的 A0 修饰符来源。

用法：env\\python.exe native/tools/dbg_t0.py <spec_path>
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

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a0 = battle.player_a.team[0]
print("after construction:")
print("  modifiers:", dict(a0._modifiers))
print("  effects:", [(e.name, e.scope, getattr(e, "steps", None)) for e in a0.active_effects])
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
print("after lead:")
print("  modifiers:", dict(a0._modifiers))
print("  effects:", [(e.name, e.scope, getattr(e, "steps", None)) for e in a0.active_effects])
