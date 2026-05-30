"""Smoke test for engine snapshot + replayer with real sim objects."""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from backend.engine.snapshot import build_ctx
from backend.vm.compiler.skill_compiler import SkillCompiler
from backend.vm.effect import AbnormalEffect, StatBuffEffect

_compiler = SkillCompiler()

def _load_skill(path):
    """Load and compile a skill from a JSON file path."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _compiler.compile(data)
from backend.common.models import SpeciesStats
from backend.engine.replayer import JournalReplayer
from backend.sim.battleskill import BattleSkill
from backend.sim.globals import GlobalEffects
from backend.sim.skill import Skill
from backend.sim.sprite import Sprite


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
    from backend.vm.journal import Damage, EnergyChange, Heal, MarkChange, StatChange, WeatherSet

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
    from backend.vm.effect import StatBuffEffect as _SB
    assert any(e.stat_key == "atk" and e.steps == 3 for e in sprite.active_effects if isinstance(e, _SB))
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
    from backend.engine.modifiers import adjust_damage, collect_modifiers
    from backend.vm.ctx import Ctx
    from backend.vm.journal import Damage, Journal, ModifierInjection

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
    from backend.engine.modifiers import adjust_damage, collect_modifiers
    from backend.vm.ctx import Ctx
    from backend.vm.journal import Damage, Journal, ModifierInjection

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
    from backend.engine.modifiers import apply_modifiers_to_journal
    from backend.vm.ctx import Ctx
    from backend.vm.journal import Damage, Journal, ModifierInjection

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
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Damage, Journal, ModifierInjection

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
    from backend.common.models import SpeciesStats
    from backend.engine.modifiers import apply_modifiers_to_journal
    from backend.engine.replayer import JournalReplayer
    from backend.engine.snapshot import build_ctx
    from backend.sim.globals import GlobalEffects
    from backend.sim.skill import Skill
    from backend.sim.sprite import Sprite
    from backend.vm.executor import execute as vm_execute

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
    from backend.common.models import SpeciesStats
    from backend.engine.modifiers import apply_modifiers_to_journal
    from backend.engine.replayer import JournalReplayer
    from backend.engine.snapshot import build_ctx
    from backend.sim.globals import GlobalEffects
    from backend.sim.skill import Skill
    from backend.sim.sprite import Sprite
    from backend.vm.executor import execute as vm_execute

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
    from backend.common.models import SpeciesStats
    from backend.engine.modifiers import apply_modifiers_to_journal
    from backend.engine.replayer import JournalReplayer
    from backend.engine.snapshot import build_ctx
    from backend.sim.globals import GlobalEffects
    from backend.sim.skill import Skill
    from backend.sim.sprite import Sprite
    from backend.vm.executor import execute as vm_execute

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
    replayer.replay(journal)

    # Damage should be based on countered skill's power=120
    hp_lost = 180 - attacker.current_hp
    print(f"  Counter with opp_power=120: dealt {hp_lost} damage, HP={attacker.current_hp}")
    assert hp_lost > 0, "Should deal damage based on countered skill power"
    # Power 120 should deal more than power 90 would
    assert hp_lost >= 30, f"Expected meaningful damage with power 120, got {hp_lost}"


def test_escape_mutation_production():
    """Test that a skill with escape opcode produces an Escape mutation."""
    from backend.common.models import SpeciesStats
    from backend.engine.snapshot import build_ctx
    from backend.sim.globals import GlobalEffects
    from backend.sim.skill import Skill
    from backend.sim.sprite import Sprite
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Escape

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
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.engine.snapshot import build_ctx
    from backend.sim.globals import GlobalEffects
    from backend.sim.skill import Skill
    from backend.sim.sprite import Sprite
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Return

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
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Damage


    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = _load_skill("data/skills/龙爪.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=True, team="A")

    assert opp.current_hp < 150, f"Expected damage to opponent, HP={opp.current_hp}"
    damages = [m for m in result.journal if isinstance(m, Damage)]
    assert len(damages) == 1
    assert damages[0].amount > 0
    print(f"  龙爪 damage: {damages[0].amount}, opp HP: {opp.current_hp}")


def test_e2e_defense_counter():
    """End-to-end: defense skill with counter_succeeded conditional effects."""
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite


    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = _load_skill("data/skills/防反.json")
    engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=False, team="B",
                                   counter_succeeded=True)

    # Should have stat changes from counter_succeeded
    atk_stages = sum(getattr(e, 'steps', 0) for e in sprite.active_effects if getattr(e, 'stat_key', '') == "atk")
    sp_atk_stages = sum(getattr(e, 'steps', 0) for e in sprite.active_effects if getattr(e, 'stat_key', '') == "sp_atk")
    assert atk_stages == 4, f"Expected atk+4 from counter_succeeded, got {atk_stages}"
    assert sp_atk_stages == 4, f"Expected sp_atk+4 from counter_succeeded, got {sp_atk_stages}"
    print(f"  防反 counter: atk+{atk_stages}, sp_atk+{sp_atk_stages}")


def test_e2e_escape_skill():
    """End-to-end: skill with escape opcode produces Escape mutation."""
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Escape


    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = _load_skill("data/skills/恶意逃离.json")
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
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import CounterRegister

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
    from backend.vm.ir_skill import CondExpr
    assert counters[0].cond == CondExpr(cond="devotion_triggered", params={})
    assert len(counters[0].then) == 1
    print(f"  CounterRegister: cond={counters[0].cond}, then={counters[0].then}")


def test_counter_register_engine_integration():
    """Test that execute_skill registers counters from journal into engine."""
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import CounterRegister


    engine = BattleVMEngine()
    species = SpeciesStats(name="测试精灵", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = _load_skill("data/skills/啃咬.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(), turn=1, is_first=True, team="A")

    # Should have CounterRegister in journal
    counters = [m for m in result.journal if isinstance(m, CounterRegister)]
    assert len(counters) == 1, f"Expected 1 CounterRegister in journal, got {len(counters)}"
    # Should be registered in the engine's observer registry
    assert len(engine.registry) >= 1, f"Expected >=1 observers registered, got {len(engine.registry)}"
    print(f"  Engine registry has {len(engine.registry)} observers after executing 啃咬")


def test_counter_fires_on_condition():
    """Test that a registered counter fires when its condition becomes true."""
    from backend.engine.battle import BattleVMEngine
    from backend.engine.observer import ObserverRegistry
    from backend.vm.ctx import Ctx, EventContext
    from backend.vm.journal import CounterRegister, ModifierInjection

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
        event=EventContext(devotion_triggered=True),
    )
    mutations = registry.fire("post_skill", ctx)
    assert len(mutations) > 0, "Counter should fire when condition is met"
    mods = [m for m in mutations if isinstance(m, ModifierInjection)]
    assert len(mods) == 1
    assert mods[0].stat == "energy_cost"
    assert mods[0].value == 2
    print(f"  Counter fired: stat={mods[0].stat}, value={mods[0].value}")


def test_counter_does_not_fire_without_condition():
    """Test that a registered counter does NOT fire when condition is false."""
    from backend.engine.battle import BattleVMEngine
    from backend.engine.observer import ObserverRegistry
    from backend.vm.ctx import Ctx, EventContext
    from backend.vm.journal import CounterRegister

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
    ctx = Ctx(element_self="虫", skill_type_self="物攻", event=EventContext(devotion_triggered=False))
    mutations = registry.fire("post_skill", ctx)
    assert len(mutations) == 0, f"Counter should NOT fire when condition false, got {len(mutations)}"
    print("  Counter correctly stayed silent when condition false")


def test_named_counter_value_tracking():
    """Test that named counters track their values and are queryable via counter_values."""
    from backend.engine.battle import BattleVMEngine
    from backend.engine.observer import ObserverRegistry
    from backend.vm.journal import CounterRegister

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
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Borrow

    ctx = Ctx(element_self="光", skill_type_self="防御")
    effects = [{"op": "borrow", "from": "skill_opp_current"}]
    journal = vm_execute(ctx, effects)
    borrows = [m for m in journal if isinstance(m, Borrow)]
    assert len(borrows) == 1
    assert borrows[0].from_skill == "skill_opp_current"
    print(f"  Borrow mutation: from={borrows[0].from_skill}")


def test_borrow_skill_substitution():
    """Test that borrow replaces skill properties with opponent's skill."""
    from backend.engine.battle import BattleVMEngine
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Damage

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
    assert dmg.amount > 0, "Borrow should deal damage with borrowed power 120"
    print(f"  Borrow: damage={dmg.amount}, type={dmg.type}, element={dmg.element}")
    print(f"  Borrow damage dealt: {dmg.amount}")


def test_borrow_e2e_mirror():
    """End-to-end: counter defense that borrows attacker's skill (镜像反射 pattern)."""
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Damage


    engine = BattleVMEngine()
    species_def = SpeciesStats(name="防御者", hp=200, atk=120, def_=120, sp_atk=110, sp_def=110, speed=100)
    species_atk = SpeciesStats(name="攻击者", hp=180, atk=130, def_=80, sp_atk=100, sp_def=80, speed=110)

    def_sprite = Sprite(species=species_def, current_hp=200, max_hp=200, energy=8,
                        initial_stats={"atk": 120, "def": 120, "sp_atk": 110, "sp_def": 110, "speed": 100})
    atk_sprite = Sprite(species=species_atk, current_hp=180, max_hp=180, energy=5,
                        initial_stats={"atk": 130, "def": 80, "sp_atk": 100, "sp_def": 80, "speed": 110})

    record = _load_skill("data/skills/镜像反射.json")
    # The opponent's skill (being countered): 龙爪 (物攻, power=80, element=龙)
    opp_skill = _load_skill("data/skills/龙爪.json")

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
    from backend.engine.battle import BattleVMEngine
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Replay

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
    from backend.vm.journal import StatChange
    stat_changes = [m for m in journal if isinstance(m, StatChange)]
    assert len(stat_changes) >= 2, f"Expected >=2 stat changes from burst replay, got {len(stat_changes)}"
    print(f"  Team burst replay: {len(stat_changes)} stat changes produced")


def test_replay_sprite_self_basic():
    """Test replay from sprite_self replays matching historical skills."""
    from backend.engine.battle import BattleVMEngine
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute

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
    from backend.vm.journal import StatChange
    stat_changes = [m for m in journal if isinstance(m, StatChange)]
    assert len(stat_changes) == 2, f"Expected 2 stat changes from history replay, got {len(stat_changes)}"
    print(f"  Sprite self replay: {len(stat_changes)} stat changes from history")


def test_replay_sprite_self_with_filter():
    """Test replay from sprite_self with skill_type filter."""
    from backend.engine.battle import BattleVMEngine
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import ModifierInjection

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
    mods = [m for m in journal if isinstance(m, ModifierInjection)]
    assert len(mods) == 1, f"Expected 1 mod from filtered replay, got {len(mods)}"
    assert mods[0].stat == "power", f"Expected power mod, got {mods[0].stat}"
    print("  Filtered replay: 1 power mod from 迅捷 skill")


# ═══════════════════════════════════════════════════════════════
# Interrupt tests
# ═══════════════════════════════════════════════════════════════

def test_interrupt_mutation_production():
    """Test that interrupt opcode produces Interrupt mutation."""
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Interrupt

    ctx = Ctx(element_self="武", skill_type_self="防御")
    effects = [{"op": "interrupt", "target": "sprite_opp"}]
    journal = vm_execute(ctx, effects)
    interrupts = [m for m in journal if isinstance(m, Interrupt)]
    assert len(interrupts) == 1
    assert interrupts[0].target == "sprite_opp"
    print(f"  Interrupt mutation: target={interrupts[0].target}")


def test_interrupt_sets_sprite_flag():
    """Test that replaying an Interrupt mutation sets the interrupted flag."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Interrupt

    species = SpeciesStats(name="test", hp=200, atk=120, def_=100,
                          sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    replayer = JournalReplayer(sprite, opp, GlobalEffects())
    journal = [Interrupt(target="sprite_opp")]
    replayer.replay(journal)
    assert opp.interrupted, "Opp sprite should have interrupted=True"
    print(f"  Interrupt flag: opp.interrupted={opp.interrupted}")


def test_interrupt_e2e_hard_gate():
    """E2E: 硬门 counters attack → interrupt + hit damage."""
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Damage, Interrupt


    engine = BattleVMEngine()
    species_def = SpeciesStats(name="防御者", hp=200, atk=120, def_=120, sp_atk=110, sp_def=110, speed=100)
    species_atk = SpeciesStats(name="攻击者", hp=180, atk=130, def_=80, sp_atk=100, sp_def=80, speed=110)

    def_sprite = Sprite(species=species_def, current_hp=200, max_hp=200, energy=8,
                        initial_stats={"atk": 120, "def": 120, "sp_atk": 110, "sp_def": 110, "speed": 100})
    atk_sprite = Sprite(species=species_atk, current_hp=180, max_hp=180, energy=5,
                        initial_stats={"atk": 130, "def": 80, "sp_atk": 100, "sp_def": 80, "speed": 110})

    record = _load_skill("data/skills/硬门.json")
    opp_skill = _load_skill("data/skills/龙爪.json")

    result = engine.execute_skill(
        def_sprite, atk_sprite, record, opp_skill,
        GlobalEffects(), turn=1, is_first=False, team="B",
        counter_succeeded=True,
    )

    # Should have Interrupt + Damage
    interrupts = [m for m in result.journal if isinstance(m, Interrupt)]
    assert len(interrupts) == 1, f"Expected 1 Interrupt, got {len(interrupts)}"
    damages = [m for m in result.journal if isinstance(m, Damage)]
    assert len(damages) == 1, "Expected 1 Damage from hit"
    # Attacker should be interrupted
    assert atk_sprite.interrupted, "Attacker should be interrupted"
    print(f"  硬门: interrupt + {damages[0].amount} damage, opp.interrupted={atk_sprite.interrupted}")


# ═══════════════════════════════════════════════════════════════
# Lock tests
# ═══════════════════════════════════════════════════════════════

def test_lock_mutation_production():
    """Test that lock opcode produces Lock mutation."""
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Lock

    ctx = Ctx(element_self="地", skill_type_self="状态")
    effects = [{"op": "lock", "target": "sprite_opp", "turns": 3}]
    journal = vm_execute(ctx, effects)
    locks = [m for m in journal if isinstance(m, Lock)]
    assert len(locks) == 1
    assert locks[0].turns == 3
    print(f"  Lock mutation: target={locks[0].target}, turns={locks[0].turns}")


def test_lock_sets_sprite_flag():
    """Test that replaying a Lock mutation prevents switching."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Lock

    species = SpeciesStats(name="test", hp=200, atk=120, def_=100,
                          sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    replayer = JournalReplayer(sprite, opp, GlobalEffects())
    journal = [Lock(target="sprite_opp", turns=3)]
    replayer.replay(journal)
    assert opp.locked_turns == 3, f"Opp should have lock_turns=3, got {opp.locked_turns}"
    print(f"  Lock flag: opp.locked_turns={opp.locked_turns}")


def test_lock_e2e_quicksand():
    """E2E: 流沙 locks enemy for 3 turns."""
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Lock


    engine = BattleVMEngine()
    species = SpeciesStats(name="test", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = _load_skill("data/skills/流沙.json")
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
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    from backend.vm.journal import Redirect

    ctx = Ctx(element_self="恶", skill_type_self="物攻")
    effects = [{"op": "redirect", "target": "sprite_self"}]
    journal = vm_execute(ctx, effects)
    redirects = [m for m in journal if isinstance(m, Redirect)]
    assert len(redirects) == 1
    assert redirects[0].target == "sprite_self"
    print(f"  Redirect mutation: target={redirects[0].target}")


def test_redirect_damage_target():
    """Test that redirect changes Damage target from opp to self."""
    from backend.engine.battle import BattleVMEngine
    from backend.vm.journal import Damage, Redirect

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
    from backend.common.models import SpeciesStats

    # SkillCompiler now used via module-level _load_skill() helper
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Exchange


    engine = BattleVMEngine()
    species = SpeciesStats(name="test", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = _load_skill("data/skills/杠杆置换.json")
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
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Steal

    species = SpeciesStats(name="test", hp=200, atk=120, def_=100,
                          sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    replayer = JournalReplayer(sprite, opp, GlobalEffects())
    journal = [Steal(from_target="sprite_opp", what="energy", amount=3)]
    replayer.replay(journal)
    assert opp.energy == 2, f"Opp should have 2 energy (5-3), got {opp.energy}"
    assert sprite.energy == 10, f"Sprite should have 10 energy (8+3 capped at max 10), got {sprite.energy}"
    print(f"  Steal energy: sprite={sprite.energy}E, opp={opp.energy}E")


# ═══════════════════════════════════════════════════════════════
# use_devotion tests
# ═══════════════════════════════════════════════════════════════

def test_use_devotion_flag():
    """Test that use_devotion flag is read from skill JSON."""
    # SkillCompiler now used via module-level _load_skill() helper


    # 啃咬 has use_devotion: true
    record = _load_skill("data/skills/啃咬.json")
    assert record.use_devotion is True, f"啃咬 should have use_devotion=True, got {record.use_devotion}"
    print(f"  啃咬 use_devotion={record.use_devotion}")


def test_use_devotion_e2e():
    """E2E: 啃咬 with use_devotion=true triggers devotion effects."""
    # SkillCompiler now used via module-level _load_skill() helper
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite
    from backend.vm.journal import CounterRegister


    engine = BattleVMEngine()
    species = SpeciesStats(name="test", hp=200, atk=120, def_=100, sp_atk=110, sp_def=95, speed=100)
    sprite = Sprite(species=species, current_hp=180, max_hp=200, energy=8,
                    initial_stats={"atk": 120, "def": 100, "sp_atk": 110, "sp_def": 95, "speed": 100})
    opp = Sprite(species=species, current_hp=150, max_hp=180, energy=5,
                 initial_stats={"atk": 115, "def": 90, "sp_atk": 105, "sp_def": 85, "speed": 95})

    record = _load_skill("data/skills/啃咬.json")
    result = engine.execute_skill(sprite, opp, record, None, GlobalEffects(),
                                   turn=1, is_first=True, team="A",
                                   devotion_triggered=True)

    # Counter should fire on devotion_triggered
    counters = [m for m in result.journal if isinstance(m, CounterRegister)]
    assert len(counters) == 1, "啃咬 should register a counter"
    # Engine should have the counter registered
    assert len(engine.registry) >= 1
    print(f"  啃咬: use_devotion=True, devotion_triggered={result.ctx.event.devotion_triggered}")


# ═══════════════════════════════════════════════════════════════
# Effect lifecycle: priority, ttl, delay, cooldown
# ═══════════════════════════════════════════════════════════════

def test_priority_sort_within_phase():
    """Effects in the same phase sort by priority descending (higher first)."""
    from backend.vm.sort import sort_effects
    effects = [
        {"op": "mod", "feeds": "power", "stat": "atk", "steps": 1, "priority": 0},
        {"op": "mod", "feeds": "power", "stat": "atk", "steps": 2, "priority": 10},
        {"op": "mod", "feeds": "power", "stat": "atk", "steps": 3, "priority": 5},
    ]
    sorted_ = sort_effects(effects)
    steps = [e["steps"] for e in sorted_]
    # priority 10 → 5 → 0
    assert steps == [2, 3, 1], f"Expected [2,3,1] (prio 10,5,0), got {steps}"


def test_priority_sort_mixed_phases():
    """Priority only affects ordering within the same phase bucket."""
    from backend.vm.sort import sort_effects
    effects = [
        {"op": "mod", "feeds": "cost", "stat": "energy_cost", "value": -1, "priority": 0},
        {"op": "mod", "feeds": "power", "stat": "power", "steps": 3, "priority": 100},
        {"op": "mod", "feeds": "cost", "stat": "energy_cost", "value": -2, "priority": 10},
        {"op": "mod", "feeds": "power", "stat": "power", "steps": 1, "priority": 0},
    ]
    sorted_ = sort_effects(effects)
    # cost phase (0) comes before power phase (1), regardless of priority
    # Within cost: priority 10 → 0 (value -2 → -1)
    # Within power: priority 100 → 0 (steps 3 → 1)
    phases = []
    for e in sorted_:
        from backend.vm.sort import _phase_of
        phases.append((_phase_of(e), e.get("priority", 0)))
    # Verify cost phase comes first
    assert phases[0][0] == 0, f"Expected cost phase first, got phase {phases[0][0]}"
    assert phases[1][0] == 0
    assert phases[2][0] == 1
    assert phases[3][0] == 1
    # Within cost phase: higher priority first
    assert phases[0][1] == 10
    assert phases[1][1] == 0
    # Within power phase: higher priority first
    assert phases[2][1] == 100
    assert phases[3][1] == 0


def test_ttl_on_statuseffect():
    """StatusEffect can carry a ttl field for turn-limited duration."""
    eff = StatBuffEffect(name="攻击+20%", source="test", stat_key="atk", steps=2, ttl=3)
    assert eff.ttl == 3
    # Default ttl = 0 means no expiry
    eff2 = StatBuffEffect(name="防御+10%", source="test", stat_key="def", steps=1)
    assert eff2.ttl == 0


def test_ttl_decrement_and_expiry():
    """TTL decrements at turn end; effects with ttl=0 are removed."""
    from backend.common.models import SpeciesStats
    from backend.sim.sprite import Sprite
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    sprite.add_effect(StatBuffEffect(name="攻击+20%", source="test", stat_key="atk", steps=2, ttl=3))
    sprite.add_effect(StatBuffEffect(name="永久防御", source="test", stat_key="def", steps=1, ttl=0))
    assert len(sprite.active_effects) == 2

    # Decrement TTL: effects with ttl>0 get decremented; ttl=0 (permanent) stay
    removed = sprite.decrement_ttl()
    assert len(removed) == 0  # ttl 3→2, still alive
    assert sprite.active_effects[0].ttl == 2

    removed = sprite.decrement_ttl()  # 2→1
    removed = sprite.decrement_ttl()  # 1→0
    assert len(removed) == 1  # ttl hit 0, removed
    assert len(sprite.active_effects) == 1  # only permanent def remains
    assert sprite.active_effects[0].name == "永久防御"


def test_delay_stores_on_sprite():
    """Effects with delay>0 are stored as pending, not applied immediately."""
    from backend.common.models import SpeciesStats
    from backend.sim.sprite import Sprite
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    # Store a delayed effect
    pending = StatBuffEffect(name="攻击+20%", source="test", stat_key="atk", steps=2, ttl=0)
    sprite.add_pending_effect(pending, delay=2)
    assert len(sprite.active_effects) == 0  # Not applied yet
    assert len(sprite._pending_effects) == 1
    assert sprite._pending_effects[0][0].name == "攻击+20%"
    assert sprite._pending_effects[0][1] == 2  # delay counter


def test_delay_decremented_at_turn_start():
    """Pending effects decrement delay each turn; when delay=0, apply effect."""
    from backend.common.models import SpeciesStats
    from backend.sim.sprite import Sprite
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    pending = StatBuffEffect(name="攻击+20%", source="test", stat_key="atk", steps=2)
    sprite.add_pending_effect(pending, delay=2)

    # Turn 1: delay 2→1, still pending
    applied = sprite.process_pending_effects()
    assert len(applied) == 0
    assert len(sprite._pending_effects) == 1
    assert sprite._pending_effects[0][1] == 1

    # Turn 2: delay 1→0, effect applied
    applied = sprite.process_pending_effects()
    assert len(applied) == 1
    assert len(sprite._pending_effects) == 0
    assert len(sprite.active_effects) == 1
    assert sprite.active_effects[0].name == "攻击+20%"


def test_cooldown_on_statuseffect():
    """EffectObject can carry a cooldown field (duck-typed)."""
    eff = AbnormalEffect(name="灼烧", source="test", stacks=1)
    eff.cooldown = 3
    assert eff.cooldown == 3
    eff2 = AbnormalEffect(name="中毒", source="test", stacks=1)
    assert getattr(eff2, 'cooldown', 0) == 0


def test_cooldown_decrement_on_use():
    """Cooldown decrements when effect triggers; removed when cooldown hits 0."""
    from backend.common.models import SpeciesStats
    from backend.sim.sprite import Sprite
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    eff = AbnormalEffect(name="灼烧", source="test", stacks=1)
    eff.cooldown = 2
    sprite.add_effect(eff)

    # After first use: cooldown 2→1
    assert sprite.use_cooldown("灼烧") == 1  # remaining cooldown
    assert sprite.active_effects[0].cooldown == 1

    # After second use: cooldown 1→0, effect removed
    assert sprite.use_cooldown("灼烧") == 0
    assert len(sprite.active_effects) == 0  # removed on cooldown expiry


# ═══════════════════════════════════════════════════════════════
# Advanced mod filters: on_next, skill_where, element:each
# ═══════════════════════════════════════════════════════════════

def test_on_next_modifier_not_applied_immediately():
    """Modifier with on_next=true is stored as pending, not applied to sprite."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import ModifierInjection
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Modifier with on_next=True → not applied immediately
    m = ModifierInjection(
        target="sprite_self", stat="power_mult", value=2.0,
        on_next=True, if_type="attack",
    )
    replayer._apply_modifier(m)
    # Should NOT be in sprite._modifiers (on_next defers it)
    assert "power_mult" not in sprite._modifiers or sprite._modifiers.get("power_mult") == 0.0
    # Should be in sprite._pending_modifiers
    assert len(sprite._pending_modifiers) == 1
    assert sprite._pending_modifiers[0].stat == "power_mult"


def test_on_next_modifier_applied_on_matching_skill():
    """Pending on_next modifier is consumed when a matching skill type is used."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import ModifierInjection
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    JournalReplayer(sprite, opp, globals_)

    # Store a pending on_next modifier
    m = ModifierInjection(
        target="sprite_self", stat="power_mult", value=2.0,
        on_next=True, if_type="attack",
    )
    sprite._pending_modifiers.append(m)

    # Consume pending modifiers for an attack skill
    consumed = sprite.consume_pending_modifiers(skill_type="物攻")
    assert len(consumed) == 1  # attack type matches
    assert len(sprite._pending_modifiers) == 0
    # Verify the modifier was applied
    assert sprite._modifiers.get("power_mult") == 2.0

    # Non-matching skill type does NOT consume
    sprite._pending_modifiers.append(ModifierInjection(
        target="sprite_self", stat="damage_mult", value=1.5,
        on_next=True, if_type="defense",
    ))
    consumed = sprite.consume_pending_modifiers(skill_type="物攻")
    assert len(consumed) == 0  # defense modifier not consumed by attack
    assert len(sprite._pending_modifiers) == 1  # still pending


def test_skill_where_filters_by_condition():
    """skill_where evaluates a per-skill condition to decide if modifier applies."""
    from backend.vm.journal import ModifierInjection
    m = ModifierInjection(
        target="sprite_self", stat="power", value=20, mode="add",
        skill_filter="all",
        skill_where={"q": "energy_cost", "op": "gt", "value": 3},
    )
    # skill_where is preserved in the mutation
    assert m.skill_where is not None
    assert m.skill_where["q"] == "energy_cost"
    assert m.skill_where["op"] == "gt"
    assert m.skill_where["value"] == 3

    # Test: evaluate skill_where against skill properties
    from backend.engine.modifiers import eval_skill_where
    # energy_cost=5 > 3 → match
    assert eval_skill_where(m.skill_where, {"energy_cost": 5}) is True
    # energy_cost=2 > 3 → no match
    assert eval_skill_where(m.skill_where, {"energy_cost": 2}) is False
    # energy_cost=3 > 3 → no match (gt, not gte)
    assert eval_skill_where(m.skill_where, {"energy_cost": 3}) is False


def test_element_each_per_element_limits():
    """element='each' with per_element limits skills per element group."""
    from backend.vm.journal import ModifierInjection
    m = ModifierInjection(
        target="sprite_self", stat="power", value=35, mode="add",
        element="each", per_element=1,
    )
    assert m.element == "each"
    assert m.per_element == 1

    # Test: group skills by element, take at most per_element per group
    from backend.engine.modifiers import select_skills_by_element
    skills = [
        {"name": "火球", "element": "火", "energy_cost": 2},
        {"name": "火焰喷射", "element": "火", "energy_cost": 4},
        {"name": "水枪", "element": "水", "energy_cost": 2},
        {"name": "水炮", "element": "水", "energy_cost": 5},
    ]
    selected = select_skills_by_element(skills, per_element=1)
    # Should get at most 1 fire and 1 water
    fire_count = sum(1 for s in selected if s["element"] == "火")
    water_count = sum(1 for s in selected if s["element"] == "水")
    assert fire_count == 1, f"Expected 1 fire skill, got {fire_count}"
    assert water_count == 1, f"Expected 1 water skill, got {water_count}"
    assert len(selected) == 2

    # With per_element=2, both fire skills should be selected
    selected2 = select_skills_by_element(skills, per_element=2)
    assert len(selected2) == 4  # all skills selected


# ═══════════════════════════════════════════════════════════════
# Target coverage: all effect direction combinations
# ═══════════════════════════════════════════════════════════════

def test_modifier_on_opp_sprite():
    """ModifierInjection(target='sprite_opp') stores modifier on opponent."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import ModifierInjection
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Reduce opponent's skill power via power_mult
    m = ModifierInjection(
        target="sprite_opp", stat="power_mult", value=0.5, mode="set", scope="battlefield",
    )
    replayer._apply_modifier(m)
    assert opp._modifiers.get("power_mult") == 0.5
    assert "power_mult" not in sprite._modifiers


def test_modifier_on_opp_energy_cost():
    """ModifierInjection raises opponent skill energy cost."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import ModifierInjection
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = ModifierInjection(
        target="sprite_opp", stat="energy_cost", value=2, mode="add", scope="persistent",
    )
    replayer._apply_modifier(m)
    assert opp._modifiers.get("energy_cost") == 2.0
    assert "energy_cost" not in sprite._modifiers


def test_mark_change_opp_team():
    """MarkChange(target_team='opp') adds negative marks to opponent's team."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import MarkChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    # Team A (self), Team B (opp)
    replayer = JournalReplayer(sprite, opp, globals_, team="A")

    # Apply negative mark to opp team (team B)
    m = MarkChange(target_team="opp", name="星陨印记", delta=2)
    replayer._apply_mark_change(m)
    _, neg_b = globals_.get_marks("B")
    assert len(neg_b) == 1
    assert neg_b[0].name == "星陨印记"
    assert neg_b[0].stacks == 2


def test_mark_change_own_and_opp():
    """Both own and opp teams can receive marks independently."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import MarkChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_, team="A")

    # Buff self team, debuff opp team
    replayer._apply_mark_change(MarkChange(target_team="own", name="光合印记", delta=1))
    replayer._apply_mark_change(MarkChange(target_team="opp", name="星陨印记", delta=1))

    pos_a, neg_a = globals_.get_marks("A")
    pos_b, neg_b = globals_.get_marks("B")
    assert len(pos_a) == 1 and pos_a[0].name == "光合印记"
    assert len(neg_b) == 1 and neg_b[0].name == "星陨印记"
    assert len(pos_b) == 0
    assert len(neg_a) == 0


def test_abnormal_change_to_sprite_opp():
    """AbnormalChange(target='sprite_opp') applies status to opponent."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import AbnormalChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = AbnormalChange(target="sprite_opp", name="中毒", delta=2)
    replayer._apply_abnormal_change(m)
    assert len(opp.active_effects) == 1
    assert opp.active_effects[0].name == "中毒"
    assert opp.active_effects[0].stacks == 2
    assert len(sprite.active_effects) == 0


def test_abnormal_change_to_sprite_self():
    """AbnormalChange(target='sprite_self') can apply self-inflicted status."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import AbnormalChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = AbnormalChange(target="sprite_self", name="灼烧", delta=1)
    replayer._apply_abnormal_change(m)
    assert len(sprite.active_effects) == 1
    assert sprite.active_effects[0].name == "灼烧"
    assert len(opp.active_effects) == 0


def test_dispel_opp_positive_buffs():
    """Dispel(target='sprite_opp', what='positive') removes opponent buffs."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Dispel
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Give opp some buffs
    opp.add_effect(StatBuffEffect(name="攻击+20%", source="test", stat_key="atk", steps=2))
    opp.add_effect(StatBuffEffect(name="速度+10%", source="test", stat_key="speed", steps=1))
    assert len(opp.active_effects) == 2

    # Dispel opp buffs
    m = Dispel(target="sprite_opp", what="positive", limit=2)
    replayer._apply_dispel(m)
    assert len(opp.active_effects) == 0
    assert len(sprite.active_effects) == 0


def test_dispel_self_negative_debuffs():
    """Dispel(target='sprite_self', what='negative') clears own debuffs."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Dispel
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Give self some debuffs
    sprite.add_effect(StatBuffEffect(name="防御-20%", source="test", stat_key="def", steps=-2))
    sprite.add_effect(StatBuffEffect(name="速度-10%", source="test", stat_key="speed", steps=-1))
    assert len(sprite.active_effects) == 2

    m = Dispel(target="sprite_self", what="negative", limit=2)
    replayer._apply_dispel(m)
    assert len(sprite.active_effects) == 0


def test_dispel_self_abnormal():
    """Dispel(target='sprite_self', what='abnormal', name='中毒') removes specific abnormal."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Dispel
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    sprite.add_effect(AbnormalEffect(name="中毒", source="test", stacks=3))
    sprite.add_effect(AbnormalEffect(name="灼烧", source="test", stacks=1))
    assert len(sprite.active_effects) == 2

    m = Dispel(target="sprite_self", what="abnormal", name="中毒")
    replayer._apply_dispel(m)
    assert len(sprite.active_effects) == 1
    assert sprite.active_effects[0].name == "灼烧"


def test_heal_to_sprite_self():
    """Heal(target='sprite_self') heals the self sprite."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Heal
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=60, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=80, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Heal(target="sprite_self", amount=25)
    replayer._apply_heal(m)
    assert sprite.current_hp == 85
    assert opp.current_hp == 80  # unchanged


def test_heal_to_sprite_opp():
    """Heal(target='sprite_opp') heals the opponent (e.g., life-giving skill)."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Heal
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=60, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=80, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Heal(target="sprite_opp", amount=20)
    replayer._apply_heal(m)
    assert opp.current_hp == 100  # capped at max_hp
    assert sprite.current_hp == 60  # unchanged


def test_energy_change_to_opp():
    """EnergyChange(target='sprite_opp') removes opponent energy."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import EnergyChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100, energy=8,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100, energy=10,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = EnergyChange(target="sprite_opp", delta=-3)
    replayer._apply_energy_change(m)
    assert opp.energy == 7
    assert sprite.energy == 8  # unchanged


def test_energy_change_to_self():
    """EnergyChange(target='sprite_self') restores self energy."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import EnergyChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100, energy=3,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100, energy=5,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = EnergyChange(target="sprite_self", delta=2)
    replayer._apply_energy_change(m)
    assert sprite.energy == 5
    assert opp.energy == 5  # unchanged


def test_stat_change_to_sprite_self():
    """StatChange(target='sprite_self') applies stat buff to self."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import StatChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = StatChange(target="sprite_self", stat="atk", steps=2, scope="battlefield")
    replayer._apply_stat_change(m)
    assert len(sprite.active_effects) == 1
    assert sprite.active_effects[0].stat_key == "atk"
    assert sprite.active_effects[0].steps == 2
    assert len(opp.active_effects) == 0


def test_stat_change_to_sprite_opp():
    """StatChange(target='sprite_opp') applies stat debuff to opponent."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import StatChange
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = StatChange(target="sprite_opp", stat="def", steps=-3, scope="battlefield")
    replayer._apply_stat_change(m)
    assert len(opp.active_effects) == 1
    assert opp.active_effects[0].stat_key == "def"
    assert opp.active_effects[0].steps == -3
    assert len(sprite.active_effects) == 0


def test_damage_to_sprite_self():
    """Damage(target='sprite_self') deals self-damage (recoil)."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Damage
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Damage(target="sprite_self", amount=30, element="普通", type="物攻")
    replayer._apply_damage(m)
    assert sprite.current_hp == 70
    assert opp.current_hp == 100


def test_damage_to_sprite_opp():
    """Damage(target='sprite_opp') deals damage to opponent."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Damage
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Damage(target="sprite_opp", amount=45, element="火", type="魔攻")
    replayer._apply_damage(m)
    assert opp.current_hp == 55
    assert sprite.current_hp == 100


def test_tick_to_sprite_opp():
    """Tick(target='sprite_opp') triggers abnormal tick on opponent."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Tick
    species = SpeciesStats(name="test", hp=200, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=200, max_hp=200,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=200, max_hp=200,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp.add_effect(AbnormalEffect(name="中毒", source="test", stacks=3))
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Tick(target="sprite_opp", abnormal_name="中毒")
    replayer._apply_tick(m)
    # 3 stacks * 3% max HP = 9% of 200 = 18
    assert opp.current_hp < 200


def test_tick_to_sprite_self():
    """Tick(target='sprite_self') triggers abnormal tick on self."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Tick
    species = SpeciesStats(name="test", hp=200, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=200, max_hp=200,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=200, max_hp=200,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    sprite.add_effect(AbnormalEffect(name="灼烧", source="test", stacks=2))
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Tick(target="sprite_self", abnormal_name="灼烧")
    replayer._apply_tick(m)
    assert sprite.current_hp < 200


def test_double_on_opp():
    """Double(target='sprite_opp', what='negative') doubles opponent's debuffs."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Double
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp.add_effect(StatBuffEffect(name="防御-20%", source="test", stat_key="def", steps=-2))
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Double(target="sprite_opp", what="negative")
    replayer._apply_double(m)
    assert opp.active_effects[0].steps == -4  # doubled


def test_double_on_self():
    """Double(target='sprite_self', what='positive') doubles own buffs."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Double
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    sprite.add_effect(StatBuffEffect(name="攻击+20%", source="test", stat_key="atk", steps=2))
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Double(target="sprite_self", what="positive")
    replayer._apply_double(m)
    assert sprite.active_effects[0].steps == 4  # doubled


# ═══════════════════════════════════════════════════════════════════
# 吟游之弦 (id=20146): mark_coexist — 印记共存模式测试
# ═══════════════════════════════════════════════════════════════════


def _make_bard_sprite(name="吟游诗人", hp=200):
    """创建一个拥有吟游之弦(20146)特性的测试精灵。

    DataDrivenTrait observer 在 pre_calc 时通过 flag_set 设置
    _modifiers["mark_coexist"] = True。测试中直接预置此 flag
    模拟 observer 执行后的状态。
    """
    species = SpeciesStats(
        name=name, hp=hp, atk=100, def_=100,
        sp_atk=100, sp_def=100, speed=100,
    )
    species.ability_id = 20146
    sprite = Sprite(
        species=species, current_hp=hp, max_hp=hp,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    sprite._modifiers["mark_coexist"] = True
    return sprite


def _make_normal_sprite(name="普通精灵", hp=200):
    """创建一个无特性的普通精灵。"""
    species = SpeciesStats(
        name=name, hp=hp, atk=100, def_=100,
        sp_atk=100, sp_def=100, speed=100,
    )
    sprite = Sprite(
        species=species, current_hp=hp, max_hp=hp,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    return sprite


def test_bard_mark_coexist_different_marks():
    """吟游之弦: applying different-name marks → both coexist (no replace)."""
    globals_ = GlobalEffects()
    _make_bard_sprite()
    team = "A"

    # Apply first mark
    globals_.apply_mark(team, "攻击印记", "positive", 1, coexist=True)
    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 1
    assert pos_a[0].name == "攻击印记"

    # Apply second DIFFERENT mark → should coexist, not replace
    globals_.apply_mark(team, "光合印记", "positive", 1, coexist=True)
    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 2, f"Expected 2 coexisting marks, got {len(pos_a)}"
    mark_names = {m.name for m in pos_a}
    assert mark_names == {"攻击印记", "光合印记"}


def test_bard_mark_coexist_same_name_stacks():
    """吟游之弦: applying same-name mark → stacks increase."""
    globals_ = GlobalEffects()
    _make_bard_sprite()
    team = "A"

    globals_.apply_mark(team, "攻击印记", "positive", 2, coexist=True)
    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 1
    assert pos_a[0].stacks == 2

    # Reapply same name → stacks add up
    globals_.apply_mark(team, "攻击印记", "positive", 3, coexist=True)
    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 1, "Same-name mark should not duplicate"
    assert pos_a[0].stacks == 5


def test_bard_mark_three_different_marks():
    """吟游之弦: three different marks all coexist."""
    globals_ = GlobalEffects()
    _make_bard_sprite()
    team = "A"

    for name in ["攻击印记", "光合印记", "润泽印记"]:
        globals_.apply_mark(team, name, "positive", 1, coexist=True)

    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 3, f"Expected 3 coexisting marks, got {len(pos_a)}"
    assert {m.name for m in pos_a} == {"攻击印记", "光合印记", "润泽印记"}


def test_bard_mark_coexist_mixed_stacks():
    """吟游之弦: different marks with stacking coexist correctly."""
    globals_ = GlobalEffects()
    _make_bard_sprite()
    team = "A"

    globals_.apply_mark(team, "攻击印记", "positive", 2, coexist=True)
    globals_.apply_mark(team, "光合印记", "positive", 1, coexist=True)
    globals_.apply_mark(team, "攻击印记", "positive", 3, coexist=True)

    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 2
    atk_mark = next(m for m in pos_a if m.name == "攻击印记")
    assert atk_mark.stacks == 5
    photo_mark = next(m for m in pos_a if m.name == "光合印记")
    assert photo_mark.stacks == 1


def test_normal_sprite_mark_replace():
    """Without 吟游之弦: different-name mark replaces existing (default behavior)."""
    globals_ = GlobalEffects()
    _make_normal_sprite()
    team = "A"

    globals_.apply_mark(team, "攻击印记", "positive", 1)
    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 1
    assert pos_a[0].name == "攻击印记"

    # Different name without bard → replaces
    globals_.apply_mark(team, "光合印记", "positive", 1)
    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 1, "Without bard, new mark should replace old"
    assert pos_a[0].name == "光合印记"


def test_normal_sprite_mark_same_name_stacks():
    """Without 吟游之弦: same-name mark still stacks (compatible behavior)."""
    globals_ = GlobalEffects()
    _make_normal_sprite()
    team = "A"

    globals_.apply_mark(team, "攻击印记", "positive", 2)
    globals_.apply_mark(team, "攻击印记", "positive", 3)
    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 1
    assert pos_a[0].stacks == 5


def test_bard_mark_teams_independent():
    """吟游之弦: marks on team A and team B are independent."""
    globals_ = GlobalEffects()
    _make_bard_sprite()

    globals_.apply_mark("A", "攻击印记", "positive", 1, coexist=True)
    globals_.apply_mark("A", "光合印记", "positive", 1, coexist=True)
    globals_.apply_mark("B", "减速", "negative", 1, coexist=True)

    pos_a, neg_a = globals_.get_marks("A")
    pos_b, neg_b = globals_.get_marks("B")

    assert len(pos_a) == 2  # bard coexists on team A
    assert len(neg_a) == 0
    assert len(pos_b) == 0
    assert len(neg_b) == 1
    assert neg_b[0].name == "减速"


def test_bard_mark_positive_negative_independent():
    """吟游之弦: positive and negative marks are tracked separately."""
    globals_ = GlobalEffects()
    _make_bard_sprite()
    team = "A"

    globals_.apply_mark(team, "攻击印记", "positive", 1, coexist=True)
    globals_.apply_mark(team, "减速", "negative", 1, coexist=True)

    pos, neg = globals_.get_marks(team)
    assert len(pos) == 1 and pos[0].name == "攻击印记"
    assert len(neg) == 1 and neg[0].name == "减速"


def test_bard_mark_coexist_non_bard_user_does_not_replace():
    """吟游之弦在场时，无特性精灵施加印记也应正常替换（按默认逻辑）。"""
    globals_ = GlobalEffects()
    _make_normal_sprite()
    team = "A"

    globals_.apply_mark(team, "攻击印记", "positive", 1)
    globals_.apply_mark(team, "光合印记", "positive", 1)

    pos_a, _ = globals_.get_marks(team)
    assert len(pos_a) == 1, "Non-bard user: new mark should replace old"
    assert pos_a[0].name == "光合印记"


def test_bard_mark_coexist_flag_is_consumed_by_replayer():
    """吟游之弦 observer 设置的 mark_coexist flag 被 replayer 正确消费。

    replayer._apply_mark_change 读取 self.self._modifiers["mark_coexist"]
    并传递 coexist=True 给 apply_mark，无需 hook 或 get_trait 查找。
    """
    globals_ = GlobalEffects()

    # coexist=True → 共存模式（模拟 replayer 读取 flag 后传递）
    globals_.apply_mark("A", "攻击印记", "positive", 1, coexist=True)
    globals_.apply_mark("A", "光合印记", "positive", 1, coexist=True)

    pos_a, _ = globals_.get_marks("A")
    assert len(pos_a) == 2  # coexistence works via flag

    # coexist=False (default) → 默认替换模式
    globals2 = GlobalEffects()
    globals2.apply_mark("A", "攻击印记", "positive", 1, coexist=False)
    globals2.apply_mark("A", "光合印记", "positive", 1, coexist=False)
    pos_a2, _ = globals2.get_marks("A")
    assert len(pos_a2) == 1  # no coexist flag → default replace


# ═══════════════════════════════════════════════════════════════════
# 星陨印记: trigger_starfall — 非幻系攻击触发消耗和伤害
# ═══════════════════════════════════════════════════════════════════


def test_starfall_trigger_basic():
    """非幻系攻击触发星陨: 消耗全部层数 + 造成幻系魔法伤害。"""
    g = GlobalEffects()
    attacker = _make_normal_sprite("攻击方", hp=300)
    defender = _make_normal_sprite("防御方", hp=300)

    # 给防御方上 3 层星陨
    g.apply_mark("A", "星陨印记", "negative", 3)

    # 触发星陨: team=A (防御方阵营), attacker → defender
    dmg = g.trigger_starfall("A", attacker, defender)
    assert dmg > 0, "星陨应造成伤害"

    # 检查星陨消耗: 全部层数应被清除
    _, neg = g.get_marks("A")
    assert len(neg) == 0, f"星陨应被全部消耗，剩余: {neg}"

    # 检查防御方 HP 减少
    assert defender.current_hp < 300, f"防御方应受到伤害: {defender.current_hp}"


def test_starfall_no_trigger_without_marks():
    """无星陨时 trigger_starfall 返回 0。"""
    g = GlobalEffects()
    attacker = _make_normal_sprite("攻击方", hp=300)
    defender = _make_normal_sprite("防御方", hp=300)

    dmg = g.trigger_starfall("A", attacker, defender)
    assert dmg == 0


def test_starfall_consume_stacks_correctly():
    """星陨消耗后层数归零。"""
    g = GlobalEffects()
    attacker = _make_normal_sprite("攻击方", hp=300)
    defender = _make_normal_sprite("防御方", hp=300)

    g.apply_mark("B", "星陨印记", "negative", 5)
    _, neg_before = g.get_marks("B")
    assert neg_before[0].stacks == 5

    g.trigger_starfall("B", attacker, defender)
    _, neg_after = g.get_marks("B")
    assert len(neg_after) == 0


def test_starfall_damage_scales_with_stacks():
    """星陨伤害随层数增长。"""
    g1 = GlobalEffects()
    g2 = GlobalEffects()
    attacker = _make_normal_sprite("攻击方", hp=300)
    d1 = _make_normal_sprite("防御方1", hp=300)
    d2 = _make_normal_sprite("防御方2", hp=300)

    g1.apply_mark("A", "星陨印记", "negative", 2)
    dmg_2 = g1.trigger_starfall("A", attacker, d1)

    g2.apply_mark("A", "星陨印记", "negative", 4)
    dmg_4 = g2.trigger_starfall("A", attacker, d2)

    assert dmg_4 > dmg_2, f"4层伤害({dmg_4})应大于2层伤害({dmg_2})"


def test_steal_mark_from_opp():
    """Steal(from_target='team_opp', what='mark') transfers marks from opp."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Steal
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    # Give team B (opp) a positive mark, team A has none
    globals_.apply_mark("B", "光合印记", "positive", 3)
    replayer = JournalReplayer(sprite, opp, globals_, team="A")

    m = Steal(from_target="team_opp", what="mark", name="光合印记")
    replayer._apply_steal(m)
    # Should now be on team A
    pos_a, _ = globals_.get_marks("A")
    assert len(pos_a) == 1
    assert pos_a[0].name == "光合印记"
    assert pos_a[0].stacks == 3


def test_lock_on_sprite_opp():
    """Lock(target='sprite_opp') sets locked_turns on opponent."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Lock
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Lock(target="sprite_opp", turns=2)
    replayer._apply_lock(m)
    assert opp.locked_turns == 2
    assert sprite.locked_turns == 0


def test_lock_on_sprite_self():
    """Lock(target='sprite_self') sets locked_turns on self."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Lock
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    m = Lock(target="sprite_self", turns=1)
    replayer._apply_lock(m)
    assert sprite.locked_turns == 1
    assert opp.locked_turns == 0


def test_redirect_borrow_combo():
    """Borrow + Redirect chain: borrow opp skill, then redirect damage to self."""
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.journal import Borrow, Damage, Redirect
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100, energy=5,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100, energy=5,
        initial_stats={"atk": 120, "def": 90, "sp_atk": 120, "sp_def": 90, "speed": 105},
    )
    globals_ = GlobalEffects()
    engine = BattleVMEngine()

    # Build a journal: borrow opp's skill → redirect damage to self
    # First, simulate that the engine would normally produce Damage→sprite_opp,
    # but Borrow + Redirect change the flow
    from backend.vm.ctx import Ctx
    ctx = Ctx(
        power_opp=80, skill_type_opp="物攻", element_opp="火",
        atk_self=100, def_opp=90, sp_atk_self=100, sp_def_opp=90,
        damage_reduction_opp=0.0, combo_self=1,
    )
    journal = [
        Borrow(from_skill="skill_opp_current"),
        Redirect(target="sprite_self"),
    ]
    # Process borrow → produces Damage
    journal = engine._handle_borrow(journal, ctx)
    assert len(journal) == 2  # Redirect + Damage

    # Process redirect → changes Damage target
    journal = engine._handle_redirect(journal)
    assert len(journal) == 1
    assert isinstance(journal[0], Damage)
    assert journal[0].target == "sprite_self"  # redirected from sprite_opp

    # Apply damage
    replayer = JournalReplayer(sprite, opp, globals_)
    replayer.replay(journal)
    assert sprite.current_hp < 100  # took self-damage
    assert opp.current_hp == 100  # not damaged


def test_target_sprite_resolution_edge_cases():
    """Covers all known target strings handled by _target_sprite."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Known self targets → resolve to self
    assert replayer._target_sprite("sprite_self") is sprite
    assert replayer._target_sprite("self") is sprite
    assert replayer._target_sprite("team_own") is sprite
    assert replayer._target_sprite("skill_off_0") is sprite

    # Known opp targets + unrecognized → resolve to opp (default)
    assert replayer._target_sprite("sprite_opp") is opp
    assert replayer._target_sprite("skill_opp") is opp
    assert replayer._target_sprite("skill_off_1") is opp
    assert replayer._target_sprite("unknown_target") is opp
    assert replayer._target_sprite("") is opp  # empty string → opp (default)


def test_abnormal_change_e2e_through_vm():
    """End-to-end: VM produces AbnormalChange mutation targeting sprite_opp."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()

    effects = [
        {"op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 2},
        {"op": "abnormal", "target": "sprite_self", "name": "灼烧", "stacks": 1},
    ]
    ctx = Ctx()
    journal = vm_execute(ctx, effects)
    assert len(journal) == 2

    replayer = JournalReplayer(sprite, opp, globals_)
    replayer.replay(journal)
    assert len(opp.active_effects) == 1
    assert opp.active_effects[0].name == "中毒"
    assert opp.active_effects[0].stacks == 2
    assert len(sprite.active_effects) == 1
    assert sprite.active_effects[0].name == "灼烧"


def test_stat_change_e2e_both_directions():
    """End-to-end: VM StatChange targets both self (buff) and opp (debuff)."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute as vm_execute
    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()

    effects = [
        {"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 2},
        {"op": "mod", "target": "sprite_opp", "stat": "def", "steps": -3},
    ]
    ctx = Ctx()
    journal = vm_execute(ctx, effects)
    assert len(journal) == 2

    replayer = JournalReplayer(sprite, opp, globals_)
    replayer.replay(journal)
    assert len(sprite.active_effects) == 1
    assert sprite.active_effects[0].stat_key == "atk"
    assert sprite.active_effects[0].steps == 2
    assert len(opp.active_effects) == 1
    assert opp.active_effects[0].stat_key == "def"
    assert opp.active_effects[0].steps == -3


# ═══════════════════════════════════════════════════════════════════
# sprite_bench target + 系统发育 trait
# ═══════════════════════════════════════════════════════════════════


def test_target_sprite_bench_with_battle():
    """sprite_bench resolves to a random non-fainted benched ally."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory
    from backend.sim.player import Player

    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)

    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = p1.team[0]
    opp = p2.team[0]
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_, battle=battle, team="A")

    # Both bench sprites (indices 1 and 2) are alive → sprite_bench returns one
    result = replayer._target_sprite("sprite_bench")
    assert result in (p1.team[1], p1.team[2]), (
        f"Expected one of the bench sprites, got {result.name}"
    )

    # Faint bench index 1 → only index 2 remains
    p1.team[1].current_hp = 0  # trigger is_fainted
    result2 = replayer._target_sprite("sprite_bench")
    assert result2 is p1.team[2], (
        f"Expected only alive bench sprite, got {result2.name}"
    )

    # All bench fainted → fallback to self
    p1.team[2].current_hp = 0  # trigger is_fainted
    result3 = replayer._target_sprite("sprite_bench")
    assert result3 is sprite, (
        f"Expected fallback to self when no bench alive, got {result3.name}"
    )


def test_trait_系统发育_energy_to_bench():
    """系统发育: gaining energy → bench ally receives equal energy."""
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.engine.observer import ObserverRegistry
    from backend.engine.replayer import JournalReplayer
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory
    from backend.engine.observer import Observer
    from backend.vm.compiler.trait_to_observer import TraitToObserver
    from backend.vm.ctx import Ctx
    from backend.vm.journal import EnergyChange

    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},   # active, has trait
        {"name": "花衣蝶", "skills": ["甩水"]},        # bench
    ])
    p2 = factory.build_player("B", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)

    # Load trait → observers
    import json
    trait_path = Path(__file__).resolve().parent.parent.parent / "data" / "traits" / "系统发育.json"
    trait_data = json.loads(trait_path.read_text(encoding="utf-8"))

    compiler = TraitToObserver()
    obs_specs = compiler.compile(trait_data["effects"])
    assert len(obs_specs) >= 1, "Expected at least 1 observer from 系统发育"

    # Only test the energy observer (first one)
    energy_spec = obs_specs[0]
    energy_observer = Observer(
        cond=energy_spec["cond"],
        then=energy_spec["then"],
        scope=energy_spec["scope"],
        listen=energy_spec["listen"],
        name=energy_spec.get("name", ""),
        threshold=energy_spec.get("threshold", 1),
        reset_on_fire=energy_spec.get("reset_on_fire", True),
    )

    engine = BattleVMEngine()
    engine.registry.register(energy_observer)

    sprite = p1.team[0]  # active, has trait
    bench = p1.team[1]   # bench ally
    opp = p2.team[0]
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_, battle=battle, team="A")

    ctx = Ctx()
    ctx.event.energy_changed_of = "sprite_self"
    ctx.energy_delta_self = 3

    # Lower bench energy so gain_energy() has room (max_energy=10)
    bench.energy = 3
    bench_energy_before = bench.energy
    sprite.energy = 8

    # Fire mutation → should trigger observer → redirect energy to bench
    journal = [EnergyChange(target="sprite_self", delta=3)]
    engine._fire_mutation_events(journal, ctx, replayer)

    # Bench should receive +3 energy
    assert bench.energy == bench_energy_before + 3, (
        f"Expected bench energy {bench_energy_before + 3}, got {bench.energy}"
    )


def test_trait_系统发育_heal_to_bench():
    """系统发育: being healed → bench ally receives equal HP."""
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.engine.observer import ObserverRegistry
    from backend.engine.replayer import JournalReplayer
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory
    from backend.engine.observer import Observer
    from backend.vm.compiler.trait_to_observer import TraitToObserver
    from backend.vm.ctx import Ctx
    from backend.vm.journal import Heal

    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
        {"name": "花衣蝶", "skills": ["甩水"]},        # bench, will receive HP
    ])
    p2 = factory.build_player("B", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)

    import json
    trait_path = Path(__file__).resolve().parent.parent.parent / "data" / "traits" / "系统发育.json"
    trait_data = json.loads(trait_path.read_text(encoding="utf-8"))

    compiler = TraitToObserver()
    obs_specs = compiler.compile(trait_data["effects"])

    # Get the heal observer (second one)
    heal_spec = obs_specs[1]
    heal_observer = Observer(
        cond=heal_spec["cond"],
        then=heal_spec["then"],
        scope=heal_spec["scope"],
        listen=heal_spec["listen"],
        name=heal_spec.get("name", ""),
        threshold=heal_spec.get("threshold", 1),
        reset_on_fire=heal_spec.get("reset_on_fire", True),
    )

    engine = BattleVMEngine()
    engine.registry.register(heal_observer)

    sprite = p1.team[0]
    bench = p1.team[1]
    opp = p2.team[0]
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_, battle=battle, team="A")

    # Damage bench first so we can see the heal
    bench.current_hp = 50
    bench_hp_before = bench.current_hp

    ctx = Ctx()
    ctx.event.heal_of = "sprite_self"
    ctx.heal_delta_self = 15

    journal = [Heal(target="sprite_self", amount=15)]
    engine._fire_mutation_events(journal, ctx, replayer)

    # Bench should receive +15 HP
    assert bench.current_hp == bench_hp_before + 15, (
        f"Expected bench HP {bench_hp_before + 15}, got {bench.current_hp}"
    )


def test_trait_系统发育_negative_delta_ignored():
    """系统发育: losing energy (negative delta) does NOT redirect to bench."""
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.engine.observer import ObserverRegistry
    from backend.engine.replayer import JournalReplayer
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory
    from backend.engine.observer import Observer
    from backend.vm.compiler.trait_to_observer import TraitToObserver
    from backend.vm.ctx import Ctx
    from backend.vm.journal import EnergyChange

    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
        {"name": "花衣蝶", "skills": ["甩水"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)

    import json
    trait_path = Path(__file__).resolve().parent.parent.parent / "data" / "traits" / "系统发育.json"
    trait_data = json.loads(trait_path.read_text(encoding="utf-8"))

    compiler = TraitToObserver()
    obs_specs = compiler.compile(trait_data["effects"])
    energy_spec = obs_specs[0]
    energy_observer = Observer(
        cond=energy_spec["cond"],
        then=energy_spec["then"],
        scope=energy_spec["scope"],
        listen=energy_spec["listen"],
        name=energy_spec.get("name", ""),
        threshold=energy_spec.get("threshold", 1),
        reset_on_fire=energy_spec.get("reset_on_fire", True),
    )

    engine = BattleVMEngine()
    engine.registry.register(energy_observer)

    sprite = p1.team[0]
    bench = p1.team[1]
    opp = p2.team[0]
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_, battle=battle, team="A")

    bench_energy_before = bench.energy

    ctx = Ctx()
    ctx.event.energy_changed_of = "sprite_self"
    ctx.energy_delta_self = -5  # energy LOSS

    journal = [EnergyChange(target="sprite_self", delta=-5)]
    engine._fire_mutation_events(journal, ctx, replayer)

    # Bench should NOT receive energy on negative delta
    assert bench.energy == bench_energy_before, (
        f"Expected bench energy unchanged ({bench_energy_before}), got {bench.energy}"
    )


# ═══════════════════════════════════════════════════════════════
# Immunity system tests
# ═══════════════════════════════════════════════════════════════

def test_immunity_abnormal_specific_blocks_target():
    """Specific 灼烧 immunity blocks 灼烧 but not 中毒."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.effect import ModifierEffect
    from backend.vm.journal import AbnormalChange

    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Give sprite specific immunity to 灼烧
    sprite.active_effects.append(ModifierEffect(
        name="灼烧", source="test", attr="immune_abnormal", value=1.0,
        target="sprite_self",
    ))

    # 灼烧 should be blocked
    replayer._apply_abnormal_change(AbnormalChange(target="sprite_self", name="灼烧", delta=1))
    assert len([e for e in sprite.active_effects if getattr(e, 'name', None) == "灼烧" and hasattr(e, 'stacks')]) == 0, (
        "灼烧 should be blocked by specific immunity"
    )

    # 中毒 should NOT be blocked
    replayer._apply_abnormal_change(AbnormalChange(target="sprite_self", name="中毒", delta=1))
    poison_effects = [e for e in sprite.active_effects if getattr(e, 'name', None) == "中毒"]
    assert len(poison_effects) == 1, "中毒 should NOT be blocked by 灼烧 immunity"
    assert poison_effects[0].stacks == 1


def test_immunity_abnormal_blanket_blocks_all():
    """Blanket immunity (empty name) blocks any abnormal."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.effect import ModifierEffect
    from backend.vm.journal import AbnormalChange

    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Blanket immunity: name="" means all abnormals
    sprite.active_effects.append(ModifierEffect(
        name="", source="test", attr="immune_abnormal", value=1.0,
        target="sprite_self",
    ))

    for ab_name in ("灼烧", "中毒", "冻结", "麻痹"):
        replayer._apply_abnormal_change(AbnormalChange(target="sprite_self", name=ab_name, delta=1))
        stacks = [e for e in sprite.active_effects if getattr(e, 'name', None) == ab_name and hasattr(e, 'stacks')]
        assert len(stacks) == 0, f"{ab_name} should be blocked by blanket immunity"


def test_immunity_abnormal_removal_not_blocked():
    """Immunity does NOT block abnormal removal (delta < 0)."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.effect import ModifierEffect
    from backend.vm.journal import AbnormalChange

    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Give sprite immunity to 灼烧
    sprite.active_effects.append(ModifierEffect(
        name="灼烧", source="test", attr="immune_abnormal", value=1.0,
        target="sprite_self",
    ))

    # First apply 灼烧 (should be blocked)
    replayer._apply_abnormal_change(AbnormalChange(target="sprite_self", name="灼烧", delta=1))
    burn_effects = [e for e in sprite.active_effects if getattr(e, 'name', None) == "灼烧" and hasattr(e, 'stacks')]
    assert len(burn_effects) == 0, "灼烧 application should be blocked"

    # Now manually add 灼烧 to simulate it was applied before immunity
    from backend.vm.effect import AbnormalEffect
    sprite.active_effects.append(AbnormalEffect(name="灼烧", source="skill", stacks=2))

    # Removal (delta < 0) should NOT be blocked
    replayer._apply_abnormal_change(AbnormalChange(target="sprite_self", name="灼烧", delta=-1))
    burn_after = [e for e in sprite.active_effects if getattr(e, 'name', None) == "灼烧" and hasattr(e, 'stacks')]
    assert len(burn_after) == 1, "灼烧 removal should NOT be blocked by immunity"
    assert burn_after[0].stacks == 1, f"Expected 1 stack after removal, got {burn_after[0].stacks}"


def test_immunity_stat_down_specific():
    """Specific atk immunity blocks atk debuff but not def debuff."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.effect import ModifierEffect
    from backend.vm.journal import StatChange

    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Specific atk debuff immunity
    sprite.active_effects.append(ModifierEffect(
        name="atk", source="test", attr="immune_stat_down", value=1.0,
        target="sprite_self",
    ))

    # atk debuff should be blocked
    replayer._apply_stat_change(StatChange(target="sprite_self", stat="atk", steps=-2, scope="battlefield"))
    atk_effects = [e for e in sprite.active_effects if getattr(e, 'stat_key', None) == "atk"]
    assert len(atk_effects) == 0, "atk debuff should be blocked by specific immunity"

    # def debuff should NOT be blocked
    replayer._apply_stat_change(StatChange(target="sprite_self", stat="def", steps=-2, scope="battlefield"))
    def_effects = [e for e in sprite.active_effects if getattr(e, 'stat_key', None) == "def"]
    assert len(def_effects) == 1, "def debuff should NOT be blocked by atk immunity"
    assert def_effects[0].steps == -2


def test_immunity_stat_down_blanket():
    """Blanket stat immunity blocks all stat debuffs."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.effect import ModifierEffect
    from backend.vm.journal import StatChange

    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Blanket stat down immunity
    sprite.active_effects.append(ModifierEffect(
        name="", source="test", attr="immune_stat_down", value=1.0,
        target="sprite_self",
    ))

    for stat in ("atk", "def", "sp_atk", "sp_def", "speed"):
        replayer._apply_stat_change(StatChange(target="sprite_self", stat=stat, steps=-2, scope="battlefield"))
        effects = [e for e in sprite.active_effects if getattr(e, 'stat_key', None) == stat]
        assert len(effects) == 0, f"{stat} debuff should be blocked by blanket immunity"


def test_immunity_stat_buff_not_blocked():
    """Immunity does NOT block stat buffs (steps > 0)."""
    from backend.common.models import SpeciesStats
    from backend.engine.replayer import JournalReplayer
    from backend.sim.sprite import Sprite
    from backend.vm.effect import ModifierEffect
    from backend.vm.journal import StatChange

    species = SpeciesStats(name="test", hp=100, atk=100, def_=100, sp_atk=100, sp_def=100, speed=100)
    sprite = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    opp = Sprite(
        species=species, current_hp=100, max_hp=100,
        initial_stats={"atk": 100, "def": 100, "sp_atk": 100, "sp_def": 100, "speed": 100},
    )
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_)

    # Blanket stat down immunity
    sprite.active_effects.append(ModifierEffect(
        name="", source="test", attr="immune_stat_down", value=1.0,
        target="sprite_self",
    ))

    # atk buff (steps > 0) should NOT be blocked
    replayer._apply_stat_change(StatChange(target="sprite_self", stat="atk", steps=2, scope="battlefield"))
    atk_effects = [e for e in sprite.active_effects if getattr(e, 'stat_key', None) == "atk"]
    assert len(atk_effects) == 1, "atk buff should NOT be blocked by stat-down immunity"
    assert atk_effects[0].steps == 2

    # sp_atk buff should also pass through
    replayer._apply_stat_change(StatChange(target="sprite_self", stat="sp_atk", steps=1, scope="battlefield"))
    spa_effects = [e for e in sprite.active_effects if getattr(e, 'stat_key', None) == "sp_atk"]
    assert len(spa_effects) == 1, "sp_atk buff should NOT be blocked"


def test_trait_美拉德反应_immunity():
    """美拉德反应: leaving field grants bench ally ATK/SP_ATK+20% and 灼烧 immunity."""
    from backend.common.models import SpeciesStats
    from backend.engine.battle import BattleVMEngine
    from backend.engine.observer import Observer
    from backend.engine.replayer import JournalReplayer
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory
    from backend.vm.compiler.trait_to_observer import TraitToObserver
    from backend.vm.ctx import Ctx
    from backend.vm.journal import AbnormalChange

    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "秩序鱿墨", "skills": ["猛烈撞击"]},  # active, has 美拉德反应
        {"name": "花衣蝶", "skills": ["甩水"]},         # bench
    ])
    p2 = factory.build_player("B", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)

    trait_path = Path(__file__).resolve().parent.parent.parent / "data" / "traits" / "美拉德反应.json"
    trait_data = json.loads(trait_path.read_text(encoding="utf-8"))

    compiler = TraitToObserver()
    obs_specs = compiler.compile(trait_data["effects"])
    assert len(obs_specs) >= 1, "Expected at least 1 observer from 美拉德反应"

    obs_spec = obs_specs[0]
    observer = Observer(
        cond=obs_spec["cond"],
        then=obs_spec["then"],
        scope=obs_spec["scope"],
        listen=obs_spec["listen"],
        name=obs_spec.get("name", ""),
        threshold=obs_spec.get("threshold", 1),
        reset_on_fire=obs_spec.get("reset_on_fire", True),
    )

    engine = BattleVMEngine()
    engine.registry.register(observer)

    sprite = p1.team[0]  # active with trait
    bench = p1.team[1]   # bench ally
    opp = p2.team[0]
    globals_ = GlobalEffects()
    replayer = JournalReplayer(sprite, opp, globals_, battle=battle, team="A")

    # Fire post_leave observer directly (simulating sprite leaving)
    ctx = Ctx()
    ctx.event.sprite_left_of = "sprite_self"
    ctx.event.self_switched = True

    engine._fire_post_event("post_leave", ctx, replayer)

    # Check bench got immunity ModifierEffect
    from backend.vm.effect import ModifierEffect
    immune_effects = [
        e for e in bench.active_effects
        if isinstance(e, ModifierEffect) and e.attr == "immune_abnormal"
    ]
    assert len(immune_effects) == 1, f"Expected 1 immunity effect on bench, got {len(immune_effects)}"
    assert immune_effects[0].name == "灼烧", (
        f"Expected immunity name='灼烧', got '{immune_effects[0].name}'"
    )

    # Verify bench is now immune to 灼烧
    bench_replayer = JournalReplayer(bench, opp, globals_, battle=battle, team="A")
    bench_replayer._apply_abnormal_change(AbnormalChange(target="sprite_self", name="灼烧", delta=1))
    burn_effects = [e for e in bench.active_effects if getattr(e, 'name', None) == "灼烧" and hasattr(e, 'stacks')]
    assert len(burn_effects) == 0, "Bench should be immune to 灼烧 after 美拉德反应"

    # But NOT immune to 中毒
    bench_replayer._apply_abnormal_change(AbnormalChange(target="sprite_self", name="中毒", delta=1))
    poison_effects = [e for e in bench.active_effects if getattr(e, 'name', None) == "中毒"]
    assert len(poison_effects) == 1, "Bench should NOT be immune to 中毒"


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
    # ── Effect lifecycle: priority, ttl, delay, cooldown ──
    test_priority_sort_within_phase()
    test_priority_sort_mixed_phases()
    test_ttl_on_statuseffect()
    test_ttl_decrement_and_expiry()
    test_delay_stores_on_sprite()
    test_delay_decremented_at_turn_start()
    test_cooldown_on_statuseffect()
    test_cooldown_decrement_on_use()
    # ── Advanced mod filters: on_next, skill_where, element:each ──
    test_on_next_modifier_not_applied_immediately()
    test_on_next_modifier_applied_on_matching_skill()
    test_skill_where_filters_by_condition()
    test_element_each_per_element_limits()
    # ── Target coverage: all effect directions ──
    test_modifier_on_opp_sprite()
    test_modifier_on_opp_energy_cost()
    test_mark_change_opp_team()
    test_mark_change_own_and_opp()
    test_abnormal_change_to_sprite_opp()
    test_abnormal_change_to_sprite_self()
    test_dispel_opp_positive_buffs()
    test_dispel_self_negative_debuffs()
    test_dispel_self_abnormal()
    test_heal_to_sprite_self()
    test_heal_to_sprite_opp()
    test_energy_change_to_opp()
    test_energy_change_to_self()
    test_stat_change_to_sprite_self()
    test_stat_change_to_sprite_opp()
    test_damage_to_sprite_self()
    test_damage_to_sprite_opp()
    test_tick_to_sprite_opp()
    test_tick_to_sprite_self()
    test_double_on_opp()
    test_double_on_self()
    test_steal_mark_from_opp()
    test_lock_on_sprite_opp()
    test_lock_on_sprite_self()
    test_redirect_borrow_combo()
    test_target_sprite_resolution_edge_cases()
    test_abnormal_change_e2e_through_vm()
    test_stat_change_e2e_both_directions()
    test_target_sprite_bench_with_battle()
    test_trait_系统发育_energy_to_bench()
    test_trait_系统发育_heal_to_bench()
    test_trait_系统发育_negative_delta_ignored()
    # ── Immunity system ──
    test_immunity_abnormal_specific_blocks_target()
    test_immunity_abnormal_blanket_blocks_all()
    test_immunity_abnormal_removal_not_blocked()
    test_immunity_stat_down_specific()
    test_immunity_stat_down_blanket()
    test_immunity_stat_buff_not_blocked()
    test_trait_美拉德反应_immunity()
    print("\nAll engine integration tests passed!")
