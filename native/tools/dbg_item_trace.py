"""dbg_item_trace — 追踪 py 自对弈前几回合的愿力结算与 slot0 技能状态。"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.mcts import NetworkPolicyAgent  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.engine.ai.train import MCTSAgent  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
seed = spec["seed"] + 1
random.seed(seed)
np.random.seed(seed)

battle = battle_from_spec(spec)
ev = TorchEvaluator(ModularBattleNet.load("checkpoints/exp16/model_rl.pt", device="cpu"), device="cpu")
opp = NetworkPolicyAgent(evaluator=ev, greedy=True)
a = MCTSAgent("A", battle.player_a, SimFactory(), opp, 12, 1.0, root_noise=0.25, record=True,
              evaluator=ev, max_turns=60, draw_margin=0.15, gamma=1.0, tanh_k=0.0,
              leaf_batch_size=16, opp_greedy=True)
b = MCTSAgent("B", battle.player_b, SimFactory(), opp, 12, 1.0, root_noise=0.25, record=True,
              evaluator=ev, max_turns=60, draw_margin=0.15, gamma=1.0, tanh_k=0.0,
              leaf_batch_size=16, opp_greedy=True)

turn = 0
while not battle.is_finished and turn < 60:
    battle.execute_turn(a, b)
    turn += 1
    s0 = battle.player_a.active
    sk = s0.skills[0]
    wish = getattr(battle, "_wish_restore", {})
    wish_info = {k: v.name for k, v in list(wish.items())[:2]}
    print(f"turn{turn}: A active={s0.name} slot0={sk.name} power={sk.power} "
          f"energy={sk.energy_cost} item.uses="
          f"{battle.player_a.item.uses if battle.player_a.item else None} "
          f"wish_restore={wish_info}")
    if turn >= 3:
        break
