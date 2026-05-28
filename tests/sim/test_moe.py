"""tests/sim/test_moe.py — 萌化机制测试

测试 apply_moe / remove_moe / _reset_moe_state：
- 首次萌化沿进化链向下退化
- 累计萌化层数
- 最低形态免疫
- 进化之力清空萌化状态
- 萌化效果同步（StatusEffect + _moe_position）
"""

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.battle import Battle
from backend.sim.factory import SimFactory

factory = SimFactory()


def _make_battle(skills=None):
    """创建含物种数据库的战斗实例，萌化需要 species_db 查进化链。"""
    p1 = factory.build_player("A", [
        {"name": "水灵", "skills": skills or ["猛烈撞击", "甩水"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "水灵", "skills": ["猛烈撞击"]},
    ])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    return b


# ═══════════════════════════════════════════════════════════════
# 基础萌化退化
# ═══════════════════════════════════════════════════════════════

def test_apply_moe_one_stack():
    """1层萌化：水灵 → 波波拉。"""
    b = _make_battle()
    sprite = b.player_a.active
    old_name = sprite.name
    assert old_name == "水灵"

    events = sprite.apply_moe(1, b)
    assert sprite.name == "波波拉", f"应退化为波波拉，实际{sprite.name}"
    assert sprite._moe_position == 1
    assert sprite._moe_origin is not None, "应保存原始形态"
    assert sprite._moe_origin.name == "水灵"
    moe_effs = [e for e in sprite.active_effects if e.name == "萌化"]
    assert len(moe_effs) == 1, f"应有萌化状态, got {[e.name for e in sprite.active_effects]}"
    assert moe_effs[0].stacks == 1
    assert any("萌化" in ev for ev in events), f"事件应含'萌化', got {events}"
    print(f"  [OK] 1层萌化: 水灵→波波拉, effects={[e.name for e in sprite.active_effects]}")


def test_apply_moe_two_stacks():
    """2层萌化：水灵 → 水蓝蓝。"""
    b = _make_battle()
    sprite = b.player_a.active

    sprite.apply_moe(2, b)
    assert sprite.name == "水蓝蓝", f"应退化为水蓝蓝，实际{sprite.name}"
    assert sprite._moe_position == 2
    moe_effs = [e for e in sprite.active_effects if e.name == "萌化"]
    assert moe_effs[0].stacks == 2
    print("  [OK] 2层萌化: 水灵→水蓝蓝(层数2)")


def test_apply_moe_cumulative():
    """累计萌化：先1层再1层 = 2层 → 水蓝蓝。"""
    b = _make_battle()
    sprite = b.player_a.active

    sprite.apply_moe(1, b)
    assert sprite.name == "波波拉"
    assert sprite._moe_position == 1

    sprite.apply_moe(1, b)
    assert sprite.name == "水蓝蓝", f"累计2层应为水蓝蓝，实际{sprite.name}"
    assert sprite._moe_position == 2
    print("  [OK] 累计萌化: +1→波波拉 +1→水蓝蓝")


# ═══════════════════════════════════════════════════════════════
# 最低形态免疫
# ═══════════════════════════════════════════════════════════════

def test_moe_immune_at_bottom():
    """已到最低形态（水蓝蓝）再萌化 → 免疫。"""
    b = _make_battle()
    sprite = b.player_a.active

    # 直接退到最低
    sprite.apply_moe(3, b)
    assert sprite.name == "水蓝蓝"
    assert sprite._moe_position == 2  # 链条最多2层

    events = sprite.apply_moe(1, b)
    assert sprite.name == "水蓝蓝", "不应再退"
    assert sprite._moe_position == 2
    assert any("免疫" in ev for ev in events), f"应有免疫事件, got {events}"
    print(f"  [OK] 最低形态免疫: {events[0]}")


# ═══════════════════════════════════════════════════════════════
# 恢复萌化
# ═══════════════════════════════════════════════════════════════

def test_remove_moe_partial():
    """移除1层萌化：水蓝蓝(2层) → 波波拉(1层)。"""
    b = _make_battle()
    sprite = b.player_a.active
    sprite.apply_moe(2, b)
    assert sprite.name == "水蓝蓝"

    removed = sprite.remove_moe(1, b)
    assert removed == 1
    assert sprite.name == "波波拉"
    assert sprite._moe_position == 1
    print("  [OK] 部分恢复: 水蓝蓝→波波拉")


def test_remove_moe_full():
    """完全移除萌化：水蓝蓝(2层) → 水灵(0层)。"""
    b = _make_battle()
    sprite = b.player_a.active
    old_skills = list(sprite.skills)
    sprite.apply_moe(2, b)
    assert sprite.name == "水蓝蓝"

    removed = sprite.remove_moe(2, b)
    assert removed == 2
    assert sprite.name == "水灵", f"应恢复为水灵，实际{sprite.name}"
    assert sprite._moe_position == 0
    assert sprite._moe_origin is None, "完全恢复后 _moe_origin 应为 None"
    moe_effs = [e for e in sprite.active_effects if e.name == "萌化"]
    assert len(moe_effs) == 0, "萌化效果应清除"
    # 技能应恢复
    assert [bs.name for bs in sprite.skills] == [bs.name for bs in old_skills], \
        f"技能应恢复, got {[bs.name for bs in sprite.skills]}"
    print("  [OK] 完全恢复: 水蓝蓝→水灵, 技能已恢复")


def test_remove_moe_zero():
    """0层萌化 remove → 返回0（无操作）。"""
    b = _make_battle()
    sprite = b.player_a.active
    assert sprite._moe_position == 0
    removed = sprite.remove_moe(1, b)
    assert removed == 0
    assert sprite.name == "水灵"
    print("  [OK] 0层remove无操作")


# ═══════════════════════════════════════════════════════════════
# 萌化与进化之力交互
# ═══════════════════════════════════════════════════════════════

def test_moe_reset_by_evolution():
    """进化之力应重置萌化状态（_reset_moe_state）。"""
    b = _make_battle()
    sprite = b.player_a.active

    # 先萌化
    sprite.apply_moe(1, b)
    assert sprite._moe_position == 1

    # 模拟进化之力重置
    sprite._reset_moe_state()
    assert sprite._moe_position == 0
    assert sprite._moe_origin is None
    assert sprite._moe_chain == []
    print("  [OK] 进化之力重置萌化")


def test_moe_removes_leader_buff():
    """萌化后应清除进化之力的首领化增益。"""
    b = _make_battle()
    sprite = b.player_a.active
    from backend.vm.effect import StatBuffEffect

    # 模拟首领化增益
    for key in ['atk', 'sp_atk', 'def', 'sp_def', 'speed']:
        sprite.add_effect(StatBuffEffect(
            name='首领化', stat_key=key, steps=2,
            scope='permanent', source='进化之力',
        ))
    assert len([e for e in sprite.active_effects if e.name == '首领化']) == 5

    # 萌化1层
    sprite.apply_moe(1, b)
    leader_buffs = [e for e in sprite.active_effects if e.name == '首领化']
    assert len(leader_buffs) == 0, f"首领化增益应被清除，实际{len(leader_buffs)}"
    print("  [OK] 萌化清除首领化增益")


# ═══════════════════════════════════════════════════════════════
# 萌化链验证
# ═══════════════════════════════════════════════════════════════

def test_moe_chain_correct():
    """验证水灵的萌化链：[水灵, 波波拉, 水蓝蓝]。"""
    b = _make_battle()
    sprite = b.player_a.active
    sprite._build_moe_chain(b)
    chain_names = [s.name for s in sprite._moe_chain]
    assert chain_names == ["水灵", "波波拉", "水蓝蓝"], \
        f"进化链应为 水灵→波波拉→水蓝蓝，got {chain_names}"
    print(f"  [OK] 萌化链: {'→'.join(chain_names)}")


def test_moe_status_effect_sync():
    """_sync_moe_status_effect：层数与 StatusEffect 同步。"""
    b = _make_battle()
    sprite = b.player_a.active

    sprite._moe_position = 3
    sprite._sync_moe_status_effect()
    moe = [e for e in sprite.active_effects if e.name == "萌化"]
    assert len(moe) == 1
    assert moe[0].stacks == 3

    sprite._moe_position = 0
    sprite._sync_moe_status_effect()
    moe = [e for e in sprite.active_effects if e.name == "萌化"]
    assert len(moe) == 0
    print("  [OK] 萌化效果同步")


if __name__ == "__main__":
    test_apply_moe_one_stack()
    test_apply_moe_two_stacks()
    test_apply_moe_cumulative()
    test_moe_immune_at_bottom()
    test_remove_moe_partial()
    test_remove_moe_full()
    test_remove_moe_zero()
    test_moe_reset_by_evolution()
    test_moe_removes_leader_buff()
    test_moe_chain_correct()
    test_moe_status_effect_sync()
    print("\n  [ALL MOE TESTS PASSED]")
