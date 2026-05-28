"""tests/sim/test_charge.py — 蓄力机制测试

测试 _gate_charge_vm：
- 首次使用蓄力技能 → 进入蓄力状态（_charging=True）
- 蓄力中再次使用同一技能 → 释放蓄力（_charging=False, charged 标记）
- 蓄力中使用其他非蓄力技能 → 被阻挡
- 蓄力中使用 usable_while_charging 技能 → 取消蓄力并正常使用
- 换宠中断蓄力 → 蓄力状态清除
- 释放蓄力后 → charged 效果存在
"""

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.battle import Battle
from backend.sim.factory import SimFactory


def _make_battle(skills_a=None, skills_b=None):
    """创建测试 Battle，两方都有基础精灵。"""
    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "水灵", "skills": skills_a or ["猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "水灵", "skills": skills_b or ["猛烈撞击"]},
    ])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    return b


def _get_skill(sprite, index=0):
    return sprite.skills[index]


# ═══════════════════════════════════════════════════════════════
# 蓄力进出
# ═══════════════════════════════════════════════════════════════

def test_enter_charge():
    """首次使用蓄力技能 → _charging=True, charged_skill_index=index。"""
    b = _make_battle(skills_a=["龙吟"], skills_b=["猛烈撞击"])
    sprite = b.player_a.active
    bs = _get_skill(sprite, 0)
    from backend.sim.action import Action
    action = Action(kind="skill", skill_index=0)

    result = b._gate_charge_vm(sprite, bs, action)
    assert result is True, "应进入蓄力"
    assert sprite._charging is True
    assert sprite._charged_skill_index == 0
    # charging state effect 应存在
    charging_effs = [e for e in sprite.active_effects if e.name == "charging"]
    assert len(charging_effs) == 1
    print("  [OK] 进入蓄力: _charging=True, charged_skill_index=0, charging effect存在")


def test_release_charge():
    """蓄力中再次使用同一技能 → 释放蓄力，charged 效果存在。"""
    b = _make_battle(skills_a=["龙吟"], skills_b=["猛烈撞击"])
    sprite = b.player_a.active
    bs = _get_skill(sprite, 0)
    from backend.sim.action import Action
    action = Action(kind="skill", skill_index=0)

    # 第一次：进入蓄力
    result1 = b._gate_charge_vm(sprite, bs, action)
    assert result1 is True

    # 第二次：释放蓄力
    result2 = b._gate_charge_vm(sprite, bs, action)
    assert result2 is None, "应释放蓄力(pass through)"
    assert sprite._charging is False
    assert sprite._charged_skill_index == -1
    # charging 消失，charged 出现
    charging_effs = [e for e in sprite.active_effects if e.name == "charging"]
    charged_effs = [e for e in sprite.active_effects if e.name == "charged"]
    assert len(charging_effs) == 0, "charging应清除"
    assert len(charged_effs) == 1, "charged应存在"
    print("  [OK] 释放蓄力: _charging=False, charged effect存在")


def test_charge_block_other_skill():
    """蓄力中使用其他非usable_while_charging技能 → 被阻挡。"""
    b = _make_battle(skills_a=["龙吟", "猛烈撞击"], skills_b=["猛烈撞击"])
    sprite = b.player_a.active
    from backend.sim.action import Action

    # 进入蓄力
    b._gate_charge_vm(sprite, _get_skill(sprite, 0), Action(kind="skill", skill_index=0))

    # 尝试使用其他技能
    result = b._gate_charge_vm(sprite, _get_skill(sprite, 1), Action(kind="skill", skill_index=1))
    assert result is False, "应被阻挡"
    assert sprite._charging is True, "蓄力状态应保持"
    print("  [OK] 蓄力阻挡其他技能")


def test_usable_while_charging():
    """蓄力中使用usable_while_charging技能 → 取消蓄力，正常通过。"""
    b = _make_battle(skills_a=["龙吟", "龙血"], skills_b=["猛烈撞击"])
    sprite = b.player_a.active
    from backend.sim.action import Action

    # 进入蓄力（龙吟）
    b._gate_charge_vm(sprite, _get_skill(sprite, 0), Action(kind="skill", skill_index=0))
    assert sprite._charging is True

    # 使用龙血（usable_while_charging=true）
    result = b._gate_charge_vm(sprite, _get_skill(sprite, 1), Action(kind="skill", skill_index=1))
    assert result is None, "应通过(取消蓄力)"
    assert sprite._charging is False, "蓄力应取消"
    assert sprite._charged_skill_index == -1
    # charging effect 应清除
    charging_effs = [e for e in sprite.active_effects if e.name == "charging"]
    assert len(charging_effs) == 0, "charging effect应清除"
    print("  [OK] usable_while_charging: 取消蓄力并正常通过")


# ═══════════════════════════════════════════════════════════════
# 多技能蓄力
# ═══════════════════════════════════════════════════════════════

def test_multiple_charge_skills():
    """龙之利爪蓄力→释放→再次蓄力循环。"""
    b = _make_battle(skills_a=["龙之利爪"], skills_b=["猛烈撞击"])
    sprite = b.player_a.active
    bs = _get_skill(sprite, 0)
    from backend.sim.action import Action
    action = Action(kind="skill", skill_index=0)

    # Round 1: 进入蓄力
    assert b._gate_charge_vm(sprite, bs, action) is True
    assert sprite._charging is True

    # Round 2: 释放
    assert b._gate_charge_vm(sprite, bs, action) is None
    assert sprite._charging is False

    # Round 3: 再次进入蓄力
    assert b._gate_charge_vm(sprite, bs, action) is True
    assert sprite._charging is True

    # Round 4: 再次释放
    assert b._gate_charge_vm(sprite, bs, action) is None
    assert sprite._charging is False
    print("  [OK] 多轮蓄力释放循环")


def test_non_charge_skill_skips_gate():
    """非蓄力技能 → gate 直接 pass through。"""
    b = _make_battle(skills_a=["猛烈撞击"], skills_b=["猛烈撞击"])
    sprite = b.player_a.active
    bs = _get_skill(sprite, 0)
    from backend.sim.action import Action

    result = b._gate_charge_vm(sprite, bs, Action(kind="skill", skill_index=0))
    assert result is None, "非蓄力技能应通过"
    assert getattr(sprite, '_charging', False) is False
    print("  [OK] 非蓄力技能直接通过")


# ═══════════════════════════════════════════════════════════════
# 换宠中断蓄力
# ═══════════════════════════════════════════════════════════════

def test_switch_interrupts_charge():
    """换宠应中断蓄力（_resolve_switch 逻辑）。需要至少2只精灵才能换宠。"""
    factory = SimFactory()
    p1 = factory.build_player("A", [
        {"name": "水灵", "skills": ["龙吟"]},
        {"name": "水灵", "skills": ["猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "水灵", "skills": ["猛烈撞击"]},
    ])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list

    sprite = b.player_a.active
    from backend.sim.action import Action

    # 进入蓄力
    b._gate_charge_vm(sprite, _get_skill(sprite, 0), Action(kind="skill", skill_index=0))
    assert sprite._charging is True

    # 换宠 → 旧精灵蓄力应中断
    events = b._resolve_switch('A', Action(kind="switch", switch_index=1))

    interrupt_events = [e for e in events if "蓄力中断" in e]
    assert len(interrupt_events) >= 1, f"应有'蓄力中断'事件, got {events}"
    assert sprite._charging is False
    assert sprite._charged_skill_index == -1
    print(f"  [OK] 换宠中断蓄力: {interrupt_events[0]}")


# ═══════════════════════════════════════════════════════════════
# 完整对局蓄力流程
# ═══════════════════════════════════════════════════════════════

def test_full_battle_charge_flow():
    """完整对战蓄力流程：通过 gate 验证 T1蓄力 T2释放。"""
    b = _make_battle(skills_a=["龙吟"], skills_b=["猛烈撞击"])
    sprite = b.player_a.active
    bs = _get_skill(sprite, 0)
    from backend.sim.action import Action
    action = Action(kind="skill", skill_index=0)

    # T1: 进入蓄力
    r1 = b._gate_charge_vm(sprite, bs, action)
    assert r1 is True
    assert sprite._charging is True
    charging = [e for e in sprite.active_effects if e.name == "charging"]
    assert len(charging) == 1, f"T1应有charging, got {[e.name for e in sprite.active_effects]}"
    print(f"  T1: charging={sprite._charging}, effects={[e.name for e in sprite.active_effects]}")

    # T2: 释放蓄力
    r2 = b._gate_charge_vm(sprite, bs, action)
    assert r2 is None, "应释放蓄力"
    assert sprite._charging is False
    charged = [e for e in sprite.active_effects if e.name == "charged"]
    assert len(charged) == 1, f"T2应有charged, got {[e.name for e in sprite.active_effects]}"
    charging2 = [e for e in sprite.active_effects if e.name == "charging"]
    assert len(charging2) == 0, "T2不应有charging"
    print(f"  T2: charging={sprite._charging}, charged={len(charged)}, effects={[e.name for e in sprite.active_effects]}")

    print("  [OK] 完整对战蓄力流程: T1进入 T2释放")


if __name__ == "__main__":
    test_enter_charge()
    test_release_charge()
    test_charge_block_other_skill()
    test_usable_while_charging()
    test_multiple_charge_skills()
    test_non_charge_skill_skips_gate()
    test_switch_interrupts_charge()
    test_full_battle_charge_flow()
    print("\n  [ALL CHARGE TESTS PASSED]")
