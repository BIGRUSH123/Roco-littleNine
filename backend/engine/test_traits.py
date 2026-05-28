"""Tests for data-driven traits: observer + inherit + post_enemy_leave."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.common.models import SpeciesStats
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.sprite import Sprite
from backend.sim.traits import dispatch_entry
from backend.vm.effect import StatBuffEffect

factory = SimFactory()


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _make_battle_with_trait(p1_trait_name: str, p1_trait_id: int = 0):
    """Build a battle where player A's active sprite has the given trait."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "甩水", "防御"]},
    ])
    p1.team[0].species.ability = p1_trait_name
    p1.team[0].species.ability_id = p1_trait_id

    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "甩水", "防御"]},
        {"name": "草衣虫", "skills": ["猛烈撞击"]},  # bench
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    # Initialize sprites: load trait observers
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")

    return battle


def _make_buff(stat_key: str, steps: int, scope: str = "battlefield") -> StatBuffEffect:
    """Create a StatBuffEffect with minimal required fields."""
    return StatBuffEffect(
        name=f"{stat_key}_{'+' if steps > 0 else ''}{steps}",
        source="test",
        stat_key=stat_key,
        steps=steps,
        scope=scope,
    )


def _active_effects_of(sprite, stat_key_filter: str | None = None):
    """Return active effects, optionally filtered by stat_key."""
    if stat_key_filter is None:
        return list(sprite.active_effects)
    return [e for e in sprite.active_effects
            if getattr(e, 'stat_key', '') == stat_key_filter]


def _make_bench_sprite(name: str = "替补") -> Sprite:
    """Create a minimal bench sprite for switch testing."""
    return Sprite(
        species=SpeciesStats(
            name=name, hp=100, atk=80, def_=80,
            sp_atk=80, sp_def=80, speed=80,
        ),
        current_hp=100, max_hp=100, energy=10,
        initial_stats={"hp": 100, "atk": 80, "def": 80,
                       "sp_atk": 80, "sp_def": 80, "speed": 80},
    )


# ═══════════════════════════════════════════════════════════════════
# 孤傲 (ID 20127)
# ═══════════════════════════════════════════════════════════════════


def test_trait_孤傲_enemy_switch_inherits_stat_buffs():
    """孤傲: enemy sprite leaves → replacement inherits its battlefield stat effects."""
    battle = _make_battle_with_trait("孤傲")

    enemy_old = battle.player_b.active  # 花衣蝶 (will leave)
    enemy_new = battle.player_b.team[1]  # 草衣虫 (will enter)

    enemy_old.add_effect(_make_buff("atk", 2))
    enemy_old.add_effect(_make_buff("speed", -1))

    assert len(_active_effects_of(enemy_old, "atk")) == 1
    assert len(_active_effects_of(enemy_old, "speed")) == 1
    assert len(enemy_new.active_effects) == 0

    # Switch enemy: team B switches from index 0 → index 1
    battle._resolve_switch("B", Action(kind="switch", switch_index=1))

    new_active = battle.player_b.active
    assert new_active is enemy_new

    atk_effects = _active_effects_of(new_active, "atk")
    assert len(atk_effects) == 1, (
        f"Expected inherited atk+2, got {len(atk_effects)} effects: {new_active.active_effects}"
    )
    assert atk_effects[0].steps == 2

    spd_effects = _active_effects_of(new_active, "speed")
    assert len(spd_effects) == 1
    assert spd_effects[0].steps == -1


def test_trait_孤傲_no_buffs_no_inherit():
    """孤傲: if the leaving enemy has no effects, nothing is inherited."""
    battle = _make_battle_with_trait("孤傲")

    enemy_new = battle.player_b.team[1]
    assert len(enemy_new.active_effects) == 0

    battle._resolve_switch("B", Action(kind="switch", switch_index=1))

    new_active = battle.player_b.active
    assert new_active is enemy_new
    # dispatch_entry loads the new sprite's own trait as an ObserverEffect,
    # but no StatBuffEffect should have been inherited
    stat_effects = [e for e in new_active.active_effects
                    if hasattr(e, 'stat_key')]
    assert len(stat_effects) == 0


def test_trait_孤傲_inherits_stat_buffs_regardless_of_scope():
    """孤傲: inherit_stat_effects copies ALL StatBuffEffect objects,
    including those with persistent scope (not just battlefield)."""
    battle = _make_battle_with_trait("孤傲")

    enemy_old = battle.player_b.active
    enemy_new = battle.player_b.team[1]

    enemy_old.add_effect(_make_buff("atk", 1, scope="battlefield"))
    enemy_old.add_effect(_make_buff("def", 2, scope="persistent"))
    enemy_old.add_effect(_make_buff("speed", -1, scope="battlefield"))

    battle._resolve_switch("B", Action(kind="switch", switch_index=1))

    new_active = battle.player_b.active
    assert new_active is enemy_new

    # All StatBuffEffect objects are inherited regardless of scope
    assert len(_active_effects_of(new_active, "atk")) == 1
    assert len(_active_effects_of(new_active, "def")) == 1
    assert len(_active_effects_of(new_active, "speed")) == 1


def test_trait_孤傲_does_not_inherit_non_stat_effects():
    """孤傲: inherit_stat_effects only copies StatBuffEffect objects,
    not other effect types like ObserverEffect or AbnormalEffect."""
    battle = _make_battle_with_trait("孤傲")

    enemy_old = battle.player_b.active
    enemy_new = battle.player_b.team[1]

    enemy_old.add_effect(_make_buff("atk", 2))

    # Add a non-StatBuffEffect — should NOT be inherited
    from backend.vm.effect import AbnormalEffect
    enemy_old.add_effect(AbnormalEffect(
        name="测试中毒", source="test",
        stacks=3, tick_damage_pct=0.0625,
    ))

    battle._resolve_switch("B", Action(kind="switch", switch_index=1))

    new_active = battle.player_b.active
    assert new_active is enemy_new

    # StatBuffEffect → inherited
    assert len(_active_effects_of(new_active, "atk")) == 1
    # AbnormalEffect → NOT inherited
    abnormals = [e for e in new_active.active_effects
                 if isinstance(e, AbnormalEffect)]
    assert len(abnormals) == 0


def test_trait_孤傲_self_switch_does_not_trigger():
    """孤傲: switching our own sprite does NOT trigger the trait
    (it only watches enemy leaves via sprite_left of=sprite_opp)."""
    battle = _make_battle_with_trait("孤傲")

    our_sprite = battle.player_a.active
    our_sprite.add_effect(_make_buff("atk", 1))

    # Add a bench sprite for team A so we can switch
    extra = _make_bench_sprite()
    battle.player_a.team.append(extra)

    battle._resolve_switch("A", Action(kind="switch", switch_index=1))

    new_our_active = battle.player_a.active
    assert len(_active_effects_of(new_our_active, "atk")) == 0


def test_trait_孤傲_faint_replace_also_inherits():
    """孤傲: when enemy faints and is replaced, effects are also inherited."""
    battle = _make_battle_with_trait("孤傲")

    enemy_old = battle.player_b.active
    enemy_new = battle.player_b.team[1]

    enemy_old.add_effect(_make_buff("atk", 3))
    enemy_old.current_hp = 0  # faint

    # _check_faint_interrupt needs an agent; mock one that returns the bench index
    class _MockAgent:
        def choose_replacement(self, _battle):
            return 1

    battle._agent_b = _MockAgent()

    events: list[str] = []
    battle._check_faint_interrupt("B", events)

    new_active = battle.player_b.active
    assert new_active is enemy_new

    atk_effects = _active_effects_of(new_active, "atk")
    assert len(atk_effects) == 1
    assert atk_effects[0].steps == 3


def test_trait_孤傲_does_not_inherit_trait_sourced_stat_effects():
    """孤傲: trait-sourced StatBuffEffect (is_inherent=True)
    should NOT be inherited — only skill-given stat changes."""
    battle = _make_battle_with_trait("孤傲")

    enemy_old = battle.player_b.active

    # Simulate a skill-given buff (is_inherent=False, the default)
    enemy_old.add_effect(_make_buff("atk", 2))

    # Simulate a trait-sourced buff (is_inherent=True, e.g. from 壮胆)
    enemy_old.add_effect(StatBuffEffect(
        name="sp_atk_+5", source="壮胆", scope="battlefield",
        stat_key="sp_atk", steps=5, is_inherent=True,
    ))

    battle._resolve_switch("B", Action(kind="switch", switch_index=1))

    new_active = battle.player_b.active

    # Skill-given atk buff → inherited
    assert len(_active_effects_of(new_active, "atk")) == 1
    # Trait-sourced sp_atk buff → NOT inherited
    assert len(_active_effects_of(new_active, "sp_atk")) == 0
