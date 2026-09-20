"""dbg_py_combined — 一次运行内同时挂钩 replay/_apply_stat_change/sync。

用法：env\\python.exe native/tools/dbg_py_combined.py <spec_id> <turn> [输出文件]
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
from backend.vm.journal import StatChange  # noqa: E402

orig_replay = JournalReplayer.replay
orig_dispatch = JournalReplayer._DISPATCH.get(StatChange)
assert orig_dispatch is not None, "StatChange not in _DISPATCH"


def replay_patched(self, journal):
    stats = [(m.stat, m.steps) for m in journal if type(m) is StatChange]
    if stats:
        print(f"REPLAY turn={battle.turn} self={self.self.name if self.self else None} stats={stats}")
    return orig_replay(self, journal)


def dispatch_patched(self, m):
    print(f"DISPATCH turn={battle.turn} stat={m.stat} steps={m.steps} src={m.source!r}")
    return orig_dispatch(self, m)


JournalReplayer.replay = replay_patched
JournalReplayer._DISPATCH[StatChange] = dispatch_patched

# 直接包装类属性版本（若 _apply_stat_change 通过 self.xxx 调 sync 则命中）
orig_sync = JournalReplayer._sync_stat_buff_effect


def sync_patched(self, sprite, stat_key, steps, scope, source, *args, **kwargs):
    print(f"SYNC turn={battle.turn} stat={stat_key} steps={steps} sprite={sprite.name}")
    return orig_sync(self, sprite, stat_key, steps, scope, source, *args, **kwargs)


JournalReplayer._sync_stat_buff_effect = sync_patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
print("DONE")
