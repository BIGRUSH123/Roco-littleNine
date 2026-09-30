"""Search must keep engine-side identities, even with asymmetric global state."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine.ai.core import mcts
from backend.engine.ai.core.encoder import _fill_global_entity
from backend.engine.ai.train import MCTSAgent
from backend.sim.globals import GlobalEffects
from backend.vm.effect import MarkEffect


def _globals():
    return GlobalEffects(mark_effects={
        "A": [MarkEffect(source="test", name="a+", stacks=2),
              MarkEffect(source="test", name="a-", stacks=3, category="negative")],
        "B": [MarkEffect(source="test", name="b+", stacks=5),
              MarkEffect(source="test", name="b-", stacks=7, category="negative")],
    })


def test_global_mark_observation_is_nonzero_and_changes_perspective():
    player = SimpleNamespace(lives=6, item=None, devotion={})
    battle = SimpleNamespace(globals=_globals(), turn=0)
    a, b = np.zeros(15), np.zeros(15)
    _fill_global_entity(battle, "A", player, player, a, np.zeros(1))
    _fill_global_entity(battle, "B", player, player, b, np.zeros(1))
    np.testing.assert_allclose(a[4:8], np.array([2, 3, 5, 7]) / 50)
    np.testing.assert_allclose(b[4:8], np.array([5, 7, 2, 3]) / 50)
    battle.globals.mark_effects.clear()
    _fill_global_entity(battle, "A", player, player, a, np.zeros(1))
    np.testing.assert_array_equal(a[4:8], 0)


class _Battle:
    def __init__(self, fail=False, terminal=True):
        self.player_a = SimpleNamespace(active=SimpleNamespace(is_fainted=False))
        self.player_b = SimpleNamespace(active=SimpleNamespace(is_fainted=False))
        self.globals = _globals()
        self.team_counters = {"A": {"n": 11}, "B": {"n": 29}}
        self.pending_effects = {"A": ["left"], "B": ["right"]}
        self.turn, self.winner = 0, None
        self._agent_a, self._agent_b = object(), object()
        self.fail, self.terminal = fail, terminal
        self.calls = []

    @property
    def is_finished(self):
        return self.winner is not None

    def save_mutable_state(self):
        return copy.deepcopy((self.turn, self.winner, self.globals.mark_effects,
                              self.team_counters, self.pending_effects))

    def restore_mutable_state(self, saved):
        (self.turn, self.winner, self.globals.mark_effects,
         self.team_counters, self.pending_effects) = copy.deepcopy(saved)

    def execute_turn_headless(self, agent_a, agent_b, fixed_action_a, fixed_action_b):
        assert agent_a.team == "A" and agent_b.team == "B"
        assert agent_a.player is self.player_a and agent_b.player is self.player_b
        assert self.team_counters == {"A": {"n": 11}, "B": {"n": 29}}
        assert self.pending_effects == {"A": ["left"], "B": ["right"]}
        assert self.globals.get_marks("A")[0][0].stacks == 2
        assert self.globals.get_marks("B")[0][0].stacks == 5
        self.calls.append((fixed_action_a.skill_index, fixed_action_b.skill_index))
        self.turn += 1
        self.team_counters["B"]["n"] += 1
        self.pending_effects["B"].append("simulated")
        self.globals.mark_effects["B"][0].stacks += 10
        self._agent_a, self._agent_b = agent_a, agent_b
        if self.fail:
            raise RuntimeError("simulated failure")
        if self.terminal:
            self.winner = "B" if fixed_action_b.skill_index == 1 else "A"


class _Evaluator:
    def evaluate(self, state, mask):
        return 0.0, mask / mask.sum()

    def evaluate_batch(self, states, masks):
        return np.zeros(len(states)), np.stack([self.evaluate(s, m)[1]
                                              for s, m in zip(states, masks, strict=True)])


def _stub_search_io(monkeypatch):
    def encode(battle, perspective="A"):
        return {"side": np.array([0 if perspective == "A" else 1])}
    def valid(player, battle):
        mask = np.zeros(mcts.NUM_ACTIONS, dtype=np.float32)
        mask[:2] = 1
        return [0, 1], mask
    monkeypatch.setattr(mcts, "encode_battle_state", encode)
    monkeypatch.setattr(mcts, "get_valid_actions", valid)
    monkeypatch.setattr(mcts, "battle_outcome_a", lambda b, *a, **kw:
                        (1.0 if b.winner == "A" else -1.0, "decisive"))


@pytest.mark.parametrize("leaf_batch_size", [1, 4])
def test_b_search_selects_b_win_and_preserves_asymmetric_engine_state(monkeypatch, leaf_batch_size):
    _stub_search_io(monkeypatch)
    battle = _Battle()
    before = battle.save_mutable_state()
    players = battle.player_a, battle.player_b
    agents = battle._agent_a, battle._agent_b
    evaluator = _Evaluator()
    probs = mcts.mcts_search(battle, None, None,
                            mcts.NetworkPolicyAgent(evaluator=evaluator),
                            evaluator=evaluator, perspective="B", root_noise=0,
                            num_simulations=32, opp_greedy=True, leaf_batch_size=leaf_batch_size)
    assert probs.argmax() == 1  # Terminal A value must be negated for B.
    assert (battle.player_a, battle.player_b) == players
    assert (battle._agent_a, battle._agent_b) == agents
    assert battle.save_mutable_state() == before
    assert battle.calls and {a for a, b in battle.calls} == {0}


@pytest.mark.parametrize("leaf_batch_size", [1, 4])
def test_b_search_restores_after_failed_simulation(monkeypatch, leaf_batch_size):
    _stub_search_io(monkeypatch)
    battle = _Battle(fail=True)
    before = battle.save_mutable_state()
    agents = battle._agent_a, battle._agent_b
    evaluator = _Evaluator()
    with pytest.raises(RuntimeError, match="simulated failure"):
        mcts.mcts_search(battle, None, None,
                         mcts.NetworkPolicyAgent(evaluator=evaluator),
                         evaluator=evaluator, perspective="B", root_noise=0,
                         num_simulations=4, leaf_batch_size=leaf_batch_size)
    assert battle.save_mutable_state() == before
    assert (battle._agent_a, battle._agent_b) == agents
    assert battle._mcts_sim is False


def test_b_agent_records_real_b_encoding_without_player_swap(monkeypatch):
    from backend.engine.ai import train
    battle = _Battle()
    original = battle.player_a, battle.player_b
    expected = {"marker": np.array([5, 7, 2, 3])}
    def encode(actual, perspective="A"):
        assert (actual.player_a, actual.player_b) == original
        assert perspective == "B"
        return expected
    def search(actual, *args, **kwargs):
        assert (actual.player_a, actual.player_b) == original
        assert kwargs["perspective"] == "B"
        probs = np.zeros(mcts.NUM_ACTIONS)
        probs[1] = 1
        return probs
    monkeypatch.setattr(train, "encode_battle_state", encode)
    monkeypatch.setattr(train, "mcts_search", search)
    monkeypatch.setattr(train, "get_valid_actions", lambda p, b:
                        ([1], np.eye(mcts.NUM_ACTIONS)[1]))
    agent = MCTSAgent("B", battle.player_b, None, None,
                      evaluator=_Evaluator(), record=True, temperature=0)
    assert agent.choose_action(battle).skill_index == 1
    assert agent.history[0][0] is expected
    assert (battle.player_a, battle.player_b) == original


@pytest.mark.parametrize("leaf_batch_size", [1, 4])
def test_b_leaf_network_value_already_has_b_perspective(monkeypatch, leaf_batch_size):
    _stub_search_io(monkeypatch)
    battle = _Battle(terminal=False)
    encoded_sides = []
    def encode(actual, perspective="A"):
        encoded_sides.append(perspective)
        return {"side": np.array([0 if perspective == "A" else 1])}
    monkeypatch.setattr(mcts, "encode_battle_state", encode)
    class Evaluator(_Evaluator):
        def evaluate(self, state, mask):
            return 0.25, mask / mask.sum()
        def evaluate_batch(self, states, masks):
            return np.full(len(states), 0.25), np.stack([
                self.evaluate(s, m)[1] for s, m in zip(states, masks, strict=True)])
    blended = []
    original_blend = mcts._blend_leaf_value
    def blend(net_value, extra_value, weight, scale):
        blended.append((net_value, extra_value))
        return original_blend(net_value, extra_value, weight, scale)
    monkeypatch.setattr(mcts, "_blend_leaf_value", blend)
    evaluator = Evaluator()
    before = battle.save_mutable_state()
    mcts.mcts_search(battle, None, None, mcts.NetworkPolicyAgent(evaluator=evaluator),
                     evaluator=evaluator, perspective="B", root_noise=0,
                     num_simulations=1, leaf_batch_size=leaf_batch_size,
                     leaf_value_fn=lambda b: 0.75, leaf_value_weight=0.5)
    assert blended == [(0.25, -0.75)]  # Only the A-based heuristic is negated.
    assert encoded_sides == ["B", "A", "B", "A"]
    assert battle.save_mutable_state() == before


@pytest.mark.parametrize("leaf_batch_size", [1, 4])
def test_real_battle_b_search_preserves_asymmetric_serialized_state(leaf_batch_size):
    """Exercise actual engine transitions, VM state and snapshot restoration."""
    import random

    from backend.engine.ai.core.encoder import encode_battle_state
    from backend.engine.serializer import battle_to_dict
    from backend.sim.factory import SimFactory
    from backend.vm.effect import StatBuffEffect

    rng = random.getstate()
    try:
        random.seed(81273)
        factory = SimFactory()
        pa = factory.build_player("A", [{"name": "水灵"}, {"name": "草衣虫"}])
        pb = factory.build_player("B", [{"name": "草衣虫"}, {"name": "水灵"}])
        battle = factory.build_battle(pa, pb)
        battle.globals = _globals()
        battle.globals.mark_effects["A"][1].speed_penalty = 3
        battle.globals.mark_effects["B"][0].energy_mod = 1
        battle.team_counters["A"]["search_regression"] = 11
        battle.team_counters["B"]["search_regression"] = 29
        battle.pending_effects = {
            "A": [StatBuffEffect(name="pending A", source="test", stat_key="atk", steps=2)],
            "B": [StatBuffEffect(name="pending B", source="test", stat_key="def", steps=5)],
        }
        before = copy.deepcopy(battle_to_dict(battle))
        encoded_b = encode_battle_state(battle, perspective="B")
        _, legal_b = mcts.get_valid_actions(pb, battle)
        evaluator = _Evaluator()
        agent = MCTSAgent("B", pb, factory, mcts.NetworkPolicyAgent(evaluator=evaluator),
                          evaluator=evaluator, record=True, temperature=0, root_noise=0,
                          opp_greedy=True, num_simulations=8, leaf_batch_size=leaf_batch_size)
        action = agent.choose_action(battle)
        assert battle.player_a is pa and battle.player_b is pb
        assert battle_to_dict(battle) == before
        state, policy, mask = agent.history[-1]
        for key, expected in encoded_b.items():
            np.testing.assert_array_equal(state[key], expected)
        np.testing.assert_array_equal(mask, legal_b)
        assert np.isfinite(policy).all() and np.isclose(policy.sum(), 1)
        assert np.all(policy[legal_b == 0] == 0)
        assert battle.action_legality("B", action).ok
    finally:
        random.setstate(rng)
