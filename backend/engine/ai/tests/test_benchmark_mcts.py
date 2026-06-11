from __future__ import annotations

import random

import numpy as np
import torch

import backend.engine.ai.core.mcts as mcts_module
from backend.engine.ai.benchmark_mcts import _fixed_battle, run_benchmark
from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.mcts import (
    MCTSNode,
    NetworkPolicyAgent,
    NUM_ACTIONS,
    _step_battle,
    action_index_to_action,
    get_valid_actions,
    mcts_search,
    policy_select_idx,
)
from backend.engine.ai.train import _value_classes
from backend.sim.action import Action
from backend.sim.agent import RuleAgent
from backend.sim.battleskill import BattleSkill
from backend.sim.player import Player
from backend.sim.factory import SimFactory
from backend.sim.skill import Skill
from backend.sim.sprite import Sprite
from backend.common.models import SpeciesStats


def _assert_encoded_equal(left: dict[str, np.ndarray], right: dict[str, np.ndarray]) -> None:
    assert left.keys() == right.keys()
    for key in left:
        np.testing.assert_allclose(left[key], right[key])


class _UniformEvaluator:
    def __init__(self) -> None:
        self.evaluate_calls = 0
        self.evaluate_batch_calls = 0

    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        self.evaluate_calls += 1
        return 0.0, self._probs(mask)

    def evaluate_batch(
        self,
        states: list[dict],
        masks: list[np.ndarray] | np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        self.evaluate_batch_calls += 1
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


class _MarkedEvaluator(_UniformEvaluator):
    def __init__(self, mark: float) -> None:
        super().__init__()
        self.mark = mark

    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        value, probs = super().evaluate(state, mask)
        return self.mark, probs

    def evaluate_batch(
        self,
        states: list[dict],
        masks: list[np.ndarray] | np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        _, probs = super().evaluate_batch(states, masks)
        return np.full(len(states), self.mark, dtype=np.float32), probs


class _SwitchBiasedEvaluator(_UniformEvaluator):
    def __init__(self, action_idx: int) -> None:
        super().__init__()
        self.action_idx = action_idx

    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        self.evaluate_calls += 1
        probs = np.zeros(NUM_ACTIONS, dtype=np.float32)
        if mask[self.action_idx] > 0:
            probs[self.action_idx] = 1.0
        else:
            probs = self._probs(mask)
        return 0.0, probs


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


def _make_charge_mask_battle():
    from backend.sim.battle import Battle

    species = SpeciesStats(name="蓄力测试", hp=100, atk=80, def_=80, sp_atk=80, sp_def=80, speed=80)
    active = Sprite(species=species, current_hp=100, max_hp=100, energy=10)
    active.skills = [
        BattleSkill(base=Skill(name="龙吟", energy_cost=0)),
        BattleSkill(base=Skill(name="猛烈撞击", energy_cost=0)),
    ]
    bench = Sprite(species=species, current_hp=100, max_hp=100, energy=10)
    bench.skills = [BattleSkill(base=Skill(name="猛烈撞击", energy_cost=0))]
    opp = Sprite(species=species, current_hp=100, max_hp=100, energy=10)
    opp.skills = [BattleSkill(base=Skill(name="猛烈撞击", energy_cost=0))]
    return Battle(Player("A", [active, bench]), Player("B", [opp]), verbose=False), active


def _make_replacement_battle():
    from backend.sim.battle import Battle

    def make_sprite(name: str) -> Sprite:
        species = SpeciesStats(name=name, hp=100, atk=80, def_=80, sp_atk=80, sp_def=80, speed=80)
        sprite = Sprite(species=species, current_hp=100, max_hp=100, energy=10)
        sprite.skills = [BattleSkill(base=Skill(name="猛烈撞击", energy_cost=0))]
        return sprite

    team_a = [make_sprite("A0"), make_sprite("A1"), make_sprite("A2")]
    team_b = [make_sprite("B0"), make_sprite("B1"), make_sprite("B2")]
    return Battle(Player("A", team_a), Player("B", team_b), verbose=False)


def test_valid_actions_charge_tracks_skill_ref_after_transmission():
    battle, active = _make_charge_mask_battle()
    charged = active.skills[0]
    battle._set_charge_target(active, charged, 0)
    active.skills = [active.skills[1], active.skills[0]]

    valid, mask = get_valid_actions(battle.player_a, battle)

    assert mask[0] == 0.0
    assert mask[1] == 1.0
    assert 1 in valid


def test_valid_actions_charge_disabled_skill_only_allows_switch():
    battle, active = _make_charge_mask_battle()
    charged = active.skills[0]
    battle._set_charge_target(active, charged, 0)
    charged.sealed = True

    valid, mask = get_valid_actions(battle.player_a, battle)

    assert mask[:10].sum() == 0.0
    assert mask[10] == 1.0
    assert 10 in valid


class _EvaluateOnlyEvaluator:
    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        return 0.0, _UniformEvaluator._probs(mask)


class _InvalidBiasedEvaluator:
    def evaluate(self, state: dict, mask: np.ndarray) -> tuple[float, np.ndarray]:
        probs = np.zeros(NUM_ACTIONS, dtype=np.float32)
        probs[0] = 1.0
        invalid = np.flatnonzero(mask <= 0)
        if len(invalid):
            probs[int(invalid[-1])] = 100.0
        total = probs.sum()
        return 0.0, probs / total


def test_mcts_node_children_are_action_index_aligned():
    prior = np.zeros(NUM_ACTIONS, dtype=np.float32)
    root = MCTSNode([2, 14], prior)
    child = MCTSNode([], prior)

    root.children[14] = child

    assert len(root.children) == NUM_ACTIONS
    assert root.children[14] is child
    assert root.children[13] is None
    assert root.children[0] is None
    assert root.has_children


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


def test_get_valid_actions_uses_weather_energy_discount():
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "巨灵石", "skills": ["地震", "防御"]}])
    p2 = factory.build_player("B", [{"name": "果冻", "skills": ["防御"]}])
    battle = factory.build_battle(p1, p2)
    p1.active.energy = 5
    battle.globals.set_weather("sand")

    valid, mask = get_valid_actions(p1, battle)

    assert mask[0] == 1.0
    assert 0 in valid


def test_get_valid_actions_allows_blood_price_energy_substitution():
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "巨灵石", "skills": ["地震", "防御"]}])
    p2 = factory.build_player("B", [{"name": "果冻", "skills": ["防御"]}])
    battle = factory.build_battle(p1, p2)
    p1.active.energy = 5
    p1.active._modifiers["blood_price"] = 0.01

    valid, mask = get_valid_actions(p1, battle)

    assert mask[0] == 1.0
    assert 0 in valid


def test_action_index_to_action_uses_fixed_bench_slot_mapping():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    player = battle.player_a

    player.active_index = 0
    assert action_index_to_action(player, 10).switch_index == 1

    player.active_index = 1
    assert action_index_to_action(player, 10).switch_index == 0


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


def test_execute_turn_headless_fixed_actions_match_regular_state():
    normal = _fixed_battle(SimFactory())
    headless = _fixed_battle(SimFactory())
    action_a = Action("gather")
    action_b = Action("gather")

    random.seed(11)
    normal.execute_turn(
        RuleAgent("A", normal.player_a),
        RuleAgent("B", normal.player_b),
        fixed_action_a=action_a,
        fixed_action_b=action_b,
    )

    random.seed(11)
    result = headless.execute_turn_headless(
        RuleAgent("A", headless.player_a),
        RuleAgent("B", headless.player_b),
        fixed_action_a=action_a,
        fixed_action_b=action_b,
    )

    assert result is None
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


def test_mutable_state_restores_full_skill_runtime_state():
    battle = _fixed_battle(SimFactory())
    sprite = battle.player_a.active
    skill = sprite.skills[0]
    replacement = Skill(name="replacement", skill_type="status")

    skill._modifiers["power"] = 2
    skill.cooldown = 1
    skill.sealed = True
    skill.replaced_by = replacement
    skill.next_attack_mult = 1.5
    skill.nullified = True
    skill.is_temporary = True
    skill._transmission = 3
    skill._element_override = "fire"
    skill._mech_energy_reduction = 1
    skill._burst_effects.append({"kind": "damage", "value": 1})

    saved = battle.save_mutable_state()

    sprite.skills = []
    skill._modifiers.clear()
    skill.cooldown = 0
    skill.sealed = False
    skill.replaced_by = None
    skill.next_attack_mult = 1.0
    skill.nullified = False
    skill.is_temporary = False
    skill._transmission = 0
    skill._element_override = ""
    skill._mech_energy_reduction = 0
    skill._burst_effects.clear()

    battle.restore_mutable_state(saved)

    restored = battle.player_a.active.skills[0]
    assert restored is skill
    assert restored._modifiers == {"power": 2}
    assert restored.cooldown == 1
    assert restored.sealed is True
    assert restored.replaced_by is replacement
    assert restored.next_attack_mult == 1.5
    assert restored.nullified is True
    assert restored.is_temporary is True
    assert restored._transmission == 3
    assert restored._element_override == "fire"
    assert restored._mech_energy_reduction == 1
    assert restored._burst_effects == [{"kind": "damage", "value": 1}]


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


def test_mcts_ignores_invalid_action_priors():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    valid, mask = get_valid_actions(battle.player_a, battle)

    policy = mcts_search(
        battle,
        None,
        factory,
        _CountingOpponent(),
        num_simulations=3,
        root_noise=0.0,
        max_turns=4,
        evaluator=_InvalidBiasedEvaluator(),
    )

    invalid = np.flatnonzero(mask <= 0)
    assert valid
    assert policy.shape == (NUM_ACTIONS,)
    assert np.all(policy[invalid] == 0.0)
    np.testing.assert_allclose(policy.sum(), 1.0)


def test_mcts_leaf_batch_path_uses_batch_evaluator():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    opponent = _CountingOpponent()
    evaluator = _UniformEvaluator()

    policy = mcts_search(
        battle,
        None,
        factory,
        opponent,
        num_simulations=4,
        root_noise=0.0,
        max_turns=4,
        evaluator=evaluator,
        leaf_batch_size=4,
    )

    assert evaluator.evaluate_batch_calls > 0
    assert policy.shape == (17,)
    np.testing.assert_allclose(policy.sum(), 1.0)


def test_mcts_leaf_batch_default_uses_serial_evaluator():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    opponent = _CountingOpponent()
    evaluator = _UniformEvaluator()

    policy = mcts_search(
        battle,
        None,
        factory,
        opponent,
        num_simulations=4,
        root_noise=0.0,
        max_turns=4,
        evaluator=evaluator,
        leaf_batch_size=1,
    )

    assert evaluator.evaluate_batch_calls == 0
    assert policy.shape == (17,)
    np.testing.assert_allclose(policy.sum(), 1.0)


def test_network_opponent_policy_uses_opponent_evaluator_on_serial_path():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    own_eval = _MarkedEvaluator(0.25)
    opp_eval = _MarkedEvaluator(-0.75)
    opponent = NetworkPolicyAgent(evaluator=opp_eval, greedy=True)

    policy = mcts_search(
        battle,
        None,
        factory,
        opponent,
        num_simulations=2,
        root_noise=0.0,
        max_turns=4,
        evaluator=own_eval,
        leaf_batch_size=1,
    )

    assert own_eval.evaluate_calls > 0
    assert own_eval.evaluate_batch_calls == 0
    assert opp_eval.evaluate_calls > 0
    assert opp_eval.evaluate_batch_calls == 0
    assert policy.shape == (17,)
    np.testing.assert_allclose(policy.sum(), 1.0)


def test_network_opponent_leaf_batch_uses_both_batch_evaluators():
    factory = SimFactory()
    battle = _fixed_battle(factory)
    own_eval = _MarkedEvaluator(0.25)
    opp_eval = _MarkedEvaluator(-0.75)
    opponent = NetworkPolicyAgent(evaluator=opp_eval, greedy=True)

    policy = mcts_search(
        battle,
        None,
        factory,
        opponent,
        num_simulations=4,
        root_noise=0.0,
        max_turns=4,
        evaluator=own_eval,
        leaf_batch_size=4,
    )

    assert own_eval.evaluate_batch_calls > 0
    assert opp_eval.evaluate_batch_calls > 0
    assert policy.shape == (17,)
    np.testing.assert_allclose(policy.sum(), 1.0)


def test_network_policy_step_uses_player_a_replacement_proxy(monkeypatch):
    battle = _make_replacement_battle()
    opponent = NetworkPolicyAgent(evaluator=_SwitchBiasedEvaluator(11), greedy=True)
    seen = {}

    def fake_execute_turn_headless(agent_a, agent_b, *, fixed_action_a=None, fixed_action_b=None):
        seen["agent_a_team"] = agent_a.team
        seen["agent_a_replacement"] = agent_a.choose_replacement(battle)
        seen["agent_b_team"] = agent_b.team
        seen["agent_b_replacement"] = agent_b.choose_replacement(battle)
        seen["fixed_action_a"] = fixed_action_a
        seen["fixed_action_b"] = fixed_action_b

    def fake_execute_turn(*args, **kwargs):
        raise AssertionError("_step_battle should use execute_turn_headless")

    monkeypatch.setattr(battle, "execute_turn_headless", fake_execute_turn_headless, raising=False)
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
    assert seen["agent_a_replacement"] == 2
    assert seen["agent_b_team"] == "B"
    assert seen["agent_b_replacement"] == 2
    assert seen["fixed_action_a"].kind == "gather"
    assert seen["fixed_action_b"].kind == "gather"


def test_mcts_terminal_uses_configured_draw_margin(monkeypatch):
    factory = SimFactory()
    battle = _fixed_battle(factory)
    seen = {}

    def fake_outcome(battle_arg, max_turns, *, draw_margin, gamma=1.0, tanh_k=0.0):
        seen["draw_margin"] = draw_margin
        return 0.0, "draw"

    monkeypatch.setattr(mcts_module, "battle_outcome_a", fake_outcome)

    mcts_module.mcts_search(
        battle,
        None,
        factory,
        _CountingOpponent(),
        num_simulations=1,
        root_noise=0.0,
        max_turns=0,
        draw_margin=0.42,
        evaluator=_UniformEvaluator(),
    )

    assert seen["draw_margin"] == 0.42


def test_value_classes_use_supplied_draw_margin():
    values = torch.tensor([-0.2, 0.2, 0.4])

    defaultish = _value_classes(values, 0.15)
    wider = _value_classes(values, 0.3)

    torch.testing.assert_close(defaultish, torch.tensor([-1.0, 1.0, 1.0]))
    torch.testing.assert_close(wider, torch.tensor([0.0, 0.0, 1.0]))
