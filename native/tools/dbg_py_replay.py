"""dbg_py_replay — 打印 replay() 收到的 journal 构成。

用法：env\\python.exe native/tools/dbg_py_replay.py <spec_id> <turn>
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

orig = JournalReplayer.replay


def patched(self, journal):
    stats = [m for m in journal if type(m).__name__ == "StatChange"]
    if battle.turn == turn and stats:
        self_name = self.self.name if self.self else "?"
        print(f"t{battle.turn} replay self={self_name} stats={[(m.stat, m.steps, m.source) for m in stats]}")
    r = orig(self, journal)
    if battle.turn == turn and stats:
        sp = self.self
        if sp is not None:
            for e in sp.active_effects:
                print(
                    f"   post-replay eff name={e.name!r} type={type(e).__name__} "
                    f"scope={e.scope} steps={getattr(e, 'steps', None)} source={e.source!r}"
                )
    return r


JournalReplayer.replay = patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
