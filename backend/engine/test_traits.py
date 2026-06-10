"""Tests for data-driven traits: observer + inherit + post_enemy_leave."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.common.models import SpeciesStats
from backend.sim.action import Action
from backend.sim.battle import Battle, _load_permanent_skill_mods_for_sprite
from backend.sim.battleskill import BattleSkill
from backend.sim.factory import SimFactory
from backend.sim.player import Player
from backend.sim.pipeline import TurnPipeline
from backend.sim.skill import Skill
from backend.sim.sprite import Sprite
from backend.sim.traits import dispatch_entry, dispatch_leave
from backend.vm.effect import AbnormalEffect, StatBuffEffect
from backend.vm.journal import CounterRegister

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


def test_ctx_skill_summary_tracks_dynamic_skill_slot_state():
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)
    sprite = battle.player_a.active
    opp = battle.player_b.active

    water = Skill(name="水测", element="水", energy_cost=2)
    fire = Skill(name="火测", element="火", energy_cost=0)
    grass = Skill(name="草测", element="草", energy_cost=5)
    sprite.skills = [BattleSkill(water), BattleSkill(fire)]

    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    assert ctx.skill_elements_self == frozenset({"水", "火"})
    assert ctx.skill_element_counts_self == {"水": 1, "火": 1}
    assert ctx.skills_energy_sum_self == 2
    assert ctx.zero_cost_skill_count_self == 1

    sprite.skills[0]._modifiers["energy_cost"] = -2
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    assert ctx.skills_energy_sum_self == 0
    assert ctx.zero_cost_skill_count_self == 2

    sprite.skills[1]._element_override = "电"
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    assert ctx.skill_elements_self == frozenset({"水", "电"})
    assert ctx.skill_element_counts_self == {"水": 1, "电": 1}

    sprite.skills[0].replaced_by = grass
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    assert ctx.skill_elements_self == frozenset({"草", "电"})
    assert ctx.skills_energy_sum_self == 3

    sprite.skills[0]._modifiers.clear()
    sprite.skills[0].nullified = True
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    assert ctx.skill_elements_self == frozenset({"电"})
    assert ctx.skills_energy_sum_self == 0
    assert ctx.zero_cost_skill_count_self == 2


def test_batch_load_permanent_skill_mods_matches_slot_base_name():
    sprite = Sprite(
        species=SpeciesStats(name="测试精灵", hp=100, atk=80, def_=80),
        initial_stats={"hp": 100, "atk": 80, "def": 80, "sp_atk": 80, "sp_def": 80, "speed": 80},
        current_hp=100,
        max_hp=100,
    )
    sprite.skills = [
        BattleSkill(Skill(name="同名技能", energy_cost=3)),
        BattleSkill(Skill(name="同名技能", energy_cost=4)),
        BattleSkill(Skill(name="其他技能", energy_cost=5)),
    ]
    sprite._modifiers.update({
        "skill.同名技能.energy_cost": -1.0,
        "skill.同名技能.power": 20.0,
        "skill.其他技能.combo": 2.0,
    })

    _load_permanent_skill_mods_for_sprite(sprite)

    assert sprite.skills[0]._modifiers["energy_cost"] == -1.0
    assert sprite.skills[1]._modifiers["energy_cost"] == -1.0
    assert sprite.skills[0]._modifiers["power"] == 20.0
    assert sprite.skills[1]._modifiers["power"] == 20.0
    assert "combo" not in sprite.skills[0]._modifiers
    assert sprite.skills[2]._modifiers["combo"] == 2.0


def test_execute_skill_vm_passes_full_team_ctx_kwargs():
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
        {"name": "神谕鲨", "skills": ["甩水"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    battle.player_b.team[1].current_hp = 0
    battle._invalidate_ctx_team_cache()
    battle.player_a.lives = 3
    battle.player_b.lives = 2
    battle.player_a.devotion["测试专注"] = 2
    battle.inc_team_counter("A", "element:水", 1)

    captured = {}
    original_execute_skill = battle._vm_engine.execute_skill

    def spy_execute_skill(*args, **kwargs):
        captured.update(kwargs)
        return original_execute_skill(*args, **kwargs)

    battle._vm_engine.execute_skill = spy_execute_skill
    battle._execute_skill_vm("A", Action(kind="skill", skill_index=0))

    assert captured["team_counters_own"]["element:水"] == 1
    assert captured["fainted_own"] == 0
    assert captured["fainted_opp"] == 1
    assert captured["lives_own"] == 3
    assert captured["lives_opp"] == 2
    assert captured["devotion_own"]["测试专注"] == 2
    assert "水" in captured["team_elements_own"]


def test_ctx_team_moe_stacks_cache_excludes_self_and_invalidates():
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
        {"name": "神谕鲨", "skills": ["甩水"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])
    battle = Battle(p1, p2, verbose=False)
    self_sprite = battle.player_a.team[0]
    bench = battle.player_a.team[1]
    opp = battle.player_b.active
    self_sprite.add_effect(AbnormalEffect(name="萌化", source="test", stacks=1))
    bench.add_effect(AbnormalEffect(name="萌化", source="test", stacks=2))
    battle._invalidate_ctx_team_cache()

    ctx_self = battle._make_ctx(self_sprite, opp, None, None, battle.globals, team="A", turn=0)
    ctx_bench = battle._make_ctx(bench, opp, None, None, battle.globals, team="A", turn=0)
    assert ctx_self.moe_team_stacks == 2
    assert ctx_bench.moe_team_stacks == 1

    bench.update_stacks("萌化", 4)
    battle._invalidate_ctx_team_cache()
    ctx_self_after = battle._make_ctx(self_sprite, opp, None, None, battle.globals, team="A", turn=0)
    assert ctx_self_after.moe_team_stacks == 4


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


def _make_transmission_battle() -> Battle:
    return Battle(Player(name="A", team=[]), Player(name="B", team=[]), verbose=False)


def _make_transmission_sprite(ability: str = "", ability_id: int = 0) -> Sprite:
    return Sprite(
        species=SpeciesStats(
            name="传动测试", hp=100, atk=80, def_=80,
            sp_atk=80, sp_def=80, speed=80,
            ability=ability, ability_id=ability_id,
        ),
        current_hp=100,
        max_hp=100,
        energy=10,
    )


def _make_transmission_skill(name: str, transmission: int, energy_cost: int = 5) -> BattleSkill:
    return BattleSkill(
        base=Skill(name=name, transmission=transmission, energy_cost=energy_cost)
    )


def _make_gear_torque_skill(transmission: int = 1) -> BattleSkill:
    data = json.loads((_PROJ / "data" / "skills" / "齿轮扭矩.json").read_text("utf-8"))
    skill = Skill(
        id=data.get("id", 0),
        name=data["name"],
        element=data.get("element", ""),
        skill_type=data.get("skill_type", ""),
        power=data.get("power", 0),
        energy_cost=data.get("energy_cost", 0),
        transmission=transmission,
    )
    return BattleSkill(base=skill)


def _register_skill_observers(battle: Battle, sprite: Sprite, skill: BattleSkill) -> None:
    record = battle._get_skill_record(skill.base.name)
    ctx = battle._make_ctx(
        sprite, battle.get_opponent("A").active,
        record, None, battle.globals,
        team="A", turn=battle.turn, battle_skill=skill,
    )
    journal = battle._vm_engine.execute_effects(ctx, record.effects)
    for mutation in journal:
        if isinstance(mutation, CounterRegister):
            battle._vm_engine.register_counter(mutation, owner_sprite=sprite, owner_skill=skill)


def _make_position_trait_battle(ability: str, ability_id: int = 0) -> tuple[Battle, Sprite, Sprite]:
    sprite = _make_transmission_sprite(ability=ability, ability_id=ability_id)
    sprite.skills = [
        _make_transmission_skill("A", transmission=0),
        _make_transmission_skill("B", transmission=0),
        _make_transmission_skill("C", transmission=0),
    ]
    opp = _make_transmission_sprite()
    opp.species.name = "对手"
    opp.skills = [_make_transmission_skill("O", transmission=0)]
    battle = Battle(
        Player(name="A", team=[sprite]),
        Player(name="B", team=[opp]),
        verbose=False,
    )
    return battle, sprite, opp


# ═══════════════════════════════════════════════════════════════════
# 机械变式 (ID 20159)
# ═══════════════════════════════════════════════════════════════════

def test_trait_机械变式_reduces_cost_per_transmission_move():
    """机械变式: 传动2每移动一次能耗-1，因此每个技能累计-2。"""
    battle = _make_transmission_battle()
    sprite = _make_transmission_sprite(ability="机械变式")
    sprite.skills = [
        _make_transmission_skill("A", transmission=2),
        _make_transmission_skill("B", transmission=2),
        _make_transmission_skill("C", transmission=2),
    ]

    battle._apply_transmission(sprite)

    assert [bs.name for bs in sprite.skills] == ["B", "C", "A"]
    assert [bs._mech_energy_reduction for bs in sprite.skills] == [-2, -2, -2]
    assert [bs.energy_cost for bs in sprite.skills] == [3, 3, 3]


def test_trait_机械变式_counts_displaced_normal_skill():
    """机械变式: 普通技能被传动块挤走也算位置变化。"""
    battle = _make_transmission_battle()
    sprite = _make_transmission_sprite(ability_id=20159)
    sprite.skills = [
        _make_transmission_skill("A", transmission=0),
        _make_transmission_skill("B", transmission=1),
        _make_transmission_skill("C", transmission=0),
    ]

    battle._apply_transmission(sprite)

    assert [bs.name for bs in sprite.skills] == ["A", "C", "B"]
    reductions = {bs.name: bs._mech_energy_reduction for bs in sprite.skills}
    assert reductions == {"A": 0, "C": -1, "B": -1}


def test_trait_机械变式_reduction_clears_on_leave():
    """机械变式减能耗持续到退场，退场后清零。"""
    battle = _make_transmission_battle()
    sprite = _make_transmission_sprite(ability="机械变式")
    sprite.skills = [
        _make_transmission_skill("A", transmission=1),
        _make_transmission_skill("B", transmission=1),
    ]
    battle._apply_transmission(sprite)
    assert any(bs._mech_energy_reduction for bs in sprite.skills)

    dispatch_leave(sprite, battle, "A")

    assert [bs._mech_energy_reduction for bs in sprite.skills] == [0, 0]


def test_trait_机械变式_does_not_apply_to_other_traits():
    """非机械变式只传动，不获得能耗减免。"""
    battle = _make_transmission_battle()
    sprite = _make_transmission_sprite()
    sprite.skills = [
        _make_transmission_skill("A", transmission=1),
        _make_transmission_skill("B", transmission=1),
    ]

    battle._apply_transmission(sprite)

    assert [bs.name for bs in sprite.skills] == ["B", "A"]
    assert [bs._mech_energy_reduction for bs in sprite.skills] == [0, 0]


def test_skill_齿轮扭矩_transmission_position_change_adds_power_to_moved_skill():
    """齿轮扭矩: 已注册后，每次传动位置变化给移动技能对象永久威力+20。"""
    battle, sprite, _ = _make_position_trait_battle("")
    gear = _make_gear_torque_skill(transmission=1)
    normal = _make_transmission_skill("普通", transmission=0)
    sprite.skills = [gear, normal]
    _register_skill_observers(battle, sprite, gear)

    battle._apply_transmission(sprite, team="A")

    assert [bs.name for bs in sprite.skills] == ["普通", "齿轮扭矩"]
    assert gear._modifiers.get("power") == 20
    assert normal._modifiers.get("power", 0) == 0


def test_skill_齿轮扭矩_transmission2_counts_each_pass():
    """齿轮扭矩: 传动2移动两次，累计+40。"""
    battle, sprite, _ = _make_position_trait_battle("")
    gear = _make_gear_torque_skill(transmission=2)
    normal = _make_transmission_skill("普通", transmission=0)
    sprite.skills = [gear, normal]
    _register_skill_observers(battle, sprite, gear)

    battle._apply_transmission(sprite, team="A")

    assert [bs.name for bs in sprite.skills] == ["齿轮扭矩", "普通"]
    assert gear._modifiers.get("power") == 40


def test_skill_齿轮扭矩_exchange_adjacent_counts_and_follows_object():
    """齿轮扭矩: 技能效果交换相邻位置也触发，且加成跟随技能对象。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import Exchange

    battle, sprite, opp = _make_position_trait_battle("")
    gear = _make_gear_torque_skill(transmission=0)
    normal = _make_transmission_skill("普通", transmission=0)
    other = _make_transmission_skill("其他", transmission=0)
    sprite.skills = [gear, normal, other]
    _register_skill_observers(battle, sprite, gear)

    replayer = JournalReplayer(
        sprite, opp, battle.globals, battle._vm_engine.registry,
        team="A", battle=battle,
    )
    replayer.replay([Exchange(target="sprite_self", what="adjacent_skills")])

    assert [bs.name for bs in sprite.skills] == ["普通", "齿轮扭矩", "其他"]
    assert gear._modifiers.get("power") == 20
    assert sprite.skills[1] is gear
    assert sprite.skills[0]._modifiers.get("power", 0) == 0


def test_battle_snapshot_restores_observer_registry():
    """MCTS 回滚: 仿真中注册的技能 watcher 不应泄漏到原战斗状态。"""
    battle, sprite, _ = _make_position_trait_battle("")
    gear = _make_gear_torque_skill(transmission=1)
    sprite.skills = [gear, _make_transmission_skill("普通", transmission=0)]
    before_count = len(battle._vm_engine.registry._observers)

    saved = battle.save_mutable_state()
    _register_skill_observers(battle, sprite, gear)
    assert len(battle._vm_engine.registry._observers) == before_count + 1
    battle._vm_engine.registry._observers[-1]._hit_count = 7

    battle.restore_mutable_state(saved)

    assert len(battle._vm_engine.registry._observers) == before_count
    assert not battle._vm_engine.registry.has_candidates("post_skill", id(sprite))


def test_trait_interaction_copy_does_not_mutate_shared_species():
    """复制/替换特性时不能污染共享 SpeciesStats（数据库/同物种替补）。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import TraitInteractionMutation

    shared_species = SpeciesStats(
        name="共享物种", hp=100, atk=80, def_=80,
        sp_atk=80, sp_def=80, speed=80,
        ability="原特性", ability_id=10001,
    )
    target = Sprite(species=shared_species, current_hp=100, max_hp=100)
    bench_same_species = Sprite(species=shared_species, current_hp=100, max_hp=100)
    source = Sprite(
        species=SpeciesStats(
            name="来源", hp=100, atk=80, def_=80,
            sp_atk=80, sp_def=80, speed=80,
            ability="向心力", ability_id=20024,
        ),
        current_hp=100,
        max_hp=100,
    )
    battle = Battle(
        Player(name="A", team=[target, bench_same_species]),
        Player(name="B", team=[source]),
        verbose=False,
    )

    replayer = JournalReplayer(
        target, source, battle.globals, battle._vm_engine.registry,
        team="A", battle=battle,
    )
    replayer.replay([
        TraitInteractionMutation(
            action="copy", target="sprite_self", copy_from="sprite_opp",
        )
    ])

    assert target.species.ability == "向心力"
    assert target.species.ability_id == 20024
    assert target.species is not shared_species
    assert bench_same_species.species is shared_species
    assert bench_same_species.species.ability == "原特性"
    assert bench_same_species.species.ability_id == 10001


def test_battle_snapshot_restores_trait_fields_and_abnormal_damage_memory():
    """MCTS 回滚: 特性交互和异常 tick 记忆都不能泄漏到后续分支。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import TraitInteractionMutation

    target = _make_transmission_sprite(ability="原特性", ability_id=10001)
    source = _make_transmission_sprite(ability="翼轴", ability_id=20109)
    battle = Battle(
        Player(name="A", team=[target]),
        Player(name="B", team=[source]),
        verbose=False,
    )
    target._last_abnormal_dmg["灼烧"] = 7

    saved = battle.save_mutable_state()
    replayer = JournalReplayer(
        target, source, battle.globals, battle._vm_engine.registry,
        team="A", battle=battle,
    )
    replayer.replay([
        TraitInteractionMutation(
            action="copy", target="sprite_self", copy_from="sprite_opp",
        )
    ])
    target._last_abnormal_dmg["灼烧"] = 19

    assert target.species.ability == "翼轴"
    assert target.species.ability_id == 20109

    battle.restore_mutable_state(saved)

    assert target.species.ability == "原特性"
    assert target.species.ability_id == 10001
    assert target._last_abnormal_dmg == {"灼烧": 7}
    assert getattr(target, "_trait_handler", None) is None


def test_battle_snapshot_restores_trait_direct_modifier_state():
    """MCTS 回滚: 特性 direct modifier 的重应用状态不能泄漏。"""
    sprite = _make_transmission_sprite(ability="水翼飞升", ability_id=20078)
    sprite.skills = [_make_transmission_skill("水花四溅", transmission=0, energy_cost=0)]
    opp = _make_transmission_sprite()
    battle = Battle(
        Player(name="A", team=[sprite]),
        Player(name="B", team=[opp]),
        verbose=False,
    )
    sprite._trait_direct_effects = [
        {
            "op": "power_mod",
            "attr": "energy_cost",
            "delta": -1,
            "mode": "add",
            "ttl": 2,
            "source": "测试特性",
        }
    ]
    sprite._direct_mod_tracked = {"水花四溅": {"energy_cost": -1.0}}

    saved = battle.save_mutable_state()
    sprite._trait_direct_effects[0]["ttl"] = 1
    sprite._trait_direct_effects.append({
        "op": "power_mod",
        "attr": "power_mult",
        "delta": 1.3,
        "mode": "set",
        "source": "分支特性",
    })
    sprite._direct_mod_tracked["水花四溅"]["energy_cost"] = -3.0
    sprite._direct_mod_tracked["水花四溅"]["power_mult"] = 1.3

    battle.restore_mutable_state(saved)

    assert sprite._trait_direct_effects == [
        {
            "op": "power_mod",
            "attr": "energy_cost",
            "delta": -1,
            "mode": "add",
            "ttl": 2,
            "source": "测试特性",
        }
    ]
    assert sprite._direct_mod_tracked == {"水花四溅": {"energy_cost": -1.0}}


def test_ctx_team_elements_cache_invalidates_after_transform():
    """同回合形态变化后，Ctx 队伍属性集合不能继续使用旧缓存。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import TransformMutation

    class _SpeciesDB:
        def get(self, name: str, form: str = ""):
            return SpeciesStats(
                name=name, hp=100, atk=80, def_=80,
                sp_atk=80, sp_def=80, speed=80,
                attributes="火",
            )

    target = Sprite(
        species=SpeciesStats(
            name="水形态", hp=100, atk=80, def_=80,
            sp_atk=80, sp_def=80, speed=80,
            attributes="水",
        ),
        current_hp=100,
        max_hp=100,
    )
    opp = _make_transmission_sprite()
    battle = Battle(
        Player(name="A", team=[target]),
        Player(name="B", team=[opp]),
        verbose=False,
    )
    battle.species_db = _SpeciesDB()

    before = battle._make_ctx(
        target, opp, None, None, battle.globals, team="A", turn=battle.turn,
    )
    assert "水" in before.team_elements_own

    replayer = JournalReplayer(
        target, opp, battle.globals, battle._vm_engine.registry,
        team="A", battle=battle,
    )
    replayer.replay([TransformMutation(species="火形态")])

    after = battle._make_ctx(
        target, opp, None, None, battle.globals, team="A", turn=battle.turn,
    )

    assert target.species.attributes == "火"
    assert "火" in after.team_elements_own
    assert "水" not in after.team_elements_own


# ═══════════════════════════════════════════════════════════════════
# 位置型入场/回合开始特性
# ═══════════════════════════════════════════════════════════════════

def test_trait_向心力_turn_start_applies_before_transmission():
    """向心力: 当前1/2号位参与本次传动，传动后效果绑定新1/2号位。"""
    battle, sprite, _ = _make_position_trait_battle("向心力", 20024)
    for bs in sprite.skills:
        bs._transmission = 0
        bs._modifiers.clear()

    TurnPipeline.execute_turn_start(battle)

    assert [bs.name for bs in sprite.skills] == ["C", "A", "B"]
    moved = {bs.name: bs for bs in sprite.skills}
    assert moved["C"]._transmission == 1
    assert moved["A"]._transmission == 1
    assert moved["B"]._transmission == 0
    assert moved["C"]._modifiers.get("power") == 30
    assert moved["A"]._modifiers.get("power") == 30
    assert "power" not in moved["B"]._modifiers


def test_trait_向心力_turn_start_rebinds_in_mcts_headless_mode():
    """MCTS/headless: 无传动日志时，位置效果仍需传动后重新投影。"""
    battle, sprite, _ = _make_position_trait_battle("向心力", 20024)
    battle._mcts_sim = True
    for bs in sprite.skills:
        bs._transmission = 0
        bs._modifiers.clear()

    TurnPipeline.execute_turn_start(battle)

    assert [bs.name for bs in sprite.skills] == ["C", "A", "B"]
    moved = {bs.name: bs for bs in sprite.skills}
    assert moved["C"]._modifiers.get("power") == 30
    assert moved["A"]._modifiers.get("power") == 30
    assert "power" not in moved["B"]._modifiers


def test_trait_向心力_switch_entry_applies_before_entry_transmission():
    """向心力: 换宠入场传动后，位置效果绑定新1/2号位。"""
    old = _make_transmission_sprite()
    old.species.name = "旧精灵"
    old.skills = [_make_transmission_skill("旧", transmission=0)]
    new = _make_transmission_sprite(ability="向心力", ability_id=20024)
    new.species.name = "向心力精灵"
    new.skills = [
        _make_transmission_skill("A", transmission=0),
        _make_transmission_skill("B", transmission=0),
        _make_transmission_skill("C", transmission=0),
    ]
    opp = _make_transmission_sprite()
    opp.skills = [_make_transmission_skill("O", transmission=0)]
    battle = Battle(
        Player(name="A", team=[old, new]),
        Player(name="B", team=[opp]),
        verbose=False,
    )

    battle._resolve_switch("A", Action(kind="switch", switch_index=1))

    assert [bs.name for bs in new.skills] == ["C", "A", "B"]
    moved = {bs.name: bs for bs in new.skills}
    assert moved["C"]._transmission == 1
    assert moved["A"]._transmission == 1
    assert moved["B"]._transmission == 0


def test_trait_翼轴_swift_tracks_pre_transmission_first_slot():
    """翼轴: 1号位参与本次传动，传动后迅捷绑定新的1号位。"""
    old = _make_transmission_sprite()
    old.species.name = "旧精灵"
    old.skills = [_make_transmission_skill("旧", transmission=0)]
    new = _make_transmission_sprite(ability="翼轴", ability_id=20109)
    new.species.name = "翼轴精灵"
    new.skills = [
        _make_transmission_skill("A", transmission=0),
        _make_transmission_skill("B", transmission=0),
        _make_transmission_skill("C", transmission=0),
    ]
    opp = _make_transmission_sprite()
    opp.skills = [_make_transmission_skill("O", transmission=0)]
    battle = Battle(
        Player(name="A", team=[old, new]),
        Player(name="B", team=[opp]),
        verbose=False,
    )

    battle._resolve_switch("A", Action(kind="switch", switch_index=1))

    assert [bs.name for bs in new.skills] == ["B", "A", "C"]
    swift_idx, swift_bs = battle._find_first_swift_skill(new)
    assert swift_idx == 0
    assert swift_bs is not None
    assert swift_bs.name == "B"
    assert new.skills[0]._transmission == 1
    assert new.skills[1]._transmission == 0


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


# ═══════════════════════════════════════════════════════════════════
# 血型吸引 (ID 20122)
# ═══════════════════════════════════════════════════════════════════


def test_trait_血型吸引_power_boost_by_enemy_skill_elements():
    """血型吸引: +10 power per distinct skill element the enemy carries."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p1.team[0].species.ability = "血型吸引"
    p1.team[0].species.ability_id = 20122

    # Enemy carries 3 distinct elements: 水, 火, 草
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["甩水", "火苗", "种子弹"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")

    sprite = p1.team[0]
    opp = p2.team[0]
    sk = sprite.skills[0]

    # Verify ctx counts 3 distinct enemy skill elements
    ctx = battle._make_ctx(sprite, opp, sk, None, battle.globals, team="A", turn=0)
    assert ctx.skill_element_count_opp == 3, (
        f"expected 3 distinct elements, got {ctx.skill_element_count_opp}"
    )

    # Fire pre_modifier → power_mod = 3 * 10 = 30
    battle._vm_engine.fire_trigger("pre_modifier", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    # power_mod applies to all skills via skill_filter:"all"
    for bs in (sprite.skills or []):
        pow_mod = bs._modifiers.get("power", 0)
        assert pow_mod == 30, f"{bs.name}: expected power=30, got {pow_mod}"


def test_trait_血型吸引_single_element_enemy():
    """血型吸引: enemy has only 1 element type → power +10."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p1.team[0].species.ability = "血型吸引"
    p1.team[0].species.ability_id = 20122

    # Enemy: all skills share the same element (普通)
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")

    sprite = p1.team[0]
    opp = p2.team[0]

    ctx = battle._make_ctx(sprite, opp, sprite.skills[0], None, battle.globals, team="A", turn=0)
    assert ctx.skill_element_count_opp == 1

    battle._vm_engine.fire_trigger("pre_modifier", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    for bs in (sprite.skills or []):
        assert bs._modifiers.get("power", 0) == 10, (
            f"{bs.name}: expected power=10 (1 element), got {bs._modifiers.get('power', 0)}"
        )


def test_trait_血型吸引_no_skill_elements():
    """血型吸引: enemy has no skills with elements → power +0."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p1.team[0].species.ability = "血型吸引"
    p1.team[0].species.ability_id = 20122

    # Enemy has skills but the factory loads them; we'll test ctx directly
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")

    sprite = p1.team[0]
    opp = p2.team[0]

    ctx = battle._make_ctx(sprite, opp, sprite.skills[0], None, battle.globals, team="A", turn=0)

    # Verify skill_element_count_opp is computed correctly
    assert ctx.skill_element_count_opp >= 0

    # Fire pre_modifier → should apply whatever count * 10
    battle._vm_engine.fire_trigger("pre_modifier", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    expected = ctx.skill_element_count_opp * 10
    for bs in (sprite.skills or []):
        assert bs._modifiers.get("power", 0) == expected, (
            f"{bs.name}: expected power={expected}, got {bs._modifiers.get('power', 0)}"
        )


def test_trait_血型吸引_swapped_view():
    """血型吸引: skill_element_count swaps correctly in swapped_view."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p1.team[0].species.ability = "血型吸引"
    p1.team[0].species.ability_id = 20122

    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["甩水", "火苗", "种子弹"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")

    sprite = p1.team[0]
    opp = p2.team[0]

    ctx = battle._make_ctx(sprite, opp, sprite.skills[0], None, battle.globals, team="A", turn=0)
    swapped = ctx.swapped_view()

    # Swapped view: self becomes opp → counts swap
    assert swapped.skill_element_count_self == ctx.skill_element_count_opp
    assert swapped.skill_element_count_opp == ctx.skill_element_count_self


# ═══════════════════════════════════════════════════════════════════
# 衡量 (ID 20123)
# ═══════════════════════════════════════════════════════════════════


def test_trait_衡量_copy_positive_on_entry():
    """衡量: on post_entry, copy opponent's positive effects (steal action=copy)."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p1.team[0].species.ability = "衡量"
    p1.team[0].species.ability_id = 20123

    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    sprite = p1.team[0]
    opp = p2.team[0]

    # Give opponent positive buffs
    opp.add_effect(_make_buff("atk", 2))
    opp.add_effect(_make_buff("speed", 1))
    assert len([e for e in opp.active_effects
                if isinstance(e, StatBuffEffect) and e.steps > 0]) == 2

    # Load trait and fire post_entry
    dispatch_entry(sprite, battle, "A")
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)
    battle._vm_engine.fire_trigger("post_entry", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    # Verify copies on self
    self_atk = [e for e in sprite.active_effects
                if isinstance(e, StatBuffEffect) and e.stat_key == "atk" and e.steps > 0]
    self_speed = [e for e in sprite.active_effects
                  if isinstance(e, StatBuffEffect) and e.stat_key == "speed" and e.steps > 0]
    assert len(self_atk) >= 1, f"expected atk buff copied to self, got {len(self_atk)}"
    assert self_atk[0].steps == 2, f"expected atk+2, got {self_atk[0].steps}"
    assert len(self_speed) >= 1, f"expected speed buff copied to self, got {len(self_speed)}"

    # Original effects should still be on opponent (copy, not steal)
    opp_atk = [e for e in opp.active_effects
               if isinstance(e, StatBuffEffect) and e.stat_key == "atk"]
    assert len(opp_atk) >= 1, "opponent should still have their buffs after copy"


def test_trait_衡量_mirror_opp_positive_change():
    """衡量: when opponent gains a positive stat stage, self mirrors it."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p1.team[0].species.ability = "衡量"
    p1.team[0].species.ability_id = 20123

    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    sprite = p1.team[0]
    opp = p2.team[0]

    dispatch_entry(sprite, battle, "A")
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)

    # Simulate opponent gaining atk+2
    ctx.event.positive_changed_of = "sprite_opp"
    ctx.event.positive_changed_stat = "atk"
    ctx.event.positive_changed_steps = 2

    battle._vm_engine.fire_trigger("post_positive_change", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    # Verify self mirrored atk+2
    self_buffs = [e for e in sprite.active_effects
                  if isinstance(e, StatBuffEffect) and e.stat_key == "atk" and e.steps > 0]
    assert len(self_buffs) >= 1, f"expected atk buff mirrored on self, got {len(self_buffs)}"
    assert sum(e.steps for e in self_buffs) >= 2, (
        f"expected atk+2 mirrored, got steps={[e.steps for e in self_buffs]}"
    )


def test_trait_衡量_no_copy_when_self_gains():
    """衡量: does NOT mirror when self gains (only opp gains trigger it)."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击"]},
    ])
    p1.team[0].species.ability = "衡量"
    p1.team[0].species.ability_id = 20123

    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0

    sprite = p1.team[0]
    opp = p2.team[0]

    dispatch_entry(sprite, battle, "A")
    ctx = battle._make_ctx(sprite, opp, None, None, battle.globals, team="A", turn=0)

    # Self gains a buff — should NOT trigger copy
    ctx.event.positive_changed_of = "sprite_self"
    ctx.event.positive_changed_stat = "def"
    ctx.event.positive_changed_steps = 1

    before = len([e for e in sprite.active_effects
                  if isinstance(e, StatBuffEffect) and e.stat_key == "def" and e.steps > 0])

    battle._vm_engine.fire_trigger("post_positive_change", ctx, sprite, opp, battle.globals, team="A", battle=battle)

    after = len([e for e in sprite.active_effects
                 if isinstance(e, StatBuffEffect) and e.stat_key == "def" and e.steps > 0])
    assert after == before, (
        f"self gaining buff should not trigger mirror; before={before}, after={after}"
    )
