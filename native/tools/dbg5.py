"""临时调试3：打印 abnormal 变更的完整链路。"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import backend.engine.replayer as rp  # noqa: E402
import backend.vm.executor as vmex  # noqa: E402

orig_sync = rp.JournalReplayer._sync_abnormal_effect


def patched_sync(sprite, name, delta, scope):
    print(f"[sync abnormal] name={name} delta={delta} scope={scope!r}")
    return orig_sync(sprite, name, delta, scope)


rp.JournalReplayer._sync_abnormal_effect = staticmethod(patched_sync)

orig_op = vmex.op_abnormal


def patched_op(ctx, effect):
    result = orig_op(ctx, effect)
    print(f"[op_abnormal] effect_type={type(effect).__name__} op={effect!r}")
    print(f"[op_abnormal] result={result}")
    return result


vmex.op_abnormal = patched_op

orig_apply = rp.JournalReplayer._apply_abnormal_change


def patched_apply(self, m):
    import traceback
    print(f"[apply_abnormal_change] m={m!r}")
    for fr in traceback.extract_stack()[-8:-1]:
        print("   ", fr.filename.split("backend")[-1], fr.lineno, fr.name)
    return orig_apply(self, m)


rp.JournalReplayer._apply_abnormal_change = patched_apply
rp.JournalReplayer._DISPATCH[rp.AbnormalChange] = patched_apply

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while battle.turn < 5 and not battle.is_finished:
    battle.execute_turn(a, b)
