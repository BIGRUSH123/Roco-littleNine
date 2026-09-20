"""dbg_stat — 追踪指定 stat 的 ModifierInjection 回放（含 source/target/值）。

用法：env\\python.exe native/tools/dbg_stat.py <spec> <turn> <stat>
"""

from __future__ import annotations

import json
import random
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine import replayer as R  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
turn = int(sys.argv[2])
stat = sys.argv[3]

orig = R.JournalReplayer._apply_modifier

_last_trigger = {"v": ""}

from backend.engine.battle import BattleVMEngine as _BVE  # noqa: E402

_orig_fire = _BVE._fire_post_event


def _wrap_fire(self, trigger, ctx, replayer):
    _last_trigger["v"] = trigger
    return _orig_fire(self, trigger, ctx, replayer)


_BVE._fire_post_event = _wrap_fire


def wrap(self, m):
    if getattr(m, "stat", "") == stat:
        stack = traceback.extract_stack()[-3:-1]
        who = f"{getattr(self.self, 'name', None)}"
        print(f"[{stat}] trigger={_last_trigger['v']} self={who} target={m.target} v={m.value} mode={m.mode} "
              f"scope={m.scope} src={m.source} ttl={getattr(m, 'ttl', None)} "
              f"via={[f.name for f in stack]}", flush=True)
    return orig(self, m)


for _k, _v in list(R.JournalReplayer._DISPATCH.items()):
    if getattr(_k, "__name__", "") == "ModifierInjection":
        R.JournalReplayer._DISPATCH[_k] = wrap
R.JournalReplayer._apply_modifier = wrap

random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
print("done turn", battle.turn)
