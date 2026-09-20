"""dbg_mcts37g - log every py modifier write during spec 0037 fixed-action step."""

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


class LogDict(dict):
    def __init__(self, label, base):
        super().__init__(base)
        self._label = label

    def __setitem__(self, k, v):
        print(f"  [py set] {self._label}[{k!r}] = {v!r}")
        super().__setitem__(k, v)

    def pop(self, k, *d):
        print(f"  [py pop] {self._label}[{k!r}]")
        return super().pop(k, *d)

    def __delitem__(self, k):
        print(f"  [py del] {self._label}[{k!r}]")
        super().__delitem__(k)


for team, tag in ((battle.player_a, "A"), (battle.player_b, "B")):
    for i, s in enumerate(team.team):
        s._modifiers = LogDict(f"{tag}{i}.{s.name}", s._modifiers)
        for j, sk in enumerate(s.skills or []):
            sk._modifiers = LogDict(f"{tag}{i}.skill{j}.{sk.name}", sk._modifiers)

va, ma = get_valid_actions(battle.player_a, battle)
vb, mb = get_valid_actions(battle.player_b, battle)
opp_idx = policy_select_idx(mb / max(mb.sum(), 1.0), 0.0, True)
action_a = action_index_to_action(battle.player_a, 1)
action_b = action_index_to_action(battle.player_b, opp_idx)
print("actions A:", action_a, "B:", action_b)
print("A0 delta_mult:", battle.player_a.active._modifiers.get("energy_cost_delta_mult"))
print("B0 delta_mult:", battle.player_b.active._modifiers.get("energy_cost_delta_mult"))
battle._mcts_sim = True
try:
    battle.execute_turn_headless(a, b, fixed_action_a=action_a, fixed_action_b=action_b)
finally:
    battle._mcts_sim = False
print("final A0 mods:", dict(battle.player_a.active._modifiers))
print("final B0 mods:", dict(battle.player_b.active._modifiers))
