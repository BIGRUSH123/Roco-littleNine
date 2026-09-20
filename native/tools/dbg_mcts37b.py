"""dbg_mcts37b — 手工固定动作步进 vs 桩搜索结果：确认 py 固定动作路径是否结算。"""

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
from backend.engine.test_rust_gate import battle_from_spec, gate_digest  # noqa: E402
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


def brief(tag: str) -> None:
    d = gate_digest(battle)
    s0 = d["players"][0]["sprites"][0]
    print(f"{tag}: turn={d['turn']} energy={s0['energy']} first_action={s0['first_action']} "
          f"effects={s0['effects']} mods={s0['modifiers']} cd={[sk['cooldown'] for sk in s0['skills']]} "
          f"tcA={d['team_counters']['A']} tcB={d['team_counters']['B']}")


brief("pre     ")

va, ma = get_valid_actions(battle.player_a, battle)
vb, mb = get_valid_actions(battle.player_b, battle)
probs_b = mb / max(mb.sum(), 1.0)
opp_idx = policy_select_idx(probs_b, 0.0, True)
print("A valid", va, "B valid", vb, "opp_idx", opp_idx)

action_a = action_index_to_action(battle.player_a, 1)
action_b = action_index_to_action(battle.player_b, opp_idx)
print("actions", action_a, action_b)

battle._mcts_sim = True
try:
    battle.execute_turn_headless(a, b, fixed_action_a=action_a, fixed_action_b=action_b)
finally:
    battle._mcts_sim = False
brief("post    ")
