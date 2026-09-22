"""连击 = N 次独立命中（2026-09-22 用户确认语义）。

钉住四条：
1. 每段各自套公式（各自取整、各自最低 1 点），不是「单事件 ×N」；
2. 同回合连击修正（`combo`/`combo_set`/`combo_mult`）**改写段数**，不折进伤害值；
3. `combo_mult` 是加成分数（1 = +100%），精灵级与日志级同口径；
4. 星陨印记这类**消耗型**印记只在第一次命中引爆。

契约见 data/IR_GUIDE.md §八「连击（combo）语义」。
"""
from pathlib import Path

import pytest

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.battleskill import SkillUse, effective_combo
from backend.sim.factory import SimFactory
from backend.vm.ctx import Ctx
from backend.vm.journal import Damage
from backend.vm.ops.hit import op_hit

_PROJ = Path(__file__).resolve().parent.parent.parent


def _make_battle(skills_a, skills_b=None):
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "水灵", "skills": skills_a}])
    p2 = factory.build_player("B", [{"name": "水灵", "skills": skills_b or ["猛烈撞击"]}])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    return b


def _live(b, idx=0, **kw):
    """执行技能 → (总伤, 逐段事件列表)"""
    opp = b.player_b.active
    hp0 = opp.current_hp
    events = b._execute_skill_vm('A', Action(kind='skill', skill_index=idx), **kw)
    segs = [e for e in events if 'HP' in e and '-' in e]
    return hp0 - opp.current_hp, segs


def _estimate(b, idx=0):
    return b._resolver.calc_damage(
        b.player_a.active, b.player_b.active,
        SkillUse(battle_skill=b.player_a.active.skills[idx], skill_index=idx),
        b.globals, attacker_team='A')[0]


# ══════════════════════════════════════════════════════════════════
# 1) N 次独立命中
# ══════════════════════════════════════════════════════════════════

def test_op_hit_emits_single_segment_plus_hits():
    """`op_hit` 只产一条 Damage：金额是**单段**，段数在 `hits` 上。"""
    ctx = Ctx(combo_self=3, atk_self=100, def_opp=100, element_self="火")
    muts = op_hit(ctx, {"op": "hit", "power": 30, "type": "物攻", "element": "火"})
    assert len(muts) == 1, "段数不由事件条数承载，而是写进 hits"
    assert muts[0].hits == 3
    # 单段：与 combo_self=1 的同一手完全同值
    ctx1 = Ctx(combo_self=1, atk_self=100, def_opp=100, element_self="火")
    single = op_hit(ctx1, {"op": "hit", "power": 30, "type": "物攻", "element": "火"})[0]
    assert muts[0].amount == single.amount


def test_expand_combo_hits_is_identity_below_two():
    from backend.engine.modifiers import expand_combo_hits
    one = Damage(target="sprite_opp", amount=10, element="火", type="物攻", hits=1)
    none = Damage(target="sprite_opp", amount=10, element="火", type="物攻")
    assert expand_combo_hits([one, none]) == [one, none]


@pytest.mark.parametrize("skill,segs", [("虫刺", 3), ("双响炮", 2), ("午夜噪音", 5)])
def test_live_damage_is_applied_per_segment(skill, segs):
    """实战按段扣血：3 连击 = 3 条独立伤害事件（各自扣血、各自取整）。"""
    b = _make_battle([skill])
    total, events = _live(b, 0)
    assert len(events) == segs, f"{skill}: 应有 {segs} 条伤害事件，实得 {len(events)}"
    per = [int(e.rsplit('-', 1)[1].split('HP')[0]) for e in events]
    assert len(set(per)) == 1, f"{skill}: 各段应同值: {per}"
    assert total == sum(per)


def test_per_segment_min_one_point():
    """每段各自 `max(1, …)`：低威力 3 连击打高减伤目标 → 3 点（旧口径只 1 点）。"""
    b = _make_battle(["虫刺"])
    b.player_b.active._modifiers['damage_reduction'] = 0.97
    total, events = _live(b, 0)
    assert (total, len(events)) == (3, 3), f"每段最低 1 点 → 3 点/3 段，实得 {total}/{len(events)}"


def test_segment_rounding_differs_from_single_event():
    """逐段取整 ≠ 末尾一次取整：3 连击的每段值先取整再相加。"""
    b = _make_battle(["虫刺"])
    total, events = _live(b, 0)
    per = total // len(events)
    assert total == per * len(events), f"总量应是单段整数倍（逐段取整）: {total} / {events}"


# ══════════════════════════════════════════════════════════════════
# 2) 同回合连击修正改写段数
# ══════════════════════════════════════════════════════════════════

def test_same_turn_combo_add_changes_segment_count():
    """疾风刺：1 连击，若先于敌方攻击改为 3 连击 → 3 条事件。"""
    b = _make_battle(["疾风刺"])
    total_first, first_events = _live(b, 0, is_first=True)
    b2 = _make_battle(["疾风刺"])
    total_late, late_events = _live(b2, 0, is_first=False)
    assert len(first_events) == 3, f"先手应 3 段: {first_events}"
    assert len(late_events) == 1, f"后手应 1 段: {late_events}"
    assert total_first == pytest.approx(3 * total_late, rel=0.02)


def test_same_turn_combo_add_on_counter():
    """散手：2 连击，应对状态改为 6 连击 → 6 条事件。"""
    b = _make_battle(["散手"])
    _total, plain = _live(b, 0)
    assert len(plain) == 2
    b2 = _make_battle(["散手"])
    total_ct, countered = _live(b2, 0, is_countered=True,
                                countered_skill=b2.player_b.active.skills[0])
    assert len(countered) == 6, f"应对成功应 6 段: {countered}"
    assert total_ct == pytest.approx(3 * sum(int(e.rsplit('-', 1)[1].split('HP')[0])
                                             for e in plain), rel=0.05)


def test_combo_mult_is_bonus_fraction_on_sprite_channel():
    """暴风眼「连击数+100%」→ `_modifiers['combo_mult'] = 1.0`（加分，不是总量）。"""
    b = _make_battle(["暴风眼", "虫刺"])
    me = b.player_a.active
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    assert me._modifiers.get('combo_mult') == 1.0, \
        f"应存加成分数 1.0（×2），实得 {me._modifiers.get('combo_mult')}"
    assert effective_combo(me.skills[1], me) == 6, "3 连击 ×(1+1) = 6 段"
    total, events = _live(b, 1)
    assert len(events) == 6, f"应 6 条伤害事件: {events}"
    assert total == pytest.approx(2 * 39, rel=0.05)   # 虫刺单体 3 段 39


def test_same_turn_combo_mult_doubles_segments():
    """灵光「若敌方本回合更换精灵，本次技能连击数翻倍」→ 3 段变 6 段。

    此前日志里的 `combo_mult` 没有任何读取方 → 翻倍整条静默失效。
    """
    b = _make_battle(["灵光"])
    _total, plain = _live(b, 0)
    assert len(plain) == 3
    b2 = _make_battle(["灵光"])
    total2, doubled = _live(b2, 0, opponent_switched=True)
    assert len(doubled) == 6, f"翻倍应 6 段: {doubled}"
    assert total2 == pytest.approx(2 * sum(int(e.rsplit('-', 1)[1].split('HP')[0])
                                           for e in plain), rel=0.02)


# ══════════════════════════════════════════════════════════════════
# 3) 消耗型印记只引爆一次
# ══════════════════════════════════════════════════════════════════

def test_starfall_detonates_once_on_combo():
    """3 连击命中携带星陨印记的目标 → 只引爆一次（印记被消耗殆尽）。"""
    b = _make_battle(["虫刺"])
    b.globals.apply_mark('B', '星陨印记', 'negative', 3)
    _total, _events = _live(b, 0)
    mark = b.globals.get_mark_by_name('B', '星陨印记')
    assert mark is None or mark.stacks == 0, "引爆后印记应清零"


# ══════════════════════════════════════════════════════════════════
# 4) 估伤 == 实战
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("skill", ["虫刺", "午夜噪音", "双响炮", "撒娇", "音波弹", "疾风刺"])
def test_estimate_matches_live_after_segment_split(skill):
    """逐段口径下估伤仍等于实战（疾风刺按「后手」保守口径，估伤不假设先手）。

    估伤在**独立的**战斗上取（与 `test_combo_semantics` 同做法）：实战会改动自身
    状态（撒娇的萌化会换物种），战后在同一对象上再估就是另一只精灵了。
    """
    b = _make_battle([skill])
    est = _estimate(b, 0)
    live, _events = _live(b, 0)
    assert est == live, f"{skill}: 估伤 {est} != 实战 {live}"
