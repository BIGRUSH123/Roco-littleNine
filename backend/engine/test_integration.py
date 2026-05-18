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


def test_counter_damage_flow():
    """Test that counter_succeeded hit effects deal damage to opponent."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.engine.snapshot import build_ctx
    from scripts.engine.replayer import JournalReplayer
    from scripts.engine.modifiers import apply_modifiers_to_journal
    from scripts.sim.sprite import Sprite
    from scripts.sim.skill import Skill
    from scripts.sim.battleskill import BattleSkill
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    species = SpeciesStats(
        name="测试精灵", hp=200, atk=120, def_=100,
        sp_atk=110, sp_def=95, speed=100,
    )
    # Countering sprite (defense skill user)
    counter_sprite = Sprite(
        species=species, current_hp=180, max_hp=200, energy=8,
        initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100},
    )
    # Attacker (being countered)
    attacker = Sprite(
        species=species, current_hp=150, max_hp=180, energy=5,
        initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95},
    )
    globals_ = GlobalEffects()

    # Build RISC IR effects for counter skill (like 硬门: counter_succeeded → hit 90 物攻)
    effects = [
        {
            "when": {"cond": "counter_succeeded"},
            "then": [
                {"op": "hit", "power": 90, "type": "物攻"},
            ],
        },
    ]

    # Build ctx with counter_succeeded=True
    ctx = build_ctx(
        counter_sprite, attacker,
        Skill.load({"name": "测试防御", "element": "武", "skill_type": "防御", "power": 0, "energy_cost": 2}),
        Skill.load({"name": "测试攻击", "element": "火", "skill_type": "物攻", "power": 80, "energy_cost": 3}),
        globals_,
        turn=1, is_first=False,
        counter_succeeded=True,
    )

    # VM execution
    journal = vm_execute(ctx, effects)
    # Apply same-skill modifiers
    journal = apply_modifiers_to_journal(journal, ctx)

    # Replay
    replayer = JournalReplayer(counter_sprite, attacker, globals_)
    events = replayer.replay(journal)

    # Attacker should have taken damage from counter hit
    assert attacker.current_hp < 150, f"Counter damage should reduce HP, got {attacker.current_hp}"
    print(f"  Counter damage: {attacker.current_hp} HP remaining ({150 - attacker.current_hp} damage dealt)")
    print(f"  Events: {events}")


def test_counter_succeeded_flag_flow():
    """Test that counter_succeeded flag correctly gates conditional effects."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.engine.snapshot import build_ctx
    from scripts.engine.replayer import JournalReplayer
    from scripts.engine.modifiers import apply_modifiers_to_journal
    from scripts.sim.sprite import Sprite
    from scripts.sim.skill import Skill
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    species = SpeciesStats(
        name="测试精灵", hp=200, atk=120, def_=100,
        sp_atk=110, sp_def=95, speed=100,
    )
    counter_sprite = Sprite(
        species=species, current_hp=180, max_hp=200, energy=8,
        initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100},
    )
    attacker = Sprite(
        species=species, current_hp=150, max_hp=180, energy=5,
        initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95},
    )
    globals_ = GlobalEffects()

    effects = [
        {
            "when": {"cond": "counter_succeeded"},
            "then": [
                {"op": "hit", "power": 90, "type": "物攻"},
            ],
        },
        # Unconditional mod that always applies
        {
            "op": "mod",
            "target": "sprite_self",
            "stat": "damage_reduction",
            "value": 0.6,
        },
    ]

    # Test 1: counter_succeeded=False → no hit damage, only mod
    ctx_fail = build_ctx(
        counter_sprite, attacker,
        Skill.load({"name": "测试防御", "element": "武", "skill_type": "防御", "power": 0, "energy_cost": 2}),
        None, globals_,
        turn=1, is_first=False,
        counter_succeeded=False,
    )
    hp_before = attacker.current_hp
    journal_fail = vm_execute(ctx_fail, effects)
    replayer = JournalReplayer(counter_sprite, attacker, globals_)
    replayer.replay(journal_fail)
    assert attacker.current_hp == hp_before, f"Without counter_succeeded, no damage expected, got HP={attacker.current_hp}"
    print(f"  counter_succeeded=False: no damage (HP={attacker.current_hp}) ✓")

    # Test 2: Reset, counter_succeeded=True → hit damage dealt
    attacker.current_hp = 150
    ctx_success = build_ctx(
        counter_sprite, attacker,
        Skill.load({"name": "测试防御", "element": "武", "skill_type": "防御", "power": 0, "energy_cost": 2}),
        Skill.load({"name": "测试攻击", "element": "火", "skill_type": "物攻", "power": 80, "energy_cost": 3}),
        globals_,
        turn=1, is_first=False,
        counter_succeeded=True,
    )
    journal_success = vm_execute(ctx_success, effects)
    journal_success = apply_modifiers_to_journal(journal_success, ctx_success)
    replayer2 = JournalReplayer(counter_sprite, attacker, globals_)
    events = replayer2.replay(journal_success)
    assert attacker.current_hp < 150, f"With counter_succeeded, damage expected, got HP={attacker.current_hp}"
    print(f"  counter_succeeded=True: damage dealt (HP={attacker.current_hp}, events={events}) ✓")


def test_counter_refs_opp_power():
    """Test counter hit that references opponent's skill power (like 听桥)."""
    from scripts.vm.executor import execute as vm_execute
    from scripts.engine.snapshot import build_ctx
    from scripts.engine.replayer import JournalReplayer
    from scripts.engine.modifiers import apply_modifiers_to_journal
    from scripts.sim.sprite import Sprite
    from scripts.sim.skill import Skill
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    species_self = SpeciesStats(
        name="防御者", hp=200, atk=120, def_=120,
        sp_atk=110, sp_def=110, speed=100,
    )
    species_opp = SpeciesStats(
        name="攻击者", hp=180, atk=130, def_=80,
        sp_atk=100, sp_def=80, speed=110,
    )
    counter_sprite = Sprite(
        species=species_self, current_hp=200, max_hp=200, energy=8,
        initial_stats={"atk": 120, "def": 120, "sp_atk": 110, "sp_def": 110, "speed": 100},
    )
    attacker = Sprite(
        species=species_opp, current_hp=180, max_hp=180, energy=5,
        initial_stats={"atk": 130, "def": 80, "sp_atk": 100, "sp_def": 80, "speed": 110},
    )
    globals_ = GlobalEffects()

    # 听桥-style: use countered skill's power (120) as hit power
    effects = [
        {
            "when": {"cond": "counter_succeeded"},
            "then": [{
                "op": "hit",
                "power": {"q": "power_base", "of": "skill_opp_current"},
                "type": "物攻",
            }],
        },
    ]

    # Countered skill with power=120
    opp_skill = Skill.load({"name": "强力攻击", "element": "火", "skill_type": "物攻", "power": 120, "energy_cost": 4})

    ctx = build_ctx(
        counter_sprite, attacker,
        Skill.load({"name": "听桥式", "element": "武", "skill_type": "防御", "power": 0, "energy_cost": 4}),
        opp_skill, globals_,
        turn=1, is_first=False,
        counter_succeeded=True,
    )

    journal = vm_execute(ctx, effects)
    journal = apply_modifiers_to_journal(journal, ctx)

    replayer = JournalReplayer(counter_sprite, attacker, globals_)
    events = replayer.replay(journal)

    # Damage should be based on countered skill's power=120
    hp_lost = 180 - attacker.current_hp
    print(f"  Counter with opp_power=120: dealt {hp_lost} damage, HP={attacker.current_hp}")
    assert hp_lost > 0, "Should deal damage based on countered skill power"
    # Power 120 should deal more than power 90 would
    assert hp_lost >= 30, f"Expected meaningful damage with power 120, got {hp_lost}"


def test_escape_mutation_production():
    """Test that a skill with escape opcode produces an Escape mutation."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Escape
    from scripts.engine.snapshot import build_ctx
    from scripts.engine.replayer import JournalReplayer
    from scripts.sim.sprite import Sprite
    from scripts.sim.skill import Skill
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

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

    # Escape skill like 恶意逃离: escape without inherit
    effects = [{"target": "sprite_self", "op": "escape"}]
    ctx = build_ctx(
        sprite, opp,
        Skill.load({"name": "测试脱离", "element": "恶", "skill_type": "状态", "power": 0, "energy_cost": 1}),
        None, globals_,
        turn=1, is_first=False,
    )
    journal = vm_execute(ctx, effects)
    escape_muts = [m for m in journal if isinstance(m, Escape)]
    assert len(escape_muts) == 1, f"Expected 1 Escape mutation, got {len(escape_muts)}"
    assert escape_muts[0].inherit is False
    assert escape_muts[0].target == "sprite_self"
    print(f"  Escape mutation: target={escape_muts[0].target}, inherit={escape_muts[0].inherit}")

    # Escape with inherit (like 击鼓传花)
    effects2 = [{"target": "sprite_self", "op": "escape", "inherit": True}]
    journal2 = vm_execute(ctx, effects2)
    escape_muts2 = [m for m in journal2 if isinstance(m, Escape)]
    assert len(escape_muts2) == 1
    assert escape_muts2[0].inherit is True
    print(f"  Escape+inherit mutation: inherit={escape_muts2[0].inherit}")


def test_return_mutation_production():
    """Test that return opcode produces Return mutation and sets pending_return."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Return
    from scripts.engine.snapshot import build_ctx
    from scripts.engine.replayer import JournalReplayer
    from scripts.sim.sprite import Sprite
    from scripts.sim.skill import Skill
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

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

    effects = [{"target": "sprite_self", "op": "return"}]
    ctx = build_ctx(
        sprite, opp,
        Skill.load({"name": "测试返场", "element": "电", "skill_type": "状态", "power": 0, "energy_cost": 1}),
        None, globals_,
        turn=1, is_first=False,
    )

    journal = vm_execute(ctx, effects)
    return_muts = [m for m in journal if isinstance(m, Return)]
    assert len(return_muts) == 1, f"Expected 1 Return mutation, got {len(return_muts)}"

    # Replay: should set pending_return on sprite
    replayer = JournalReplayer(sprite, opp, globals_)
    events = replayer.replay(journal)
    assert sprite.pending_return is True, f"Expected pending_return=True, got {sprite.pending_return}"
    print(f"  Return mutation: pending_return={sprite.pending_return}, events={events}")


def test_e2e_attack_skill():
    """End-to-end: execute a real attack skill through VM engine."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats
    from scripts.vm.journal import Damage

    loader = SkillLoader()
    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = loader.load_file("data/skills/龙爪.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=True, team="A")

    assert opp.current_hp < 150, f"Expected damage to opponent, HP={opp.current_hp}"
    damages = [m for m in result.journal if isinstance(m, Damage)]
    assert len(damages) == 1
    assert damages[0].amount > 0
    print(f"  龙爪 damage: {damages[0].amount}, opp HP: {opp.current_hp}")


def test_e2e_defense_counter():
    """End-to-end: defense skill with counter_succeeded conditional effects."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    loader = SkillLoader()
    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = loader.load_file("data/skills/防反.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=False, team="B",
                                   counter_succeeded=True)

    # Should have stat changes from counter_succeeded
    atk_stages = sum(getattr(e, 'steps', 0) for e in sprite.effects if getattr(e, 'stat_key', '') == "atk")
    sp_atk_stages = sum(getattr(e, 'steps', 0) for e in sprite.effects if getattr(e, 'stat_key', '') == "sp_atk")
    assert atk_stages == 4, f"Expected atk+4 from counter_succeeded, got {atk_stages}"
    assert sp_atk_stages == 4, f"Expected sp_atk+4 from counter_succeeded, got {sp_atk_stages}"
    print(f"  防反 counter: atk+{atk_stages}, sp_atk+{sp_atk_stages}")


def test_e2e_escape_skill():
    """End-to-end: skill with escape opcode produces Escape mutation."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats
    from scripts.vm.journal import Escape

    loader = SkillLoader()
    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = loader.load_file("data/skills/恶意逃离.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=True, team="A")

    escapes = [m for m in result.journal if isinstance(m, Escape)]
    assert len(escapes) == 1, f"Expected 1 Escape mutation, got {len(escapes)}"
    assert escapes[0].inherit is False
    print(f"  恶意逃离: Escape mutation produced, inherit={escapes[0].inherit}")


# ═══════════════════════════════════════════════════════════════
# Counter firing tests
# ═══════════════════════════════════════════════════════════════

def test_counter_register_production():
    """Test that a skill with count opcode produces CounterRegister mutation."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import CounterRegister

    ctx = Ctx(skill_type_self="物攻", element_self="虫")
    effects = [
        {"op": "mod", "target": "skill_off_0", "stat": "combo", "value": 1},
        {
            "op": "count",
            "when": {"cond": "devotion_triggered"},
            "then": [
                {"op": "mod", "target": "skill_off_0", "stat": "energy_cost",
                 "value": 1, "mode": "add", "scope": "permanent"}
            ]
        },
    ]
    journal = vm_execute(ctx, effects)
    counters = [m for m in journal if isinstance(m, CounterRegister)]
    assert len(counters) == 1, f"Expected 1 CounterRegister, got {len(counters)}"
    assert counters[0].cond == {"cond": "devotion_triggered"}
    assert len(counters[0].then) == 1
    print(f"  CounterRegister: cond={counters[0].cond}, then={counters[0].then}")


def test_counter_register_engine_integration():
    """Test that execute_skill registers counters from journal into engine."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats
    from scripts.vm.journal import CounterRegister

    loader = SkillLoader()
    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = loader.load_file("data/skills/啃咬.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=True, team="A")

    # Should have CounterRegister in journal
    counters = [m for m in result.journal if isinstance(m, CounterRegister)]
    assert len(counters) == 1, f"Expected 1 CounterRegister in journal, got {len(counters)}"
    # Should be registered in the engine's observer registry
    assert len(engine.registry) >= 1, f"Expected >=1 observers registered, got {len(engine.registry)}"
    print(f"  Engine registry has {len(engine.registry)} observers after executing 啃咬")


def test_counter_fires_on_condition():
    """Test that a registered counter fires when its condition becomes true."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute, process_effects
    from scripts.vm.journal import CounterRegister, ModifierInjection
    from scripts.engine.battle import BattleVMEngine
    from scripts.engine.observer import ObserverRegistry, Observer

    # Setup: register a counter that modifies energy_cost on devotion_triggered
    registry = ObserverRegistry()
    engine = BattleVMEngine(registry)

    # Register counter manually (simulating what execute_skill does)
    engine.register_counter(CounterRegister(
        name="test_counter",
        cond={"cond": "devotion_triggered"},
        then=[{"op": "mod", "target": "skill_off_0", "stat": "energy_cost",
               "value": 2, "mode": "add", "scope": "permanent"}],
        scope="persistent",
    ))
    assert len(registry) == 1

    # Now fire with a ctx where devotion_triggered=True
    ctx = Ctx(
        element_self="虫", skill_type_self="物攻",
        devotion_triggered=True,
    )
    mutations = registry.fire("post_skill", ctx)
    assert len(mutations) > 0, f"Counter should fire when condition is met"
    mods = [m for m in mutations if isinstance(m, ModifierInjection)]
    assert len(mods) == 1
    assert mods[0].stat == "energy_cost"
    assert mods[0].value == 2
    print(f"  Counter fired: stat={mods[0].stat}, value={mods[0].value}")


def test_counter_does_not_fire_without_condition():
    """Test that a registered counter does NOT fire when condition is false."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.journal import CounterRegister
    from scripts.engine.battle import BattleVMEngine
    from scripts.engine.observer import ObserverRegistry

    registry = ObserverRegistry()
    engine = BattleVMEngine(registry)

    engine.register_counter(CounterRegister(
        name="test_counter",
        cond={"cond": "devotion_triggered"},
        then=[{"op": "mod", "target": "skill_off_0", "stat": "energy_cost",
               "value": 2, "mode": "add", "scope": "permanent"}],
        scope="persistent",
    ))

    # Fire with devotion_triggered=False
    ctx = Ctx(element_self="虫", skill_type_self="物攻", devotion_triggered=False)
    mutations = registry.fire("post_skill", ctx)
    assert len(mutations) == 0, f"Counter should NOT fire when condition false, got {len(mutations)}"
    print(f"  Counter correctly stayed silent when condition false")


def test_named_counter_value_tracking():
    """Test that named counters track their values and are queryable via counter_values."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.journal import CounterRegister, Mutation
    from scripts.engine.battle import BattleVMEngine
    from scripts.engine.observer import ObserverRegistry

    registry = ObserverRegistry()
    engine = BattleVMEngine(registry)
    # Initialize counter_values dict on engine
    engine._counter_values = {}

    engine.register_counter(CounterRegister(
        name="星陨",
        cond={"cond": "skill_use"},
        then=[],
        scope="persistent",
    ))

    # Simulate counter firing twice
    engine._increment_counter("星陨")
    engine._increment_counter("星陨")
    assert engine._counter_values["星陨"] == 2

    # Also test unnamed counter
    engine.register_counter(CounterRegister(
        name=None,
        cond={"cond": "on_damage_taken"},
        then=[{"op": "mod", "stat": "atk", "steps": 1}],
        scope="battlefield",
    ))
    engine._increment_counter(None)  # should not crash
    print(f"  Counter values: {engine._counter_values}")


# ═══════════════════════════════════════════════════════════════
# Borrow tests
# ═══════════════════════════════════════════════════════════════

def test_borrow_mutation_production():
    """Test that borrow opcode produces a Borrow mutation."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Borrow

    ctx = Ctx(element_self="光", skill_type_self="防御")
    effects = [{"op": "borrow", "from": "skill_opp_current"}]
    journal = vm_execute(ctx, effects)
    borrows = [m for m in journal if isinstance(m, Borrow)]
    assert len(borrows) == 1
    assert borrows[0].from_skill == "skill_opp_current"
    print(f"  Borrow mutation: from={borrows[0].from_skill}")


def test_borrow_skill_substitution():
    """Test that borrow replaces skill properties with opponent's skill."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Borrow, Damage
    from scripts.engine.battle import BattleVMEngine

    engine = BattleVMEngine()

    # Simulate: defense skill counters attack, borrows opponent's skill
    # Self skill: 防御, power=0, element=光
    # Opp skill:  物攻, power=120, element=火
    ctx = Ctx(
        power_self=0, element_self="光", skill_type_self="防御",
        power_opp=120, element_opp="火", skill_type_opp="物攻",
        energy_cost_self=1, atk_self=120, def_opp=100,
        atk_opp=100, def_self=100, sp_atk_self=100, sp_def_opp=100,
        sp_atk_opp=100, sp_def_self=100,
        combo_self=1, damage_reduction_opp=0.0,
        stat_stages_self={}, stat_stages_opp={},
    )
    effects = [{"op": "borrow", "from": "skill_opp_current"}]
    journal = vm_execute(ctx, effects)

    # Apply borrow substitution — returns journal with Damage
    new_journal = engine._apply_borrow(journal, ctx)
    damages = [m for m in new_journal if isinstance(m, Damage)]
    assert len(damages) == 1, f"Expected 1 Damage from borrowed skill, got {len(damages)}"
    dmg = damages[0]
    assert dmg.element == "火", f"Borrow should use opp element 火, got {dmg.element}"
    assert dmg.type == "物攻", f"Borrow should use opp type 物攻, got {dmg.type}"
    assert dmg.amount > 0, f"Borrow should deal damage with borrowed power 120"
    print(f"  Borrow: damage={dmg.amount}, type={dmg.type}, element={dmg.element}")
    print(f"  Borrow damage dealt: {dmg.amount}")


def test_borrow_e2e_mirror():
    """End-to-end: counter defense that borrows attacker's skill (镜像反射 pattern)."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats
    from scripts.vm.journal import Damage, Borrow

    loader = SkillLoader()
    engine = BattleVMEngine()
    species_def = SpeciesStats(name="防御者", hp=200, atk=120, def_=120, sp_atk=110, sp_def=110, speed=100)
    species_atk = SpeciesStats(name="攻击者", hp=180, atk=130, def_=80, sp_atk=100, sp_def=80, speed=110)

    def_sprite = Sprite(species=species_def, current_hp=200, max_hp=200, energy=8,
                        initial_stats={"atk": 120, "def": 120, "sp_atk": 110, "sp_def": 110, "speed": 100})
    atk_sprite = Sprite(species=species_atk, current_hp=180, max_hp=180, energy=5,
                        initial_stats={"atk": 130, "def": 80, "sp_atk": 100, "sp_def": 80, "speed": 110})

    record = loader.load_file("data/skills/镜像反射.json")
    # The opponent's skill (being countered): 龙爪 (物攻, power=80, element=龙)
    opp_skill = loader.load_file("data/skills/龙爪.json")

    result = engine.execute_skill(
        def_sprite, atk_sprite, record, opp_skill,
        GlobalEffects(), turn=1, is_first=False, team="B",
        counter_succeeded=True,
    )

    # After _handle_borrow, Borrow is consumed and replaced with Damage
    damages = [m for m in result.journal if isinstance(m, Damage)]
    assert len(damages) >= 1, f"Expected damage from borrowed skill, got {len(damages)} damages, journal={[(type(m).__name__) for m in result.journal]}"
    assert damages[0].element == "龙", f"Borrow should use opp element 龙, got {damages[0].element}"
    print(f"  镜像反射: borrow + {damages[0].amount} damage dealt, opp HP={atk_sprite.current_hp}")


# ═══════════════════════════════════════════════════════════════
# Replay (sprite_self) tests
# ═══════════════════════════════════════════════════════════════

def test_replay_team_burst():
    """Test that replay from team_burst replays registered burst effects."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Replay
    from scripts.engine.battle import BattleVMEngine

    engine = BattleVMEngine()
    # Register burst effects
    engine._burst_effects["A"] = [
        ("龙爪", [{"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 1}]),
        ("火球", [{"op": "mod", "target": "sprite_opp", "stat": "def", "steps": -1}]),
    ]

    ctx = Ctx(element_self="电", skill_type_self="魔攻", atk_self=100, def_self=100,
              sp_atk_self=100, sp_def_self=100, atk_opp=100, def_opp=100,
              sp_atk_opp=100, sp_def_opp=100, stat_stages_self={}, stat_stages_opp={})

    effects = [{"op": "replay", "from": "team_burst", "what": "burst"}]
    journal = vm_execute(ctx, effects)
    journal = engine._handle_replay(journal, "A", ctx)

    replays = [m for m in journal if isinstance(m, Replay)]
    assert len(replays) == 0, "Replay mutations should be consumed by _handle_replay"

    # Should have the burst effects' mutations (atk+1, def-1 = 2 StatChanges)
    from scripts.vm.journal import StatChange
    stat_changes = [m for m in journal if isinstance(m, StatChange)]
    assert len(stat_changes) >= 2, f"Expected >=2 stat changes from burst replay, got {len(stat_changes)}"
    print(f"  Team burst replay: {len(stat_changes)} stat changes produced")


def test_replay_sprite_self_basic():
    """Test replay from sprite_self replays matching historical skills."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Replay
    from scripts.engine.battle import BattleVMEngine

    engine = BattleVMEngine()
    # Track skill history for a sprite
    engine._skill_history["sprite_A"] = [
        ("迅捷攻击", [{"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 1}], {"tag": ""}),
        ("普通防御", [{"op": "mod", "target": "sprite_self", "stat": "def", "steps": 1}], {"tag": ""}),
    ]

    ctx = Ctx(element_self="翼", skill_type_self="状态", atk_self=100, def_self=100,
              sp_atk_self=100, sp_def_self=100, atk_opp=100, def_opp=100,
              sp_atk_opp=100, sp_def_opp=100, stat_stages_self={}, stat_stages_opp={})

    effects = [{"op": "replay", "from": "sprite_self"}]
    journal = vm_execute(ctx, effects)
    journal = engine._handle_replay_sprite_self(journal, "sprite_A", ctx)

    # Both historical skills' effects should execute
    from scripts.vm.journal import StatChange
    stat_changes = [m for m in journal if isinstance(m, StatChange)]
    assert len(stat_changes) == 2, f"Expected 2 stat changes from history replay, got {len(stat_changes)}"
    print(f"  Sprite self replay: {len(stat_changes)} stat changes from history")


def test_replay_sprite_self_with_filter():
    """Test replay from sprite_self with skill_type filter."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Replay, ModifierInjection
    from scripts.engine.battle import BattleVMEngine

    engine = BattleVMEngine()
    engine._skill_history["sprite_A"] = [
        ("迅捷技能A", [{"op": "mod", "target": "skill_off_0", "stat": "power", "value": 10, "mode": "add"}], {"tag": "迅捷"}),
        ("普通技能", [{"op": "mod", "target": "skill_off_0", "stat": "energy_cost", "value": -1, "mode": "add"}], {"tag": ""}),
    ]
    # Tag each skill in the history lookup
    engine._skill_tags["sprite_A"] = {
        "迅捷技能A": "迅捷",
        "普通技能": "",
    }

    ctx = Ctx(element_self="翼", skill_type_self="状态", skill_tag_self="",
              atk_self=100, def_self=100, sp_atk_self=100, sp_def_self=100,
              atk_opp=100, def_opp=100, sp_atk_opp=100, sp_def_opp=100,
              stat_stages_self={}, stat_stages_opp={})

    effects = [{"op": "replay", "from": "sprite_self", "skill_filter": {"tag": "迅捷"}}]
    journal = vm_execute(ctx, effects)
    journal = engine._handle_replay_sprite_self(journal, "sprite_A", ctx)

    # Only 迅捷技能A should be replayed
    from scripts.vm.journal import ModifierInjection
    mods = [m for m in journal if isinstance(m, ModifierInjection)]
    assert len(mods) == 1, f"Expected 1 mod from filtered replay, got {len(mods)}"
    assert mods[0].stat == "power", f"Expected power mod, got {mods[0].stat}"
    print(f"  Filtered replay: 1 power mod from 迅捷 skill")


# ═══════════════════════════════════════════════════════════════
# Interrupt tests
# ═══════════════════════════════════════════════════════════════

def test_interrupt_mutation_production():
    """Test that interrupt opcode produces Interrupt mutation."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Interrupt

    ctx = Ctx(element_self="武", skill_type_self="防御")
    effects = [{"op": "interrupt", "target": "sprite_opp"}]
    journal = vm_execute(ctx, effects)
    interrupts = [m for m in journal if isinstance(m, Interrupt)]
    assert len(interrupts) == 1
    assert interrupts[0].target == "sprite_opp"
    print(f"  Interrupt mutation: target={interrupts[0].target}")


def test_interrupt_sets_sprite_flag():
    """Test that replaying an Interrupt mutation sets the interrupted flag."""
    from scripts.engine.replayer import JournalReplayer
    from scripts.vm.journal import Interrupt
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    species = SpeciesStats(name="test", hp=200, atk=120, def_=100,
                          sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    replayer = JournalReplayer(sprite, opp, GlobalEffects())
    journal = [Interrupt(target="sprite_opp")]
    events = replayer.replay(journal)
    assert opp.interrupted, f"Opp sprite should have interrupted=True"
    print(f"  Interrupt flag: opp.interrupted={opp.interrupted}")


def test_interrupt_e2e_hard_gate():
    """E2E: 硬门 counters attack → interrupt + hit damage."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats
    from scripts.vm.journal import Interrupt, Damage

    loader = SkillLoader()
    engine = BattleVMEngine()
    species_def = SpeciesStats(name="防御者", hp=200, atk=120, def_=120, sp_atk=110, sp_def=110, speed=100)
    species_atk = SpeciesStats(name="攻击者", hp=180, atk=130, def_=80, sp_atk=100, sp_def=80, speed=110)

    def_sprite = Sprite(species=species_def, current_hp=200, max_hp=200, energy=8,
                        initial_stats={"atk": 120, "def": 120, "sp_atk": 110, "sp_def": 110, "speed": 100})
    atk_sprite = Sprite(species=species_atk, current_hp=180, max_hp=180, energy=5,
                        initial_stats={"atk": 130, "def": 80, "sp_atk": 100, "sp_def": 80, "speed": 110})

    record = loader.load_file("data/skills/硬门.json")
    opp_skill = loader.load_file("data/skills/龙爪.json")

    result = engine.execute_skill(
        def_sprite, atk_sprite, record, opp_skill,
        GlobalEffects(), turn=1, is_first=False, team="B",
        counter_succeeded=True,
    )

    # Should have Interrupt + Damage
    interrupts = [m for m in result.journal if isinstance(m, Interrupt)]
    assert len(interrupts) == 1, f"Expected 1 Interrupt, got {len(interrupts)}"
    damages = [m for m in result.journal if isinstance(m, Damage)]
    assert len(damages) == 1, f"Expected 1 Damage from hit"
    # Attacker should be interrupted
    assert atk_sprite.interrupted, f"Attacker should be interrupted"
    print(f"  硬门: interrupt + {damages[0].amount} damage, opp.interrupted={atk_sprite.interrupted}")


# ═══════════════════════════════════════════════════════════════
# Lock tests
# ═══════════════════════════════════════════════════════════════

def test_lock_mutation_production():
    """Test that lock opcode produces Lock mutation."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Lock

    ctx = Ctx(element_self="地", skill_type_self="状态")
    effects = [{"op": "lock", "target": "sprite_opp", "turns": 3}]
    journal = vm_execute(ctx, effects)
    locks = [m for m in journal if isinstance(m, Lock)]
    assert len(locks) == 1
    assert locks[0].turns == 3
    print(f"  Lock mutation: target={locks[0].target}, turns={locks[0].turns}")


def test_lock_sets_sprite_flag():
    """Test that replaying a Lock mutation prevents switching."""
    from scripts.engine.replayer import JournalReplayer
    from scripts.vm.journal import Lock
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    species = SpeciesStats(name="test", hp=200, atk=120, def_=100,
                          sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    replayer = JournalReplayer(sprite, opp, GlobalEffects())
    journal = [Lock(target="sprite_opp", turns=3)]
    events = replayer.replay(journal)
    assert opp.locked_turns == 3, f"Opp should have lock_turns=3, got {opp.locked_turns}"
    print(f"  Lock flag: opp.locked_turns={opp.locked_turns}")


def test_lock_e2e_quicksand():
    """E2E: 流沙 locks enemy for 3 turns."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats
    from scripts.vm.journal import Lock

    loader = SkillLoader()
    engine = BattleVMEngine()
    species = SpeciesStats(name="test", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = loader.load_file("data/skills/流沙.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=True, team="A")

    locks = [m for m in result.journal if isinstance(m, Lock)]
    assert len(locks) == 1, f"Expected 1 Lock mutation, got {len(locks)}"
    assert locks[0].turns == 3
    assert opp.locked_turns == 3, f"Opp should be locked for 3 turns, got {opp.locked_turns}"
    print(f"  流沙: lock {opp.locked_turns}t, opp locked")


# ═══════════════════════════════════════════════════════════════
# Redirect tests
# ═══════════════════════════════════════════════════════════════

def test_redirect_mutation_production():
    """Test that redirect opcode produces Redirect mutation."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Redirect

    ctx = Ctx(element_self="恶", skill_type_self="物攻")
    effects = [{"op": "redirect", "target": "sprite_self"}]
    journal = vm_execute(ctx, effects)
    redirects = [m for m in journal if isinstance(m, Redirect)]
    assert len(redirects) == 1
    assert redirects[0].target == "sprite_self"
    print(f"  Redirect mutation: target={redirects[0].target}")


def test_redirect_damage_target():
    """Test that redirect changes Damage target from opp to self."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.journal import Damage, Redirect
    from scripts.engine.battle import BattleVMEngine

    engine = BattleVMEngine()
    journal = [
        Redirect(target="sprite_self"),
        Damage(target="sprite_opp", amount=50, element="恶", type="物攻"),
    ]
    result = engine._handle_redirect(journal)
    damages = [m for m in result if isinstance(m, Damage)]
    assert damages[0].target == "sprite_self", f"Expected damage redirected to self, got {damages[0].target}"
    print(f"  Redirect: damage target changed to {damages[0].target}")


# ═══════════════════════════════════════════════════════════════
# Exchange tests
# ═══════════════════════════════════════════════════════════════

def test_exchange_adjacent_skills():
    """Test that exchange(adjacent_skills) swaps skill positions."""
    from scripts.vm.ctx import Ctx
    from scripts.vm.executor import execute as vm_execute
    from scripts.vm.journal import Exchange
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    loader = SkillLoader()
    engine = BattleVMEngine()
    species = SpeciesStats(name="test", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = loader.load_file("data/skills/杠杆置换.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=True, team="A")

    exchanges = [m for m in result.journal if isinstance(m, Exchange)]
    assert len(exchanges) == 1
    assert exchanges[0].what == "adjacent_skills"
    print(f"  杠杆置换: exchange what={exchanges[0].what}, events={result.events}")


# ═══════════════════════════════════════════════════════════════
# Steal tests
# ═══════════════════════════════════════════════════════════════

def test_steal_energy():
    """Test that steal(energy) transfers energy from target to self."""
    from scripts.engine.replayer import JournalReplayer
    from scripts.vm.journal import Steal
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats

    species = SpeciesStats(name="test", hp=200, atk=120, def_=100,
                          sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    replayer = JournalReplayer(sprite, opp, GlobalEffects())
    journal = [Steal(from_target="sprite_opp", what="energy", amount=3)]
    events = replayer.replay(journal)
    assert opp.energy == 2, f"Opp should have 2 energy (5-3), got {opp.energy}"
    assert sprite.energy == 10, f"Sprite should have 10 energy (8+3 capped at max 10), got {sprite.energy}"
    print(f"  Steal energy: sprite={sprite.energy}E, opp={opp.energy}E")


# ═══════════════════════════════════════════════════════════════
# use_devotion tests
# ═══════════════════════════════════════════════════════════════

def test_use_devotion_flag():
    """Test that use_devotion flag is read from skill JSON."""
    from scripts.engine.skill_loader import SkillLoader

    loader = SkillLoader()
    # 啃咬 has use_devotion: true
    record = loader.load_file("data/skills/啃咬.json")
    assert record.use_devotion is True, f"啃咬 should have use_devotion=True, got {record.use_devotion}"
    print(f"  啃咬 use_devotion={record.use_devotion}")


def test_use_devotion_e2e():
    """E2E: 啃咬 with use_devotion=true triggers devotion effects."""
    from scripts.engine.skill_loader import SkillLoader
    from scripts.engine.battle import BattleVMEngine
    from scripts.sim.sprite import Sprite
    from scripts.sim.globals import GlobalEffects
    from scripts.common.models import SpeciesStats
    from scripts.vm.journal import CounterRegister

    loader = SkillLoader()
    engine = BattleVMEngine()
    species = SpeciesStats(name="test", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = loader.load_file("data/skills/啃咬.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(),
                                   turn=1, is_first=True, team="A",
                                   devotion_triggered=True)

    # Counter should fire on devotion_triggered
    counters = [m for m in result.journal if isinstance(m, CounterRegister)]
    assert len(counters) == 1, "啃咬 should register a counter"
    # Engine should have the counter registered
    assert len(engine.registry) >= 1
    print(f"  啃咬: use_devotion=True, devotion_triggered={result.ctx.devotion_triggered}")


if __name__ == "__main__":
    test_snapshot()
    test_replayer()
    test_modifier_collection()
    test_modifier_chain()
    test_modifier_collection_end_to_end()
    test_life_drain()
    test_counter_damage_flow()
    test_counter_succeeded_flag_flow()
    test_counter_refs_opp_power()
    test_escape_mutation_production()
    test_return_mutation_production()
    test_e2e_attack_skill()
    test_e2e_defense_counter()
    test_e2e_escape_skill()
    # Counter firing
    test_counter_register_production()
    test_counter_register_engine_integration()
    test_counter_fires_on_condition()
    test_counter_does_not_fire_without_condition()
    test_named_counter_value_tracking()
    # Borrow
    test_borrow_mutation_production()
    test_borrow_skill_substitution()
    test_borrow_e2e_mirror()
    # Replay
    test_replay_team_burst()
    test_replay_sprite_self_basic()
    test_replay_sprite_self_with_filter()
    # Interrupt
    test_interrupt_mutation_production()
    test_interrupt_sets_sprite_flag()
    test_interrupt_e2e_hard_gate()
    # Lock
    test_lock_mutation_production()
    test_lock_sets_sprite_flag()
    test_lock_e2e_quicksand()
    # Redirect
    test_redirect_mutation_production()
    test_redirect_damage_target()
    # Exchange
    test_exchange_adjacent_skills()
    # Steal
    test_steal_energy()
    # use_devotion
    test_use_devotion_flag()
    test_use_devotion_e2e()
    print("\nAll engine integration tests passed!")
