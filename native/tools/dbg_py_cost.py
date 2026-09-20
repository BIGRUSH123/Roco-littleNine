"""dbg_py_cost — 打印指定回合 py 的 skill_energy_cost 调用明细。

用法：env\\python.exe native/tools/dbg_py_cost.py <spec_id> <turn>
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

from backend.sim.battle import Battle  # noqa: E402

orig = Battle.skill_energy_cost


def patched(self, team, user, skill, skill_index=None, *args, **kwargs):
    cost = orig(self, team, user, skill, skill_index, *args, **kwargs)
    if self.turn == turn:
        print(
            f"t{self.turn} {team} {skill.name}: base={skill.energy_cost} "
            f"skill_mods={skill._modifiers!r} sprite_ec={user._modifiers.get('energy_cost')!r} "
            f"mech={skill._mech_energy_reduction} -> cost={cost}"
        )
    return cost


Battle.skill_energy_cost = patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
