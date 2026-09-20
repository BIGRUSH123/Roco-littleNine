"""dbg_mcts37h - log py counter-effect injection (execute_effects) for spec 0037."""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

from backend.engine.ai.core.mcts import (  # noqa: E402
    action_index_to_action, get_valid_actions, policy_select_idx,
)
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0037.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
np.random.seed((spec["seed"] + 1) % (2**32 - 1))
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

rec = battle._get_skill_record("精神扰乱")
effs = battle._vm_engine._get_effects(rec)
print("compiled effects of 精神扰乱:")
for e in effs:
    print("   type=", type(e).__name__, "when=", getattr(e, "when", "<none>"),
          "dict?", isinstance(e, dict), repr(e)[:200])

vm = battle._vm_engine
orig_ee = vm.execute_effects


def ee_patched(ctx, effects):
    print(f"[execute_effects] ctx.team={getattr(ctx, 'team', '?')} "
          f"self={getattr(getattr(ctx, 'self_sprite', None), 'name', '?')} "
          f"opp={getattr(getattr(ctx, 'opp_sprite', None), 'name', '?')} n_eff={len(effects)}")
    for e in effects:
        print("    eff:", type(e).__name__, repr(e)[:220])
    out = orig_ee(ctx, effects)
    print("    journal:", [repr(m)[:160] for m in (out or [])])
    return out


vm.execute_effects = ee_patched

va, ma = get_valid_actions(battle.player_a, battle)
vb, mb = get_valid_actions(battle.player_b, battle)
opp_idx = policy_select_idx(mb / max(mb.sum(), 1.0), 0.0, True)
action_a = action_index_to_action(battle.player_a, 1)
action_b = action_index_to_action(battle.player_b, opp_idx)
print("actions A:", action_a, "B:", action_b)
battle._mcts_sim = True
try:
    battle.execute_turn_headless(a, b, fixed_action_a=action_a, fixed_action_b=action_b)
finally:
    battle._mcts_sim = False
print("final A0 energy_cost:", battle.player_a.active._modifiers.get("energy_cost"))
print("final B0 energy_cost:", battle.player_b.active._modifiers.get("energy_cost"))
