"""dbg_py_types — 检查 replay journal 中各 mutation 的确切类型与派发命中。

用法：env\\python.exe native/tools/dbg_py_types.py <spec_id> <turn>
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
from backend.vm.journal import StatChange as JournalStatChange  # noqa: E402

orig = JournalReplayer.replay


def patched(self, journal):
    hits = []
    for m in journal:
        if type(m).__name__ == "StatChange":
            handler = self._DISPATCH.get(type(m))
            hits.append(
                f"stat={m.stat} type_mod={type(m).__module__} qual={type(m).__qualname__} "
                f"id(type)={id(type(m))} dispatch_hit={handler is not None} "
                f"is_journal_cls={type(m) is JournalStatChange}"
            )
    if hits and battle.turn == turn:
        print(f"turn={battle.turn}")
        for h in hits:
            print("  ", h)
    return orig(self, journal)


JournalReplayer.replay = patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
