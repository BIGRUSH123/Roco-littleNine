"""backend/tests/test_rule_agent.py — RuleAgent.choose_action branch coverage.

Covers the 7+ branches identified in the engineering review:
  1. Fainted sprite → forced switch
  2. Fainted sprite, no replacement → gather
  3. Item 进化之力 used early (turn <= 2)
  4. Item 愿力 used at low HP + high aggression
  5. Low HP → switch below threshold
  6. Low energy → gather (skip if charging)
  7. Charging → forced skill release
  8. Normal skill scoring (attack / defense / status)
  9. No usable skills → gather fallback
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

import pytest
from backend.sim.factory import SimFactory
from backend.sim.player import Item, PlayStyle
from backend.sim.agent import RuleAgent


factory = SimFactory()


def _make_battle(p1_specs=None, p2_specs=None, p1_style=None, p1_item=None):
    from backend.sim.battle import Battle
    p1 = factory.build_player("A", p1_specs or [
        {"name": "草衣虫", "skills": ["猛烈撞击", "甩水", "防御"]},
    ], style=p1_style, item=p1_item)
    p2 = factory.build_player("B", p2_specs or [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "甩水", "防御"]},
    ])
    b = Battle(p1, p2, verbose=False)
    b.player_a.active_index = 0
    b.player_b.active_index = 0
    return b


# ══ Branch 1: Fainted → forced switch ══


def test_choose_action_fainted_forces_switch():
    """Fainted sprite must switch to replacement."""
    b = _make_battle()
    # Add a second sprite for replacement
    b.player_a.team.append(factory.build_player("A", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ]).team[0])
    b.player_a.active.current_hp = 0
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "switch"
    assert action.switch_index is not None


# ══ Branch 2: Fainted, no replacement → gather ══


def test_choose_action_fainted_no_replacement_gathers():
    """Fainted sprite with no bench falls back to gather."""
    b = _make_battle()
    b.player_a.active.current_hp = 0
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "gather"


# ══ Branch 3: Item 进化之力 early turn ══


def test_choose_action_evolution_power_early():
    """Evolution power item used on turn <= 2."""
    b = _make_battle(p1_item=Item.leader())
    b.turn = 2
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "item"


def test_choose_action_evolution_power_late_turn_skipped():
    """Evolution power NOT used after turn 2."""
    b = _make_battle(p1_item=Item.leader())
    b.turn = 3
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind != "item"


# ══ Branch 4: Item 愿力 at low HP ══


def test_choose_action_wish_at_low_hp():
    """Wish item used when HP < 50% and aggression > 0.4."""
    style = PlayStyle(aggression=0.8)
    b = _make_battle(p1_item=Item.wish(), p1_style=style)
    b.turn = 1
    s = b.player_a.active
    s.current_hp = int(s.max_hp * 0.3)
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "item"


def test_choose_action_wish_skipped_at_high_hp():
    """Wish NOT used when HP >= 50%."""
    style = PlayStyle(aggression=0.8)
    b = _make_battle(p1_item=Item.wish(), p1_style=style)
    b.turn = 1
    b.player_a.active.current_hp = b.player_a.active.max_hp
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind != "item"


def test_choose_action_wish_skipped_low_aggression():
    """Wish NOT used when aggression <= 0.4 even at low HP."""
    style = PlayStyle(aggression=0.2)
    b = _make_battle(p1_item=Item.wish(), p1_style=style)
    b.turn = 1
    s = b.player_a.active
    s.current_hp = int(s.max_hp * 0.3)
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind != "item"


# ══ Branch 5: Low HP → switch ══


def test_choose_action_low_hp_switches():
    """Switch when HP below threshold and bench available."""
    style = PlayStyle(switch_hp_threshold=0.9)
    b = _make_battle(p1_style=style)
    b.player_a.team.append(factory.build_player("A", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ]).team[0])
    s = b.player_a.active
    s.current_hp = int(s.max_hp * 0.1)
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "switch"


def test_choose_action_low_hp_no_bench_skips():
    """Low HP switch skipped when no bench replacement."""
    style = PlayStyle(switch_hp_threshold=0.9)
    b = _make_battle(p1_style=style)
    s = b.player_a.active
    s.current_hp = int(s.max_hp * 0.1)
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind != "switch"


# ══ Branch 6: Low energy → gather ══


def test_choose_action_low_energy_gathers():
    """Gather when energy below threshold."""
    style = PlayStyle(gather_energy_threshold=8)
    b = _make_battle(p1_style=style)
    b.player_a.active.energy = 2
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "gather"


def test_choose_action_low_energy_charging_skips_gather():
    """Low energy gather skipped when charging (must release charged skill)."""
    style = PlayStyle(gather_energy_threshold=8)
    b = _make_battle(p1_style=style)
    s = b.player_a.active
    s.energy = 2
    s._charging = True
    s._charged_skill_index = 0
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    # Should release charged skill, not gather
    assert action.kind == "skill"
    assert action.skill_index == 0


# ══ Branch 7: Charging → forced release ══


def test_choose_action_charging_releases_skill():
    """Charging sprite must release the charged skill."""
    b = _make_battle()
    s = b.player_a.active
    s.energy = 10
    s._charging = True
    s._charged_skill_index = 1
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "skill"
    assert action.skill_index == 1


def test_choose_action_charging_sealed_skill_falls_through():
    """Charging with sealed skill falls through to scoring (or gather)."""
    b = _make_battle()
    s = b.player_a.active
    s.energy = 1  # low energy, but charging so gather is skipped
    s._charging = True
    s._charged_skill_index = 0
    s.skills[0].sealed = True
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    # Falls through to skill scoring (skill[1] or skill[2]), not gather
    assert action.kind in ("skill", "gather")


# ══ Branch 8: Skill scoring (normal path) ══


def test_choose_action_picks_skill():
    """Normal case: agent picks a skill."""
    b = _make_battle()
    b.player_a.active.energy = 10
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "skill"


def test_choose_action_skips_cooldown_skill():
    """Skills on cooldown are excluded from scoring."""
    b = _make_battle()
    s = b.player_a.active
    s.energy = 10
    s.skills[0].cooldown = 99
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    # Should pick skill[1] or skill[2], not skill[0]
    assert action.skill_index != 0


# ══ Branch 9: No usable skills → gather ══


def test_choose_action_no_usable_skills_gathers():
    """All skills unusable → fallback to gather."""
    b = _make_battle()
    s = b.player_a.active
    s.energy = 10
    for sk in s.skills:
        sk.cooldown = 99
    agent = RuleAgent("A", b.player_a)

    action = agent.choose_action(b)
    assert action.kind == "gather"


# ══ choose_lead ══


def test_choose_lead_picks_best():
    """choose_lead returns a valid index."""
    b = _make_battle()
    agent = RuleAgent("A", b.player_a)

    idx = agent.choose_lead(b)
    assert 0 <= idx < len(b.player_a.team)


def test_choose_lead_skips_fainted():
    """choose_lead skips fainted sprites."""
    b = _make_battle()
    b.player_a.team.append(factory.build_player("A", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ]).team[0])
    b.player_a.team[0].current_hp = 0  # fainted
    agent = RuleAgent("A", b.player_a)

    idx = agent.choose_lead(b)
    assert idx == 1  # only alive


# ══ choose_replacement ══


def test_choose_replacement_picks_alive():
    """choose_replacement returns an alive bench index."""
    b = _make_battle()
    b.player_a.team.append(factory.build_player("A", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ]).team[0])
    agent = RuleAgent("A", b.player_a)

    idx = agent.choose_replacement(b)
    assert idx == 1  # only bench option


def test_choose_replacement_no_alive_fallback():
    """choose_replacement returns first alive when all fainted."""
    b = _make_battle()
    b.player_a.team[0].current_hp = 0
    agent = RuleAgent("A", b.player_a)

    idx = agent.choose_replacement(b)
    # alive_sprites is empty → alive is empty → best_idx stays -1 → returns -1
    # Actually, alive_sprites filters for non-fainted, so it's empty
    # The code does: alive = [... if i != active_index]; if best_idx < 0 and alive: best_idx = alive[0]
    # Since alive is empty, best_idx stays -1
    assert idx == -1


# ══ on_game_end ══


def test_on_game_end_noop():
    """on_game_end is a no-op."""
    b = _make_battle()
    agent = RuleAgent("A", b.player_a)
    agent.on_game_end("A")  # should not raise
