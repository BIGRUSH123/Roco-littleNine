"""Test and benchmark the skill summary cache in snapshot.py."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.battle import Battle
from backend.sim.factory import SimFactory

factory = SimFactory()


def test_skill_summary_cache_basic():
    """Test that skill summary cache is created and used."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    sprite = battle.player_a.active

    # First call should create cache
    ctx1 = battle._make_ctx(
        sprite,
        battle.player_b.active,
        sprite.skills[0],
        None,
        battle.globals,
        team="A",
        turn=1,
    )

    # Check cache was created
    assert hasattr(sprite, '_skill_summary_cache')
    cache = sprite._skill_summary_cache
    assert cache is not None
    assert len(cache) == 2  # (key, result)

    # Second call should reuse cache
    ctx2 = battle._make_ctx(
        sprite,
        battle.player_b.active,
        sprite.skills[0],
        None,
        battle.globals,
        team="A",
        turn=1,
    )

    # Cache should be unchanged
    assert sprite._skill_summary_cache is cache


def test_skill_summary_cache_invalidation():
    """Test that cache is invalidated when skill state changes."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    sprite = battle.player_a.active

    # First call
    ctx1 = battle._make_ctx(
        sprite,
        battle.player_b.active,
        sprite.skills[0],
        None,
        battle.globals,
        team="A",
        turn=1,
    )
    cache_key_1 = sprite._skill_summary_cache[0]

    # Modify skill state
    sprite.skills[0]._modifiers["energy_cost"] = -10

    # Second call should detect change
    ctx2 = battle._make_ctx(
        sprite,
        battle.player_b.active,
        sprite.skills[0],
        None,
        battle.globals,
        team="A",
        turn=1,
    )
    cache_key_2 = sprite._skill_summary_cache[0]

    # Cache key should be different
    assert cache_key_1 != cache_key_2
