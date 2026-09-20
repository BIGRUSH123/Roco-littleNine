"""dbg_py_sync — 包装 JournalReplayer._sync_stat_buff_effect 记录调用。

用法：env\\python.exe native/tools/dbg_py_sync.py <spec_id> <turn>
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

orig = JournalReplayer._sync_stat_buff_effect


def patched(self, sprite, stat_key, steps, scope, source, *args, **kwargs):
    if battle.turn == turn:
        print(
            f"t{battle.turn} SYNC stat={stat_key} steps={steps} scope={scope} "
            f"src={source!r} kwargs={kwargs} sprite={sprite.name}"
        )
    return orig(self, sprite, stat_key, steps, scope, source, *args, **kwargs)


JournalReplayer._sync_stat_buff_effect = patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
