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
    print("\nAll engine integration tests passed!")
