"""dbg_py_calc — 打印指定回合的 calc_damage 调用参数。

用法：env\\python.exe native/tools/dbg_py_calc.py <spec_id> <turn> [power过滤]
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
pw = int(sys.argv[3]) if len(sys.argv) > 3 else None
turn = int(turn_s)

spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

import backend.vm.ops.hit as hitmod  # noqa: E402

orig = hitmod.calc_damage if hasattr(hitmod, "calc_damage") else None
if orig is None:
    from backend.engine.damage import calc_damage as orig  # noqa: E402
    import backend.engine.damage as dmgmod
else:
    import backend.vm.ops.hit as dmgmod


def patched(power, atk, dfn, **kw):
    r = orig(power, atk, dfn, **kw)
    if pw is None or power == pw:
        print(f"t{battle.turn} calc power={power} atk={atk} def={dfn} "
              f"atk_stage={kw.get('atk_stage')} def_stage={kw.get('def_stage')} "
              f"dr={kw.get('damage_reduction')} -> {r}")
    return r


dmgmod.calc_damage = patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
