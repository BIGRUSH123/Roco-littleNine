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


# ═══════════════════════════════════════════════════════════════════
# 水翼飞升 (ID 20078): power boost for 0-cost skills + energy_cost reduction
# ═══════════════════════════════════════════════════════════════════


def test_trait_水翼飞升_energy_reduction_and_power_mult():
    """水翼飞升: entry with team_counters[element:水]=3 should
    reduce all skills' energy_cost by 3 and give power_mult=1.3 to 0-cost skills."""
    p1 = factory.build_player("A", [
        {"name": "神谕鲨", "skills": ["甩水", "水花四溅"]},
    ])
    # Set trait
    p1.team[0].species.ability = "水翼飞升"
    p1.team[0].species.ability_id = 20078

    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    sprite = p1.team[0]
    # Simulate 3 water skills already used → team_counters[element:水]=3
    for _ in range(3):
        battle.inc_team_counter("A", "element:水")

    # Fire post_entry observer with current team_counters
    dispatch_entry(sprite, battle, "A")
    opp = p2.team[0]
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    battle._vm_engine.fire_trigger("post_entry", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    # Verify energy_cost reduced on all skills
    for bs in (sprite.skills or []):
        ec_mod = bs._modifiers.get("energy_cost", 0)
        bs_name = bs.name
        base_cost = bs.base.energy_cost
        effective = bs.energy_cost
        # energy_cost should be base - 3
        assert ec_mod == -3.0, f"{bs_name}: expected energy_cost mod=-3.0, got {ec_mod}"
        assert effective == base_cost - 3, f"{bs_name}: expected effective cost={base_cost-3}, got {effective}"

    # Verify power_mult=1.3 on 0-cost skills (both 甩水 base=0 and 水花四溅 now effective=0)
    for bs in (sprite.skills or []):
        pm = bs._modifiers.get("power_mult", 1.0)
        assert pm == 1.3, f"{bs.name}: expected power_mult=1.3 (energy_cost=0), got {pm}"

    # Verify _trait_direct_effects is populated for re-application
    assert sprite._trait_direct_effects is not None
    assert len(sprite._trait_direct_effects) >= 2

    # Simulate turn re-application flow
    _PER_TURN_KEYS = frozenset({"power", "power_mult", "damage_mult", "damage_reduction",
                                 "energy_cost", "energy_cost_mult", "priority", "combo_set"})
    _SKILL_PER_TURN_KEYS = _PER_TURN_KEYS | {"combo", "combo_mult"}
    for key in _PER_TURN_KEYS:
        sprite._modifiers.pop(key, None)
    for skill in (sprite.skills or []):
        for key in _SKILL_PER_TURN_KEYS:
            skill._modifiers.pop(key, None)

    battle._vm_engine.trait_loader.reapply_all_direct_mods([sprite])

    # After re-apply: energy_cost should still be -3, power_mult should still be 1.3
    for bs in (sprite.skills or []):
        assert bs._modifiers.get("energy_cost", 0) == -3.0, (
            f"{bs.name}: after re-apply energy_cost should be -3.0, got {bs._modifiers.get('energy_cost', 0)}"
        )
        assert bs._modifiers.get("power_mult", 1.0) == 1.3, (
            f"{bs.name}: after re-apply power_mult should be 1.3 (cost=0), got {bs._modifiers.get('power_mult', 1.0)}"
        )


def test_trait_水翼飞升_zero_cost_energy_not_consumed():
    """水翼飞升: energy payment should be 0 when effective cost is 0."""
    p1 = factory.build_player("A", [
        {"name": "神谕鲨", "skills": ["水花四溅"]},
    ])
    p1.team[0].species.ability = "水翼飞升"
    p1.team[0].species.ability_id = 20078

    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    sprite = p1.team[0]
    for _ in range(3):
        battle.inc_team_counter("A", "element:水")

    dispatch_entry(sprite, battle, "A")
    opp = p2.team[0]
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    battle._vm_engine.fire_trigger("post_entry", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    # Verify bs.energy_cost is 0 (3 base - 3 trait = 0)
    bs = sprite.skills[0]  # 水花四溅
    assert bs.name == "水花四溅", f"Expected 水花四溅 as first skill, got {bs.name}"
    assert bs.base.energy_cost == 3
    assert bs.energy_cost == 0, f"Expected effective energy_cost=0, got {bs.energy_cost}"

    # Verify user._modifiers["energy_cost"] is clean (no on_next/dedication spam)
    assert sprite._modifiers.get("energy_cost", 0) == 0, (
        f"sprite-level energy_cost should be 0, got {sprite._modifiers.get('energy_cost', 0)}"
    )


# ═══════════════════════════════════════════════════════════════════
# 石天平 (ID 20102)
# ═══════════════════════════════════════════════════════════════════


def test_trait_石天平_self_higher_cost_drains_opp_at_turn_end():
    """石天平: self skill cost > opp skill cost → turn_end drain difference."""
    battle = _make_battle_with_trait("石天平", 20102)

    sprite = battle.player_a.active
    opp = battle.player_b.active

    # Get skills and set known energy costs
    sk_self = sprite.skills[0]  # 猛烈撞击 (cost 1)
    sk_opp = opp.skills[0]  # 猛烈撞击 (cost 1)

    # Manually set energy_mod so effective costs differ: self=7, opp=5
    sk_self._modifiers["energy_cost"] = 6.0  # base(1) + 6 = 7
    sk_opp._modifiers["energy_cost"] = 4.0   # base(1) + 4 = 5

    # Build ctx with both skills
    ctx = battle._make_ctx(sprite, opp, sk_self, sk_opp, battle.globals, team="A", turn=1)

    # Verify ctx has correct energy costs
    assert ctx.energy_cost_self == 7, f"expected energy_cost_self=7, got {ctx.energy_cost_self}"
    assert ctx.energy_cost_opp == 5, f"expected energy_cost_opp=5, got {ctx.energy_cost_opp}"

    # Fire post_skill observer
    battle._vm_engine.fire_trigger("post_skill", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    # Verify a schedule entry was created
    assert len(battle.scheduled_effects) == 1, (
        f"expected 1 scheduled effect, got {len(battle.scheduled_effects)}"
    )
    sched = battle.scheduled_effects[0]
    assert sched["phase"] == "end"
    assert sched["turn"] == battle.turn  # turns=0 → fires this turn

    # Verify frozen delta in the scheduled effects
    frozen_then = sched["effects"]
    assert len(frozen_then) == 1
    energize = frozen_then[0]
    assert energize.target == "sprite_opp"
    delta = energize.delta
    assert delta == -2, f"expected frozen delta=-2 (5-7), got {delta} (type={type(delta).__name__})"

    # Execute the scheduled effect
    opp_energy_before = opp.energy
    events = battle._execute_scheduled_effects("end")
    assert opp.energy == opp_energy_before - 2, (
        f"expected opp energy {opp_energy_before}→{opp_energy_before-2}, got {opp.energy}. events: {events}"
    )
    assert len(events) > 0, "expected energy drain event"


def test_trait_石天平_self_lower_cost_no_trigger():
    """石天平: self skill cost <= opp skill cost → no schedule, no drain."""
    battle = _make_battle_with_trait("石天平", 20102)

    sprite = battle.player_a.active
    opp = battle.player_b.active

    sk_self = sprite.skills[0]
    sk_opp = opp.skills[0]
    # self=3, opp=7 (self lower, should NOT trigger)
    sk_self._modifiers["energy_cost"] = 2.0
    sk_opp._modifiers["energy_cost"] = 6.0

    ctx = battle._make_ctx(sprite, opp, sk_self, sk_opp, battle.globals, team="A", turn=1)
    assert ctx.energy_cost_self == 3
    assert ctx.energy_cost_opp == 7

    battle._vm_engine.fire_trigger("post_skill", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    # No schedule should be created
    assert len(battle.scheduled_effects) == 0, (
        f"expected 0 scheduled effects when self_cost <= opp_cost, got {len(battle.scheduled_effects)}"
    )
