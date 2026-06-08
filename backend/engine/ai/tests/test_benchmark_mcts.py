from __future__ import annotations

import random

import numpy as np

from backend.engine.ai.benchmark_mcts import _fixed_battle, run_benchmark
from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.mcts import (
    NetworkPolicyAgent,
    _step_battle,
    get_valid_actions,
    mcts_search,
    policy_select_idx,
)
from backend.sim.action import Action
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory


def _assert_encoded_equal(left: dict[str, np.ndarray], right: dict[str, np.ndarray]) -> None:
    assert left.keys() == right.keys()
    for key in left:
        np.testing.assert_allclose(left[key], right[key])


class _UniformEvaluator:
    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        return 0.0, self._probs(mask)

    def evaluate_batch(
        self,
        states: list[dict],
        masks: list[np.ndarray] | np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        mask_list = masks if isinstance(masks, list) else list(masks)
        return (
            np.zeros(len(states), dtype=np.float32),
            np.stack([self._probs(mask) for mask in mask_list], axis=0),
        )

    @staticmethod
    def _probs(mask: np.ndarray) -> np.ndarray:
        probs = mask.astype(np.float32).copy()
        total = probs.sum()
        return probs / total if total > 0 else probs


class _CountingOpponent:
    team = "B"

    def __init__(self) -> None:
        self.choose_action_calls = 0

    def choose_action(self, battle):
        self.choose_action_calls += 1
        return Action("gather")

    def choose_lead(self, battle) -> int:
        return 0

    def choose_replacement(self, battle) -> int:
        return -1

    def on_game_end(self, winner: str) -> None:
        pass


class _EvaluateOnlyEvaluator:
    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        return 0.0, _UniformEvaluator._probs(mask)


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


def test_get_valid_actions_matches_mask_nonzero_indices():
    battle = _fixed_battle(SimFactory())

    valid, mask = get_valid_actions(battle.player_a, battle)

    assert valid == np.flatnonzero(mask > 0).tolist()


def test_policy_select_idx_handles_empty_and_greedy_probs():
    assert policy_select_idx(np.zeros(17, dtype=np.float32), temperature=1.0) == -1
    assert policy_select_idx(np.zeros(17, dtype=np.float32), temperature=0.0) == -1
    assert policy_select_idx(
        np.array([0.1, 0.7, 0.2], dtype=np.float32),
        temperature=1.0,
        greedy=True,
    ) == 1


def test_policy_select_idx_samples_by_cumulative_weight(monkeypatch):
    probs = np.array([0.2, 0.3, 0.5], dtype=np.float32)
    monkeypatch.setattr(np.random, "random", lambda: 0.6)

    assert policy_select_idx(probs, temperature=1.0) == 2


def test_policy_select_idx_temperature_path_samples_valid_weight(monkeypatch):
    probs = np.array([0.25, 0.75, 0.0], dtype=np.float32)
    monkeypatch.setattr(np.random, "random", lambda: 0.01)

    assert policy_select_idx(probs, temperature=0.5) == 0


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


def test_mcts_uses_non_network_opponent_agent_actions():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    opponent = _CountingOpponent()

    mcts_search(
        battle,
        None,
        factory,
        opponent,
        num_simulations=1,
        root_noise=0.0,
        max_turns=4,
        evaluator=_UniformEvaluator(),
    )

    assert opponent.choose_action_calls > 0


def test_mcts_non_network_opponent_does_not_require_batch_evaluator():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    opponent = _CountingOpponent()

    policy = mcts_search(
        battle,
        None,
        factory,
        opponent,
        num_simulations=2,
        root_noise=0.0,
        max_turns=4,
        evaluator=_EvaluateOnlyEvaluator(),
    )

    assert opponent.choose_action_calls > 0
    assert policy.shape == (17,)
    np.testing.assert_allclose(policy.sum(), 1.0)


def test_network_policy_step_uses_player_a_replacement_proxy(monkeypatch):
    factory = SimFactory()
    battle = _fixed_battle(factory)
    opponent = NetworkPolicyAgent(evaluator=_UniformEvaluator(), greedy=True)
    seen = {}

    def fake_execute_turn(agent_a, agent_b, *, fixed_action_a=None, fixed_action_b=None):
        seen["agent_a_team"] = agent_a.team
        seen["agent_a_replacement"] = agent_a.choose_replacement(battle)
        seen["agent_b_team"] = agent_b.team
        seen["fixed_action_a"] = fixed_action_a
        seen["fixed_action_b"] = fixed_action_b

    monkeypatch.setattr(battle, "execute_turn", fake_execute_turn)

    ok = _step_battle(
        battle,
        15,
        opponent,
        opp_policy=np.eye(1, 17, 15, dtype=np.float32)[0],
        opp_greedy=True,
    )

    assert ok
    assert seen["agent_a_team"] == "A"
    assert seen["agent_a_replacement"] == 1
    assert seen["agent_b_team"] == "B"
    assert seen["fixed_action_a"].kind == "gather"
    assert seen["fixed_action_b"].kind == "gather"
