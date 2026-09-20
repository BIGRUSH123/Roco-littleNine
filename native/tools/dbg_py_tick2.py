"""dbg_py_tick2 — 包装 JournalReplayer._apply_tick 打印内部值。

用法：env\\python.exe native/tools/dbg_py_tick2.py <spec_id> <turn>
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

from backend.engine.replayer import JournalReplayer  # noqa: E402
from backend.vm.effect import AbnormalEffect  # noqa: E402
from backend.vm.journal import Tick  # noqa: E402

orig = JournalReplayer._DISPATCH.get(Tick)


def wrapper(self, m):
    sprite = self._target_sprite(m.target)
    if battle.turn == turn:
        print(f"t{battle.turn} _apply_tick name={m.abnormal_name!r} target={m.target}")
        for e in getattr(sprite, "active_effects", []):
            if isinstance(e, AbnormalEffect):
                print(
                    f"   eff name={e.name!r} stacks={e.stacks} pct={e.tick_damage_pct} "
                    f"elem={e.tick_element!r} per_stack={e.tick_per_stack} src={e.source!r}"
                )
    return orig(self, m)


JournalReplayer._DISPATCH[Tick] = wrapper

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
