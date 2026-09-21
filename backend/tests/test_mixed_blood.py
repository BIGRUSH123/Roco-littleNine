"""混血（3015）+ 色散「对混血精灵造成伤害+50%」。

游戏内文本 3015：「非本系血脉的精灵，包括非本系的系别血脉、首领血脉、奇异血脉、
污染血脉等。」→ 条件 `is_mixed_blood`（Ctx 预计算，backend/engine/bloodline.py）。
"""

from __future__ import annotations

from backend.engine.bloodline import is_mixed_blood
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry

factory = SimFactory()


def _battle(blood_a=None, blood_b=None, skills_a=("猛烈撞击",), skills_b=("猛烈撞击",),
            name_a="草衣虫", name_b="花衣蝶"):
    p1 = factory.build_player("A", [{"name": name_a, "skills": list(skills_a),
                                     "bloodline": blood_a}])
    p2 = factory.build_player("B", [{"name": name_b, "skills": list(skills_b),
                                     "bloodline": blood_b}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


# ═══════════════════════════════════════════════════════════════════
# 判定本身（纯函数）
# ═══════════════════════════════════════════════════════════════════

def test_native_bloodline_is_not_mixed():
    """草衣虫 系别 = 草；血脉 草 = 本系 → 非混血。"""
    battle = _battle(blood_a="草")
    assert is_mixed_blood(battle.player_a.active) is False


def test_other_element_bloodline_is_mixed():
    battle = _battle(blood_a="水")
    assert is_mixed_blood(battle.player_a.active) is True


def test_leader_bloodline_is_mixed():
    battle = _battle(blood_a="首领")
    assert is_mixed_blood(battle.player_a.active) is True


def test_pollution_and_strange_bloodlines_are_mixed():
    for blood in ("污染", "奇异"):
        battle = _battle(blood_a=blood)
        assert is_mixed_blood(battle.player_a.active) is True, blood


def test_dual_element_sprite_any_native_element_is_not_mixed():
    """花衣蝶 系别 = 虫/草 → 虫、草 任一为本系血脉都不算混血。"""
    for blood in ("虫", "草"):
        battle = _battle(blood_a=blood, name_a="花衣蝶")
        assert is_mixed_blood(battle.player_a.active) is False, blood


def test_empty_bloodline_is_not_mixed():
    battle = _battle(blood_a="草")
    battle.player_a.active.bloodline = ""
    assert is_mixed_blood(battle.player_a.active) is False


# ═══════════════════════════════════════════════════════════════════
# Ctx 寄存器 + 条件
# ═══════════════════════════════════════════════════════════════════

def test_ctx_registers_expose_mixed_blood_both_sides():
    battle = _battle(blood_a="草", blood_b="首领")
    ctx = battle._make_ctx(battle.player_a.active, battle.player_b.active,
                           None, None, battle.globals, team="A", turn=1)
    assert ctx.is_mixed_blood_self is False
    assert ctx.is_mixed_blood_opp is True


def test_is_mixed_blood_condition_reads_of_param():
    from backend.vm.cond import eval_one

    battle = _battle(blood_a="草", blood_b="首领")
    ctx = battle._make_ctx(battle.player_a.active, battle.player_b.active,
                           None, None, battle.globals, team="A", turn=1)
    assert eval_one(ctx, {"cond": "is_mixed_blood", "of": "sprite_opp"}) is True
    assert eval_one(ctx, {"cond": "is_mixed_blood", "of": "sprite_self"}) is False
    # 缺省 of = sprite_self
    assert eval_one(ctx, {"cond": "is_mixed_blood"}) is False


def test_mixed_blood_flag_survives_ctx_swap():
    battle = _battle(blood_a="草", blood_b="首领")
    ctx = battle._make_ctx(battle.player_a.active, battle.player_b.active,
                           None, None, battle.globals, team="A", turn=1)
    swapped = ctx.swapped_view()
    assert swapped.is_mixed_blood_self is True
    assert swapped.is_mixed_blood_opp is False


# ═══════════════════════════════════════════════════════════════════
# 色散
# ═══════════════════════════════════════════════════════════════════

def _sesan_damage(defender_bloodline: str) -> int:
    battle = _battle(blood_b=defender_bloodline, skills_a=("色散",))
    defender = battle.player_b.active
    hp_before = defender.current_hp
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    return hp_before - defender.current_hp


def test_sesan_damage_boosted_against_mixed_blood():
    mixed = _sesan_damage("首领")
    normal = _sesan_damage("草")
    assert normal > 0
    assert mixed == round(normal * 1.5) or abs(mixed - normal * 1.5) <= 1
