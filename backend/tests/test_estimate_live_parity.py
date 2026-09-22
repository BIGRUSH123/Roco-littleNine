"""估伤（`SkillResolver.calc_damage`）与实战同口径：技能级/精灵级倍率 + 技能自身同回合修正。

背景（2026-09-22）：估伤此前只读 `SkillUse.modifiers`（旧 kind 效果层，IR 语料下恒空），
于是「技能级/精灵级 power_mult/damage_mult」与「技能自身写在 effects[] 里的同回合
power_mod」对估伤完全不可见——实测精灵级 `power_mult=1.5` 时实战 39→59、估伤恒 33；
魔能爆实战 1/61/121/201、估伤恒 1。这直接影响 AI 选招与 BC 标签质量。

口径：与 `engine/snapshot.build_ctx`（倍率相加）+ `engine/modifiers.adjust_damage`
（同回合修正**先算伤害再后乘取整**）逐项对齐。
"""
from pathlib import Path

import pytest

from backend.sim.action import Action
from backend.sim.battle import Battle, _load_permanent_skill_mods_for_sprite
from backend.sim.battleskill import SkillUse
from backend.sim.factory import SimFactory

_PROJ = Path(__file__).resolve().parent.parent.parent


def _battle(a_skills=("猛烈撞击",), a_name="草衣虫", b_name="花衣蝶"):
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": a_name, "skills": list(a_skills)}])
    p2 = factory.build_player("B", [{"name": b_name, "skills": ["猛烈撞击"]}])
    b = Battle(p1, p2, verbose=False)
    b.player_a.active_index = b.player_b.active_index = 0
    return b


def _live(b, index: int = 0) -> int:
    opp = b.player_b.active
    hp0 = opp.current_hp
    b._execute_skill_vm('A', Action(kind='skill', skill_index=index))
    return hp0 - opp.current_hp


def _estimate(b, index: int = 0) -> int:
    me, opp = b.player_a.active, b.player_b.active
    dmg, _ = b._resolver.calc_damage(
        me, opp, SkillUse(battle_skill=me.skills[index], skill_index=index),
        b.globals, attacker_team='A')
    return dmg


# ── 技能自身同回合修正（魔能爆：每消耗 1 点能量基础威力 +20）──

@pytest.mark.parametrize("energy", [0, 3, 6, 10])
def test_same_turn_power_mod_parity_monengbao(energy):
    """魔能爆：估伤必须随能量线性上升，且与实战逐值相等（1/61/121/201）。"""
    b = _battle(["魔能爆"])
    b.player_a.active.energy = energy
    est = _estimate(b)
    live = _live(b)
    assert b.player_a.active.energy == 0, "用后能量应清零"
    assert est == live, f"energy={energy}: 估伤 {est} ≠ 实战 {live}"


def test_same_turn_power_mod_scales_with_energy():
    """同回合 power_add 要真的进伤害（不是恒 1）。"""
    def est_at(energy: int) -> int:
        b = _battle(["魔能爆"])
        b.player_a.active.energy = energy
        return _estimate(b)

    assert est_at(10) > est_at(0) * 50, f"能量未进威力: {est_at(0)} → {est_at(10)}"


# ── 技能级 / 精灵级倍率 ──

def test_sprite_level_power_mult_parity():
    """精灵级 power_mult=1.5：实战与估伤同时提升且相等。"""
    b0 = _battle()
    base_live = _live(b0)
    b = _battle()
    b.player_a.active._modifiers['power_mult'] = 1.5
    assert _estimate(b) == _live(b), "精灵级 power_mult 对估伤不可见"
    assert _estimate(b) == pytest.approx(base_live * 1.5, rel=0.03)


def test_skill_level_power_mult_parity():
    """技能级 power_mult=1.5（`skill.<名>.power_mult`）：同上。"""
    b = _battle()
    me = b.player_a.active
    me._modifiers["skill.猛烈撞击.power_mult"] = 1.5
    _load_permanent_skill_mods_for_sprite(me)
    assert _estimate(b) == _live(b), "技能级 power_mult 对估伤不可见"


def test_same_type_attack_bonus_is_1_25_in_both_paths():
    """本系加成 = 1.25（用户 2026-09-22 确认的游戏真值）。

    实战 `op_hit._stab_mult` 此前写 1.5 → 同一手实战比估伤高 20%；现在两边都 1.25。
    """
    from backend.vm.ops.hit import _stab_mult
    assert _stab_mult("虫", ("虫", "草")) == 1.25
    assert _stab_mult("普通", ("虫", "草")) == 1.0

    b = _battle(["虫刺"], b_name="雪怪")        # 草衣虫（虫/草）用虫刺 → 本系
    est = _estimate(b)
    live = _live(b)
    assert est == live, f"本系攻击：估伤 {est} ≠ 实战 {live}"


def test_damage_mult_and_defender_reduction_parity():
    """damage_mult=1.3 与「防御方精灵级减伤 0.5」都要进两边同一条口径。"""
    b = _battle()
    b.player_a.active._modifiers['damage_mult'] = 1.3
    assert _estimate(b) == _live(b)

    b2 = _battle()
    b2.player_b.active._modifiers['damage_reduction'] = 0.5
    est = _estimate(b2)
    live = _live(b2)
    assert est == live, f"防御方减伤未同口径: 估伤 {est} / 实战 {live}"
    assert est < _estimate(_battle()) * 0.6, "减伤没生效"


# ── 位置条件（skill_at）下的同回合修正：不能无条件计入 ──

def test_skill_at_conditional_power_mod_is_evaluated_not_always_counted():
    """械斗「位于 1 号位时威力+60」：位置不符时两边都不加，符合时都加。

    即估伤必须**求值** `when{skill_at}`，而不是把 then 分支无条件计入
    （否则 45 威力会被估成 105）。
    """
    b0 = _battle(["械斗", "猛烈撞击"])
    b1 = _battle(["猛烈撞击", "械斗"])
    # skill_index=0 / 1 显式传入：引擎侧每手都会带，估伤侧由调用方给
    est0, est1 = _estimate(b0, 0), _estimate(b1, 1)
    assert est0 == _live(_battle(["械斗", "猛烈撞击"]), 0)  # 位置不符：不加 +60
    assert est1 > est0, "位置符合时 +60 未计入估伤"
    assert est1 == _live(b1, 1), f"位置条件估伤 {est1} ≠ 实战"


def test_unknown_skill_index_skips_position_conditional():
    """调用方不知道槽位（skill_index=-1，Agent 侧的常见情形）时按「条件不成立」保守处理：
    读数与「位置不符」一致（不加 +60），不会把 45 威力估成 105。"""
    b = _battle(["猛烈撞击", "械斗"])
    me, opp = b.player_a.active, b.player_b.active
    dmg, _ = b._resolver.calc_damage(
        me, opp, SkillUse(battle_skill=me.skills[1]), b.globals, attacker_team='A')
    assert dmg == _estimate(_battle(["械斗", "猛烈撞击"]), 0)
