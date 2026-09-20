"""dbg_py_tick — 打印指定回合的 tick 明细（stacks/pct/mult/dmg）。

用法：env\\python.exe native/tools/dbg_py_tick.py <spec_id> <turn>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

sid, turn_s = sys.argv[1], sys.argv[2]
turn = int(turn_s)

spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

from backend.sim.resolver import SkillResolver  # noqa: E402

orig = SkillResolver._tick_multiplier


def patched(sprite, tick_name, element):
    m = orig(sprite, tick_name, element)
    print(f"t{battle.turn} tick_mult {sprite.name} name={tick_name} elem={element} mult={m}")
    return m


SkillResolver._tick_multiplier = staticmethod(patched)

orig_te = SkillResolver.turn_end


def te(sprites, globals_):
    for s in sprites.values():
        for ae in getattr(s, "active_effects", []):
            if getattr(ae, "stacks", 0) and ae.stacks > 0 and getattr(ae, "tick_damage_pct", 0):
                print(f"t{battle.turn} pre-tick {s.name} {ae.name} stacks={ae.stacks} pct={ae.tick_damage_pct} maxhp={s.max_hp} hp={s.current_hp}")
    return orig_te(sprites, globals_)


SkillResolver.turn_end = staticmethod(te)

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
