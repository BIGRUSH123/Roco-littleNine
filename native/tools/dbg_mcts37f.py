"""dbg_mcts37f - print py charge-gate decision for spec 0037 A skill 1 (吓退)."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

from backend.engine.ai.core.mcts import (  # noqa: E402
    action_index_to_action, get_valid_actions, policy_select_idx,
)
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.battle import Battle  # noqa: E402

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0037.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
np.random.seed((spec["seed"] + 1) % (2**32 - 1))
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

orig_gate = Battle._gate_charge_vm


def gate_patched(self, user, bs, action, record=None):
    r = orig_gate(self, user, bs, action, record)
    has = None
    try:
        rec = record or self._get_skill_record(bs.base.name)
        has = self._skill_has_charge(rec)
    except Exception as exc:  # noqa: BLE001
        has = f"err:{exc}"
    print(f"  [gate] {bs.name} has_charge={has} -> {r}")
    return r


Battle._gate_charge_vm = gate_patched

va, ma = get_valid_actions(battle.player_a, battle)
vb, mb = get_valid_actions(battle.player_b, battle)
opp_idx = policy_select_idx(mb / max(mb.sum(), 1.0), 0.0, True)
action_a = action_index_to_action(battle.player_a, 1)
action_b = action_index_to_action(battle.player_b, opp_idx)

sa = battle.player_a.active.skills[1]
print("skillA:", sa.name, "base.usable_while_charging=", sa.base.usable_while_charging)
rec = battle._get_skill_record(sa.base.name)
print("py record effects:", json.dumps(rec.effects if hasattr(rec, "effects") else rec, ensure_ascii=False)[:600])

spec_skill = None
for ss in spec["players"][0]["sprites"][0]["skills"]:
    if ss["name"] == sa.name:
        spec_skill = ss
print("spec skill keys:", list(spec_skill.keys()) if spec_skill else None)
print("spec effects:", json.dumps(spec_skill.get("effects"), ensure_ascii=False)[:600])

battle._mcts_sim = True
try:
    battle.execute_turn_headless(a, b, fixed_action_a=action_a, fixed_action_b=action_b)
finally:
    battle._mcts_sim = False
print("after: A first_action=", battle.player_a.active.first_action,
      "energy=", battle.player_a.active.energy,
      "charging=", getattr(battle.player_a.active, "_charging", None))
