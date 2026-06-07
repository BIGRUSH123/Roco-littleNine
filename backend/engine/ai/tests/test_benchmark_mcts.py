from __future__ import annotations

import random

import numpy as np

from backend.engine.ai.benchmark_mcts import _fixed_battle, run_benchmark
from backend.engine.ai.core.encoder import encode_battle_state
from backend.sim.action import Action
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory


def _assert_encoded_equal(left: dict[str, np.ndarray], right: dict[str, np.ndarray]) -> None:
    assert left.keys() == right.keys()
    for key in left:
        np.testing.assert_allclose(left[key], right[key])


def test_mcts_benchmark_smoke():
    result = run_benchmark(
        encode_iters=1,
        eval_iters=1,
        mcts_repeats=1,
        simulations=1,
        device="cpu",
        seed=1,
    )

    assert result["encode"]["iters"] == 1
    assert result["evaluate"]["iters"] == 1
    assert result["mcts"]["repeats"] == 1
    assert result["mcts"]["simulations"] == 1
    assert result["mcts"]["simulations_per_sec"] > 0


def test_mcts_sim_turn_does_not_append_battle_log():
    battle = _fixed_battle(SimFactory())
    battle._mcts_sim = True
    agent_a = RuleAgent("A", battle.player_a)
    agent_b = RuleAgent("B", battle.player_b)

    rec = battle.execute_turn(agent_a, agent_b)

    assert rec.turn == 1
    assert battle.turn == 1
    assert battle.log == []


def test_mcts_sim_fixed_actions_match_regular_state():
    normal = _fixed_battle(SimFactory())
    headless = _fixed_battle(SimFactory())
    action_a = Action("gather")
    action_b = Action("gather")

    random.seed(7)
    normal.execute_turn(
        RuleAgent("A", normal.player_a),
        RuleAgent("B", normal.player_b),
        fixed_action_a=action_a,
        fixed_action_b=action_b,
    )

    random.seed(7)
    headless._mcts_sim = True
    headless.execute_turn(
        RuleAgent("A", headless.player_a),
        RuleAgent("B", headless.player_b),
        fixed_action_a=action_a,
        fixed_action_b=action_b,
    )

    assert len(normal.log) == 1
    assert headless.log == []
    assert headless.turn == normal.turn
    assert headless.winner == normal.winner
    _assert_encoded_equal(
        encode_battle_state(headless, perspective="A"),
        encode_battle_state(normal, perspective="A"),
    )
    _assert_encoded_equal(
        encode_battle_state(headless, perspective="B"),
        encode_battle_state(normal, perspective="B"),
    )
