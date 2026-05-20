"""roco/tests/test_bridge.py — VM-to-SDK bridge tests."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

import pytest

from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory
from roco.ai.agent import RandomAgent
from roco.ai.observation import ActionKind
from roco.bridge import (
    adapt_agent,
    build_observation,
    legal_actions_filter,
)


@pytest.fixture(scope="module")
def factory():
    return SimFactory()


def _fresh_battle(factory):
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "甩水", "防御"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "甩水", "防御"]},
    ])
    from backend.sim.battle import Battle
    b = Battle(p1, p2, verbose=False)
    b.player_a.active_index = 0
    b.player_b.active_index = 0
    return b


@pytest.fixture
def battle(factory):
    """Fresh battle per test to avoid cross-test state pollution."""
    return _fresh_battle(factory)


# ── build_observation ──


def test_build_observation_structure(battle):
    obs = build_observation(battle, "A")

    assert obs.turn_number == 0
    assert obs.weather == ""
    assert obs.my_sprite.name == "草衣虫"
    assert obs.opp_sprite.name == "花衣蝶"
    assert obs.my_sprite.current_hp > 0
    assert obs.opp_sprite.current_hp > 0
    assert obs.my_sprite.max_ep == 10
    assert len(obs.my_team) == 1
    assert obs.opp_team_size == 1


def test_build_observation_symmetry(battle):
    obs_a = build_observation(battle, "A")
    obs_b = build_observation(battle, "B")

    assert obs_a.my_sprite.name == obs_b.opp_sprite.name
    assert obs_a.opp_sprite.name == obs_b.my_sprite.name


def test_build_observation_fainted_sprite(battle):
    battle.player_a.active.current_hp = 0
    obs = build_observation(battle, "A")
    assert obs.my_sprite.is_fainted is True
    assert obs.my_sprite.current_hp == 0


# ── legal_actions_filter ──


def test_legal_actions_includes_gather(battle):
    actions = legal_actions_filter(battle, "A")
    kinds = [a.kind for a in actions]
    assert ActionKind.GATHER in kinds
    assert ActionKind.PASS in kinds


def test_legal_actions_includes_skills(battle):
    battle.player_a.active.current_hp = battle.player_a.active.max_hp
    battle.player_a.active.energy = 10
    actions = legal_actions_filter(battle, "A")
    skills = [a for a in actions if a.kind == ActionKind.SKILL]
    assert len(skills) >= 1


def test_legal_actions_fainted_only_switch(battle):
    battle.player_a.active.current_hp = 0
    # Add a second alive sprite to switch to
    p = battle.player_a
    from copy import deepcopy
    clone = deepcopy(p.team[0])
    clone.current_hp = clone.max_hp
    p.team.append(clone)

    actions = legal_actions_filter(battle, "A")
    kinds = {a.kind for a in actions}
    assert ActionKind.SWITCH in kinds
    assert ActionKind.SKILL not in kinds
    assert ActionKind.GATHER not in kinds

    # Cleanup
    p.team.pop()


def test_legal_actions_item_available(battle):
    from backend.sim.player import Item
    battle.player_a.item = Item.leader()
    battle.turn = 1
    actions = legal_actions_filter(battle, "A")
    kinds = [a.kind for a in actions]
    assert ActionKind.GATHER in kinds
    assert ActionKind.ITEM in kinds


# ── adapt_agent ──


def test_adapt_agent_choose_action(battle):
    battle.player_a.active.current_hp = battle.player_a.active.max_hp
    battle.player_a.active.energy = 10
    agent = RandomAgent()
    adapted = adapt_agent(agent, "A")

    action = adapted.choose_action(battle)
    assert action.kind in ("skill", "gather", "switch", "item")


def test_adapt_agent_choose_lead(battle):
    agent = RandomAgent()
    adapted = adapt_agent(agent, "A")

    idx = adapted.choose_lead(battle)
    assert 0 <= idx < len(battle.player_a.team)


def test_adapt_agent_on_game_end(battle):
    agent = RandomAgent()
    adapted = adapt_agent(agent, "A")
    adapted.on_game_end("A")  # should not raise


# ── adapt_agent integration: full battle with RandomAgent ──


def test_adapt_agent_full_battle():
    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "甩水", "防御"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "甩水", "防御"]},
    ])
    from backend.sim.battle import Battle
    b = Battle(p1, p2, verbose=False)

    agent_a = adapt_agent(RandomAgent(), "A")
    agent_b = RuleAgent("B", b.player_b)

    result = b.run(agent_a, agent_b)
    assert result is not None
    assert b.turn > 0


def test_adapt_agent_vs_rule_agent_multiple():
    """RandomAgent vs RuleAgent — RuleAgent should win most games (baseline)."""
    rule_wins = 0
    random_wins = 0
    for _ in range(10):
        factory = SimFactory()
        p1 = factory.build_player("A", [
            {"name": "草衣虫", "skills": ["猛烈撞击", "甩水", "防御"]},
        ])
        p2 = factory.build_player("B", [
            {"name": "花衣蝶", "skills": ["猛烈撞击", "甩水", "防御"]},
        ])
        from backend.sim.battle import Battle
        b = Battle(p1, p2, verbose=False)

        agent_a = adapt_agent(RandomAgent(), "A")
        agent_b = RuleAgent("B", b.player_b)

        result = b.run(agent_a, agent_b)
        if result == "B":
            rule_wins += 1
        elif result == "A":
            random_wins += 1

    assert rule_wins + random_wins == 10
    # RandomAgent should at least win some
    assert rule_wins >= 3
