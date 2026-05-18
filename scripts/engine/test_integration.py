"""Smoke test for engine snapshot + replayer with real sim objects."""
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from scripts.engine.snapshot import build_ctx
from scripts.engine.replayer import JournalReplayer
from scripts.sim.sprite import Sprite
from scripts.sim.skill import Skill
from scripts.sim.battleskill import BattleSkill
from scripts.sim.globals import GlobalEffects
from scripts.common.models import SpeciesStats


def test_snapshot():
    species = SpeciesStats(
        name="测试精灵", hp=200, atk=120, def_=100,
        sp_atk=110, sp_def=95, speed=100,
    )
    sprite = Sprite(
        species=species, current_hp=180, max_hp=200, energy=8,
        initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=150, max_hp=180, energy=5,
        initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95},
    )
    skill_data = {"name": "测试技能", "element": "火", "skill_type": "物攻", "power": 100, "energy_cost": 3}
    s = Skill.load(skill_data)
    bs = BattleSkill(base=s)
    globals_ = GlobalEffects()

    ctx = build_ctx(sprite, opp, bs, None, globals_, turn=1, is_first=True)

    assert ctx.hp_self == 180
    assert ctx.hp_opp == 150
    assert ctx.atk_self == 120
    assert ctx.atk_opp == 115
    assert ctx.energy_self == 8
    assert ctx.element_self == "火"
    assert ctx.skill_type_self == "物攻"
    assert ctx.power_self == 100
    assert ctx.turn == 1
    assert ctx.is_first
    print("Snapshot: OK")


def test_replayer():
    from scripts.vm.journal import Damage, Heal, EnergyChange, StatChange, MarkChange, WeatherSet

    species = SpeciesStats(
        name="测试精灵", hp=200, atk=120, def_=100,
        sp_atk=110, sp_def=95, speed=100,
    )
    sprite = Sprite(
        species=species, current_hp=180, max_hp=200, energy=8,
        initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=150, max_hp=180, energy=5,
        initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Test damage
    journal = [Damage(target="sprite_opp", amount=30, element="火", type="物攻")]
    events = replayer.replay(journal)
    assert opp.current_hp == 120  # 150 - 30
    print(f"  Damage: {events[0]}")

    # Test heal
    journal = [Heal(target="sprite_self", amount=15)]
    events = replayer.replay(journal)
    assert sprite.current_hp == 195  # 180 + 15
    print(f"  Heal: {events[0]}")

    # Test energy
    journal = [EnergyChange(target="sprite_self", delta=-3)]
    events = replayer.replay(journal)
    assert sprite.energy == 5  # 8 - 3
    print(f"  Energy: {events[0]}")

    # Test stat change
    journal = [StatChange(target="sprite_self", stat="atk", steps=3, scope="battlefield")]
    events = replayer.replay(journal)
    assert any(e.stat_key == "atk" and e.steps == 3 for e in sprite.effects if e.category == "stat")
    assert sprite.effective_stat("atk") == 120 * 1.3  # base * (1 + 3*0.1)
    print(f"  StatChange: {events[0]}")
    print(f"  ATK after mod: {sprite.effective_stat('atk')}")

    # Test mark
    journal = [MarkChange(target_team="own", name="攻击印记", delta=2)]
    events = replayer.replay(journal)
    print(f"  MarkChange: {events[0]}")

    # Test weather
    journal = [WeatherSet(weather="rain", turns=8)]
    events = replayer.replay(journal)
    assert globals_.weather == "rain"
    print(f"  WeatherSet: {events[0]}")

    print("Replayer: OK")


def test_modifier_collection():
    """Test that ModifierInjections from journal are collected and applied to Damage."""
    from scripts.vm.journal import Damage, ModifierInjection, Journal
    from scripts.vm.ctx import Ctx
    from scripts.engine.modifiers import collect_modifiers, adjust_damage

    # Simulate: skill has an effect that produces power_mult=1.5, then hit deals damage
    ctx = Ctx(power_self=100, atk_self=120, def_opp=100, skill_type_self="物攻",
              element_self="火", damage_reduction_opp=0.0, combo_self=1)

    journal: Journal = [
        ModifierInjection(target="skill_off_0", stat="power_mult", value=1.5, mode="multiply", scope="battlefield"),
        Damage(target="sprite_opp", amount=108, element="火", type="物攻"),
    ]

    mods = collect_modifiers(journal, ctx)
    assert mods["power_mult"] == 1.5, f"Expected power_mult=1.5, got {mods['power_mult']}"
    assert mods["damage_mult"] == 1.0, f"Expected damage_mult=1.0, got {mods['damage_mult']}"

    # Adjust the damage
    adjusted = adjust_damage(journal[1], mods)
    assert adjusted.amount == round(108 * 1.5), f"Expected {round(108*1.5)}, got {adjusted.amount}"
    print(f"  Modifier collection: power_mult={mods['power_mult']}, damage={108}→{adjusted.amount}")


def test_modifier_chain():
    """Test multiple stacked modifiers."""
    from scripts.vm.journal import Damage, ModifierInjection, Journal
    from scripts.vm.ctx import Ctx
    from scripts.engine.modifiers import collect_modifiers, adjust_damage

    ctx = Ctx(power_self=80, atk_self=100, def_opp=100, skill_type_self="物攻",
              element_self="水", damage_reduction_opp=0.0, combo_self=1)

    journal: Journal = [
        ModifierInjection(target="skill_off_0", stat="power_mult", value=2.0, mode="multiply", scope="battlefield"),
        ModifierInjection(target="skill_off_0", stat="damage_mult", value=1.3, mode="multiply", scope="battlefield"),
        ModifierInjection(target="skill_off_0", stat="combo", value=2, mode="add", scope="battlefield"),
        Damage(target="sprite_opp", amount=72, element="水", type="魔攻"),
    ]

    mods = collect_modifiers(journal, ctx)
    assert mods["power_mult"] == 2.0
    assert mods["damage_mult"] == 1.3
    assert mods["combo_add"] == 2

    # (72 * power_mult * damage_mult * (1 + combo_add))
    adjusted = adjust_damage(journal[3], mods)
    expected = round(round(72 * 2.0 * 1.3) * 3)  # intermediate rounding
    assert adjusted.amount == expected, f"Expected {expected}, got {adjusted.amount}"
    print(f"  Modifier chain: power_mult×2.0 + damage_mult×1.3 + combo+2, damage={72}→{adjusted.amount}")


def test_modifier_collection_end_to_end():
    """Full pipeline: sprite with effects → Ctx → VM-like execution → modifier collection."""
    from scripts.vm.journal import Damage, ModifierInjection, Journal
    from scripts.vm.ctx import Ctx
    from scripts.engine.modifiers import apply_modifiers_to_journal

    # Simulate what the engine does: build ctx, run VM, get journal, apply modifiers
    ctx = Ctx(power_self=75, atk_self=130, def_opp=110, skill_type_self="物攻",
              element_self="火", damage_reduction_opp=0.15, combo_self=1,
              stat_stages_self={"atk": 2}, stat_stages_opp={"def": -1})

    # Journal from VM: modifier + damage
    journal: Journal = [
        ModifierInjection(target="skill_off_0", stat="power_mult", value=1.5, mode="multiply", scope="battlefield"),
        Damage(target="sprite_opp", amount=80, element="火", type="物攻"),
    ]

    adjusted_journal = apply_modifiers_to_journal(journal, ctx)

    # The damage should now be higher due to power_mult=1.5
    assert len(adjusted_journal) == 2
    assert isinstance(adjusted_journal[1], Damage)
    assert adjusted_journal[1].amount > 80, f"Expected damage > 80, got {adjusted_journal[1].amount}"
    # power_mult 1.5 should increase damage by ~50%
    assert adjusted_journal[1].amount == round(80 * 1.5)
    print(f"  E2E modifier pipeline: damage {80}→{adjusted_journal[1].amount} with power_mult×1.5")


def test_life_drain():
    """Test that life drain modifier heals attacker when dealing damage."""
    from scripts.vm.journal import Damage, ModifierInjection, Journal
    from scripts.engine.replayer import JournalReplayer
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    species = SpeciesStats(
        name="测试精灵", hp=200, atk=120, def_=100,
        sp_atk=110, sp_def=95, speed=100,
    )
    attacker = Sprite(
        species=species, current_hp=180, max_hp=200, energy=8,
        initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100},
    )
    defender = Sprite(
        species=species, current_hp=150, max_hp=180, energy=5,
        initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(attacker, defender, globals_)

    # Simulate: life_drain modifier applied first, then damage
    journal: Journal = [
        ModifierInjection(target="skill_off_0", stat="life_drain", value=0.5, mode="set", scope="battlefield"),
        Damage(target="sprite_opp", amount=40, element="火", type="物攻"),
    ]
    events = replayer.replay(journal)

    # Attacker should have drained 50% of 40 = 20 HP
    assert attacker.current_hp == 200  # 180 + 20 = 200 (capped at max_hp)
    assert defender.current_hp == 110  # 150 - 40 = 110
    assert any("吸血+20HP" in e for e in events), f"Expected life drain event, got {events}"
    print(f"  Life drain: {events}")


if __name__ == "__main__":
    test_snapshot()
    test_replayer()
    test_modifier_collection()
    test_modifier_chain()
    test_modifier_collection_end_to_end()
    test_life_drain()
    print("\nAll engine integration tests passed!")
