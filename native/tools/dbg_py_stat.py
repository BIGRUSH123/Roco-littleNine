"""dbg_py_stat — 打印指定回合的 StatChange 回放明细。

用法：env\\python.exe native/tools/dbg_py_stat.py <spec_id> <turn> [stat过滤]
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
flt = sys.argv[3] if len(sys.argv) > 3 else None
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
from backend.vm.journal import StatChange  # noqa: E402

orig = JournalReplayer._DISPATCH.get(StatChange)


def wrapper(self, m):
    if battle.turn == turn and (flt is None or m.stat == flt):
        print(
            f"t{battle.turn} STAT stat={m.stat} steps={m.steps} scope={m.scope} "
            f"target={m.target} source={m.source!r} self={self.self.name if self.self else None}"
        )
    return orig(self, m)


JournalReplayer._DISPATCH[StatChange] = wrapper

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
