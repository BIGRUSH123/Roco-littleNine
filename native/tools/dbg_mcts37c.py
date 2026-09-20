"""dbg_mcts37c — 打印 py 固定动作步进中 resolve 的决策（counter/执行）。"""

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
from backend.sim.battle import Battle  # noqa: E402
from backend.sim.resolver import SkillResolver  # noqa: E402

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0037.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
np.random.seed((spec["seed"] + 1) % (2**32 - 1))
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

orig_exec = Battle._execute_skill_vm
orig_counter = SkillResolver.resolve_counter


def exec_patched(self, team, action, is_countered=False, countered_skill=None,
                 countering_skill=None, is_first=False, opponent_switched=False, opp_skill=None):
    out = orig_exec(self, team, action, is_countered, countered_skill, countering_skill,
                    is_first, opponent_switched, opp_skill)
    print(f"  [exec] team={team} action={action} countered={is_countered} "
          f"countering={countering_skill.name if countering_skill else None} "
          f"first={is_first} events={len(out)}")
    return out


def counter_patched(atk, dfn):
    r = orig_counter(atk, dfn)
    print(f"  [counter] {getattr(atk, 'name', atk)} vs {getattr(dfn, 'name', dfn)} -> {r}")
    return r


Battle._execute_skill_vm = exec_patched
SkillResolver.resolve_counter = staticmethod(counter_patched)

va, ma = get_valid_actions(battle.player_a, battle)
vb, mb = get_valid_actions(battle.player_b, battle)
opp_idx = policy_select_idx(mb / max(mb.sum(), 1.0), 0.0, True)
action_a = action_index_to_action(battle.player_a, 1)
action_b = action_index_to_action(battle.player_b, opp_idx)
print("actions A:", action_a, "B:", action_b)
print("skillA:", battle.player_a.active.skills[1].name,
      "is_attack=", battle.player_a.active.skills[1].is_attack,
      "| skillB:", battle.player_b.active.skills[opp_idx].name,
      "is_attack=", battle.player_b.active.skills[opp_idx].is_attack)
print("B0 counters:", battle.player_b.active.skills[opp_idx].counters)
print("A1 counters:", battle.player_a.active.skills[1].counters)
battle._mcts_sim = True
try:
    battle.execute_turn_headless(a, b, fixed_action_a=action_a, fixed_action_b=action_b)
finally:
    battle._mcts_sim = False
d = gate_digest(battle)
print("post A:", d["players"][0]["sprites"][0])
print("post B:", d["players"][1]["sprites"][0])
print("tc:", d["team_counters"])
