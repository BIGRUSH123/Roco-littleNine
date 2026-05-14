"""tests/sim/test_items.py — 道具系统集成测试"""

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from scripts.sim.factory import SimFactory
from scripts.sim.player import Item
from scripts.common.sprite_db import SpriteDB

factory = SimFactory()
sprite_db = SpriteDB(_PROJ)


def _find_leader_form(number: str):
    """查找同编号的首领形态。"""
    for p in sprite_db._by_number.get(number, []):
        s = sprite_db._read_one(p)
        if s and '首领' in (s.form or ''):
            return s
    return None


def test_wish_replaces_skill0_with_bloodline_skill():
    """愿力：使用后一技能被血脉技能替换"""
    player = factory.build_player("A", [
        {"name": "水灵", "skills": ["猛烈撞击", "甩水", "防御"], "bloodline": "水"},
    ])
    sprite = player.team[0]
    old_name = sprite.skills[0].name
    assert old_name == "猛烈撞击", f"一技能应为猛烈撞击，实际为{old_name}"

    bl_skill_id = sprite.bloodline_skills.get("水")
    assert bl_skill_id is not None, "水灵应有水系血脉技能"

    from scripts.common.skill_trait_ids import SKILL_ID_TO_NAME
    bl_name = SKILL_ID_TO_NAME.get(bl_skill_id)
    assert bl_name is not None, f"技能ID {bl_skill_id} 应有名称"
    assert bl_name == "水弹枪", f"水系血脉技能应为水弹枪，实际为{bl_name}"

    new_skills = factory._build_skill_list([bl_name])
    assert new_skills, f"应能加载技能: {bl_name}"
    sprite.skills[0] = new_skills[0]
    assert sprite.skills[0].name == "水弹枪", f"一技能应为水弹枪，实际为{sprite.skills[0].name}"
    print(f"  [OK] 愿力: {sprite.name} skill[0] 猛烈撞击→{sprite.skills[0].name}")


def test_evolution_power_evolves_to_leader_form():
    """进化之力：同编号有首领形态的精灵进化为首领"""
    player = factory.build_player("A", [
        {"name": "水灵", "skills": ["猛烈撞击", "甩水", "防御"]},
    ])
    sprite = player.team[0]
    old_name = sprite.name
    old_hp_ratio = sprite.hp_pct

    boss = _find_leader_form(sprite.species.number)
    assert boss is not None, "水灵(number=10)应有首领形态"
    assert "首领" in (boss.form or ""), f"首领形态名称应包含'首领': {boss.display_name()}"

    from scripts.common.formulas import StatsCalc
    calc = StatsCalc()
    result = calc.compute(boss, nature=sprite.nature, iv=sprite.iv)
    sprite.species = boss
    sprite.initial_stats = dict(result.final_stats)
    sprite.max_hp = result.final_stats['hp']
    sprite.current_hp = max(1, round(result.final_stats['hp'] * old_hp_ratio))
    sprite.bloodline_skills = dict(boss.bloodline_skills)

    from scripts.sim.sprite import StatusEffect
    for key in ['atk', 'sp_atk', 'def', 'sp_def', 'speed']:
        sprite.add_effect(StatusEffect(
            name='首领化', category='stat', stat_key=key, steps=2,
            scope='permanent', source='进化之力',
        ))

    assert sprite.name == boss.display_name()
    assert sprite.species.form == boss.form
    buffs = [e for e in sprite.effects if e.name == '首领化']
    assert len(buffs) == 5, f"首领化应有5条增益，实际{len(buffs)}"
    for key in ['atk', 'sp_atk', 'def', 'sp_def', 'speed']:
        stat_eff = sprite.effective_stat(key)
        base = sprite.initial_stats.get(key, 0)
        assert stat_eff > base, f"{key}: 有效值{stat_eff} 应 > 基础值{base}"

    print(f"  [OK] 进化之力: {old_name} → {sprite.name} (+2级全属性)")


def test_wish_cooldown_enforced():
    """愿力：两次使用需间隔3回合（含使用回合）"""
    item = Item.wish()
    assert item.name == "愿力"
    assert item.max_uses == 2
    assert item.cooldown_turns == 4

    item.last_use_turn = 0
    item.uses = 0
    assert item.can_use(1), "第1回合可用"
    item.use(1)
    assert item.uses == 1

    assert not item.can_use(2), "第2回合不可用"
    assert not item.can_use(3), "第3回合不可用"
    assert not item.can_use(4), "第4回合不可用"

    assert item.can_use(5), "第5回合可用"
    item.use(5)
    assert item.uses == 2
    assert not item.can_use(6), "用完后不可用"
    print("  [OK] 愿力冷却: 间隔3回合 + 最多2次")


def test_evolution_power_single_use():
    """进化之力：只能使用一次"""
    item = Item.leader()
    assert item.name == "进化之力"
    assert item.max_uses == 1

    item.use(1)
    assert item.uses == 1
    assert not item.can_use(2), "使用后应不可用"
    print("  [OK] 进化之力: 仅一次使用")


def test_wish_requires_bloodline_match():
    """愿力：需要精灵血脉与血脉技能匹配"""
    player = factory.build_player("A", [
        {"name": "水灵", "skills": ["猛烈撞击"], "bloodline": "水"},
    ])
    sprite = player.team[0]
    assert "水" in sprite.bloodline_skills, "水灵应有水系血脉技能"
    bl_id = sprite.bloodline_skills["水"]
    assert bl_id == 10558, f"水灵水系血脉技能ID应为10558，实际为{bl_id}"
    print("  [OK] 愿力: 血脉匹配检查通过")


def test_no_leader_form_no_evolution():
    """进化之力：无首领形态的精灵无法进化"""
    factory.build_player("A", [
        {"name": "水灵", "skills": ["猛烈撞击"]},
    ])
    boss = _find_leader_form("99999")
    assert boss is None, "不存在的编号应返回None"
    print("  [OK] 进化之力: 无效编号返回None")


def test_full_item_battle_flow():
    """完整对战道具流程：创建对局，验证道具可用"""
    from scripts.sim.battle import Battle

    p1 = factory.build_player("Alice", [
        {"name": "水灵", "skills": ["猛烈撞击", "甩水"], "bloodline": "水"},
    ], item=Item.wish())
    p2 = factory.build_player("Bob", [
        {"name": "火神", "skills": ["吹火", "天火"]},
    ])

    battle = Battle(player_a=p1, player_b=p2)
    battle.species_db = sprite_db
    battle.skill_loader = factory._build_skill_list

    assert p1.item is not None
    assert p1.item.name == "愿力"
    assert p2.item is None

    boss = _find_leader_form(p1.team[0].species.number)
    assert boss is not None, "水灵应有首领形态"

    print("  [OK] 完整对战流程: 道具绑定 + 首领形态查询正常")


if __name__ == "__main__":
    test_wish_replaces_skill0_with_bloodline_skill()
    test_evolution_power_evolves_to_leader_form()
    test_wish_cooldown_enforced()
    test_evolution_power_single_use()
    test_wish_requires_bloodline_match()
    test_no_leader_form_no_evolution()
    test_full_item_battle_flow()
    print("\n  [ALL TESTS PASSED]")
