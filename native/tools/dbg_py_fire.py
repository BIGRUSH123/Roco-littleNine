"""dbg_py_fire — 打印 post_entry 触发时各观察者的 then 与产出。

用法：env\\python.exe native/tools/dbg_py_fire.py <spec_id> <turn> [trigger]
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
trig = sys.argv[3] if len(sys.argv) > 3 else "post_entry"
turn = int(turn_s)

spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

from backend.engine.battle import BattleVMEngine  # noqa: E402

orig = BattleVMEngine._fire_post_event


def patched(self, trigger, ctx, replayer):
    if battle.turn == turn and trigger == trig:
        own = id(replayer.self) if replayer.self else None
        cands = self.registry.candidates_for_owner(trigger, own)
        for obs in cands:
            src = getattr(obs, "source", "")
            if "蓄" in str(src) or "蓄电池" in str(getattr(obs, "name", "")):
                print(f"t{battle.turn} obs src={src!r} then_len={len(obs.then)}")
                from backend.vm.executor import process_effects
                jr = process_effects(ctx, obs.then)
                print(f"    journal_len={len(jr)}")
                for mm in jr:
                    print("    mut:", type(mm).__name__, mm)
    return orig(self, trigger, ctx, replayer)


BattleVMEngine._fire_post_event = patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
