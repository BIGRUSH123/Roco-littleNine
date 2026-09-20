"""dbg_disp — 追踪 py 的显示效果创建（_sync_mult_display_effect / _sync_stat_buff_effect）。

用法：env\\python.exe native/tools/dbg_disp.py <spec> <turn> [stat片段]
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
tip = sys.argv[3] if len(sys.argv) > 3 else ""

orig_m = R.JournalReplayer._sync_mult_display_effect.__func__ if hasattr(
    R.JournalReplayer._sync_mult_display_effect, "__func__") else R.JournalReplayer._sync_mult_display_effect

orig = R.JournalReplayer._sync_mult_display_effect


def wrap_m(sprite, stat_key, mult_value, scope, source, display_value=None, additive=False):
    if tip in str(stat_key):
        callers = [f.name for f in traceback.extract_stack()[-4:-1]]
        print(f"[mult_disp] {sprite.name} stat={stat_key} mult={mult_value} scope={scope} "
              f"dv={display_value} additive={additive} via={callers}", flush=True)
    return orig(sprite, stat_key, mult_value, scope, source, display_value, additive)


R.JournalReplayer._sync_mult_display_effect = staticmethod(wrap_m)

orig_s = R.JournalReplayer._sync_stat_buff_effect


def wrap_s(sprite, stat_key, steps, scope, source, mode="add", is_inherent=False):
    if tip in str(stat_key):
        callers = [f.name for f in traceback.extract_stack()[-4:-1]]
        print(f"[stat_buff] {sprite.name} stat={stat_key} steps={steps} scope={scope} mode={mode} "
              f"via={callers}", flush=True)
    return orig_s(sprite, stat_key, steps, scope, source, mode=mode, is_inherent=is_inherent)


R.JournalReplayer._sync_stat_buff_effect = staticmethod(wrap_s)

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
