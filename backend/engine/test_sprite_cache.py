from backend.common.models import SpeciesStats
from backend.engine.replayer import JournalReplayer
from backend.sim.battle import Battle
from backend.sim.globals import GlobalEffects
from backend.sim.player import Player
from backend.sim.resolver import SkillResolver
from backend.sim.sprite import Sprite
from backend.vm.effect import AbnormalEffect, StatBuffEffect
from backend.vm.journal import Dispel, Exchange, Steal


def _sprite() -> Sprite:
    species = SpeciesStats(
        name="缓存测试精灵",
        hp=100,
        atk=100,
        sp_atk=100,
        def_=100,
        sp_def=100,
        speed=100,
    )
    return Sprite(
        species=species,
        current_hp=100,
        max_hp=100,
        initial_stats={
            "atk": 100,
            "sp_atk": 100,
            "def": 100,
            "sp_def": 100,
            "speed": 100,
        },
    )


def test_clear_effects_invalidates_stat_cache_for_removed_modifier():
    """作用域清理删除 modifier 后，四维属性必须立即回到基础值。"""
    sprite = _sprite()
    sprite._modifiers["atk"] = 0.5
    sprite._mod_scopes["atk"] = "battlefield"
    sprite._invalidate_stat_cache()
    assert sprite.atk_with_modifiers == 150

    sprite.clear_effects("battlefield")

    assert "atk" not in sprite._modifiers
    assert sprite.atk_with_modifiers == 100


def test_exchange_effects_invalidates_both_effect_caches():
    """交换效果列表后，双方异常层数查询必须立即反映新归属。"""
    sprite_a = _sprite()
    sprite_b = _sprite()
    sprite_a.add_effect(AbnormalEffect(
        name="冻结",
        source="测试",
        scope="persistent",
        stacks=5,
    ))
    assert sprite_a.get_stacks("冻结") == 5
    assert sprite_b.get_stacks("冻结") == 0
    battle = Battle(
        Player("A", [sprite_a]),
        Player("B", [sprite_b]),
        verbose=False,
    )

    JournalReplayer(
        sprite_a,
        sprite_b,
        battle.globals,
        battle._vm_engine.registry,
        team="A",
        battle=battle,
    ).replay([Exchange(target="sprite_self", what="effects")])

    assert sprite_a.get_stacks("冻结") == 0
    assert sprite_b.get_stacks("冻结") == 5


def test_trait_unload_invalidates_cache_after_faint_cleanup():
    """力竭卸载 persistent 异常后不能留下幽灵层数。"""
    sprite = _sprite()
    opponent = _sprite()
    sprite.add_effect(AbnormalEffect(
        name="冻结",
        source="测试",
        scope="persistent",
        stacks=4,
    ))
    assert sprite.get_stacks("冻结") == 4
    battle = Battle(
        Player("A", [sprite]),
        Player("B", [opponent]),
        verbose=False,
    )

    battle._vm_engine.trait_loader.unload_for_sprite(sprite, "faint")

    assert not sprite.active_effects
    assert sprite.get_stacks("冻结") == 0


def test_source_dispel_invalidates_effect_cache():
    """按来源驱散异常后，层数缓存必须同步清零。"""
    sprite = _sprite()
    opponent = _sprite()
    sprite.add_effect(AbnormalEffect(
        name="冻结",
        source="冰系技能",
        scope="persistent",
        stacks=3,
    ))
    assert sprite.get_stacks("冻结") == 3
    battle = Battle(
        Player("A", [sprite]),
        Player("B", [opponent]),
        verbose=False,
    )

    JournalReplayer(
        sprite,
        opponent,
        battle.globals,
        battle._vm_engine.registry,
        team="A",
        battle=battle,
    ).replay([Dispel(
        target="sprite_self",
        what="abnormal",
        source="冰系技能",
    )])

    assert not sprite.active_effects
    assert sprite.get_stacks("冻结") == 0


def test_steal_positive_invalidates_target_effect_cache():
    """增益被偷走后，原持有者不能继续使用旧属性阶段。"""
    thief = _sprite()
    target = _sprite()
    target.add_effect(StatBuffEffect(
        name="攻击强化",
        source="测试",
        stat_key="atk",
        steps=3,
    ))
    assert target.effective_stat("atk") == 130
    battle = Battle(
        Player("A", [thief]),
        Player("B", [target]),
        verbose=False,
    )

    JournalReplayer(
        thief,
        target,
        battle.globals,
        battle._vm_engine.registry,
        team="A",
        battle=battle,
    ).replay([Steal(from_target="sprite_opp", what="positive")])

    assert target.effective_stat("atk") == 100
    assert thief.effective_stat("atk") == 130


def test_cooldown_expiry_invalidates_effect_cache():
    """冷却归零移除效果时，异常层数缓存不能保留旧值。"""
    sprite = _sprite()
    sprite.add_effect(AbnormalEffect(
        name="冻结",
        source="测试",
        scope="persistent",
        stacks=2,
        cooldown=1,
    ))
    assert sprite.get_stacks("冻结") == 2

    assert sprite.use_cooldown("冻结") == 0

    assert not sprite.active_effects
    assert sprite.get_stacks("冻结") == 0


def test_burn_decay_updates_cached_stacks():
    """回合末灼烧衰减必须同步更新异常层数缓存。"""
    sprite = _sprite()
    sprite.add_effect(AbnormalEffect(
        name="灼烧",
        source="测试",
        scope="persistent",
        stacks=7,
        tick_damage_pct=0.01,
        decay_on_tick=True,
    ))
    assert sprite.get_stacks("灼烧") == 7

    SkillResolver.turn_end({"A": sprite}, GlobalEffects())

    assert sprite.active_effects[0].stacks == 3
    assert sprite.get_stacks("灼烧") == 3
