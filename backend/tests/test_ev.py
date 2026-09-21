"""backend/tests/test_ev.py — 收益矩阵/期望值（`backend/sim/ev.py`）。

核心是**对拍引擎**：`payoff_terms` 预估的 dealt/taken 必须等于引擎真打一手的结果
（用 `battle.execute_turn(..., fixed_action_a=…, fixed_action_b=…)` 固定双方动作）。
覆盖应对三角（防御技反制攻击 / 攻击技反制状态 / 状态技反制防御）、换人先于技能、
"我出手前就倒"的幻影伤害剔除、以及矩阵/期望值/混合采样的语义。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim import ev
from backend.sim.action import Action
from backend.sim.agent import _GATHER_ACTION
from backend.sim.factory import SimFactory
from backend.sim.resolver import SkillResolver

factory = SimFactory()

# 技能事实（data/skills/*.json，均已核对）：
#   风墙  防御·counter=攻击·减伤 50%；  快速移动 状态·counter=防御（无伤害/无 DoT）；
#   偷袭  物攻·counter=状态·应对成功威力×3；  龙卷风 物攻·counter=状态·先手+1；
#   猛烈撞击 物攻·counter=无；  剧毒 状态·counter=防御（带中毒 DoT，只用于反向用例）。
MY_SKILLS = ["偷袭", "猛烈撞击", "风墙", "快速移动"]
OPP_SKILLS = ["猛烈撞击", "风墙", "快速移动"]
OPP_BENCH = [{"name": "草衣虫", "skills": ["猛烈撞击"]}]
MY_BENCH = [{"name": "草衣虫", "skills": ["猛烈撞击"]}]
SCENARIO_OF = {"猛烈撞击": "attack", "风墙": "defense", "快速移动": "status", None: "gather"}


class _Gatherer:
    """只会聚能的假 agent（固定动作由 fixed_action_* 提供，这个只是占位）。"""

    def choose_action(self, battle):
        return _GATHER_ACTION

    def choose_lead(self, battle):
        return 0

    def choose_replacement(self, battle):
        return 0

    def on_game_end(self, winner):
        pass


def _battle(my_skills=None, opp_skills=None, opp_bench=None, my_bench=None):
    p1_specs = [{"name": "水灵", "skills": list(my_skills or MY_SKILLS)}]
    p1_specs += [dict(s) for s in (my_bench if my_bench is not None else [])]
    p2_specs = [{"name": "雪怪", "skills": list(opp_skills or OPP_SKILLS)}]
    p2_specs += [dict(s) for s in (opp_bench if opp_bench is not None else OPP_BENCH)]
    return factory.build_battle(factory.build_player("A", p1_specs),
                               factory.build_player("B", p2_specs))


def _ctx(battle, my_team="A"):
    me = battle.player_a.active if my_team == "A" else battle.player_b.active
    opp_player = battle.player_b if my_team == "A" else battle.player_a
    return me, opp_player, ev.scenario_context(battle, me, my_team, opp_player)


def _skill_index(battle, name, team="A"):
    sprite = battle.player_a.active if team == "A" else battle.player_b.active
    for i, s in enumerate(sprite.skills):
        if s.name == name:
            return i
    raise AssertionError(f"{name} 不在技能表里")


def _run_one_turn(battle, my_action: ev.Candidate, their_skill_name: str | None):
    """固定双方动作真打一手，返回 (我打出的伤害, 我吃的伤害)。"""
    opp = battle.player_b.active
    their_action = (_GATHER_ACTION if their_skill_name is None else
                    Action('skill', skill_index=_skill_index(battle, their_skill_name, "B")))
    if my_action.kind == 'skill':
        mine = Action('skill', skill_index=my_action.index)
    elif my_action.kind == 'switch':
        mine = Action('switch', switch_index=my_action.index)
    else:
        mine = _GATHER_ACTION
    my_hp0, opp_hp0 = battle.player_a.active.current_hp, opp.current_hp
    battle.execute_turn_headless(_Gatherer(), _Gatherer(),
                                 fixed_action_a=mine, fixed_action_b=their_action)
    dealt = opp_hp0 - opp.current_hp
    taken = my_hp0 - battle.player_a.active.current_hp
    return dealt, taken


# ══ 技能 JSON 读取 ══


def test_skill_json_readers():
    b = _battle()
    assert ev.defense_reduction(b.player_a.active.skills[_skill_index(b, "风墙")]) == pytest.approx(0.5)
    assert ev.counter_power_mult(b.player_a.active.skills[_skill_index(b, "偷袭")]) == pytest.approx(3.0)
    assert ev.counter_power_mult(b.player_a.active.skills[_skill_index(b, "猛烈撞击")]) == pytest.approx(1.0)


def test_counter_triangle_direction():
    """三角方向：防御技反制攻击、攻击技反制状态、状态技反制防御（wiki 02）。"""
    b = _battle()
    sneak = b.player_a.active.skills[_skill_index(b, "偷袭")]        # 物攻, counter=状态
    wall = b.player_a.active.skills[_skill_index(b, "风墙")]          # 防御, counter=攻击
    quick = b.player_a.active.skills[_skill_index(b, "快速移动")]     # 状态, counter=防御
    strike = b.player_b.active.skills[_skill_index(b, "猛烈撞击", "B")]  # 物攻, counter=无
    assert wall.counter == "攻击" and sneak.counter == "状态" and quick.counter == "防御"
    # resolve_counter(atk, def) 问的是"def 这只技能有没有应对 atk"
    assert SkillResolver.resolve_counter(strike, wall) is True    # 风墙（counter=攻击）应对攻击
    assert SkillResolver.resolve_counter(strike, quick) is False  # 快速移动（counter=防御）不应对攻击
    assert SkillResolver.resolve_counter(wall, quick) is True     # 快速移动应对防御技
    assert SkillResolver.resolve_counter(quick, sneak) is True    # 偷袭（counter=状态）应对状态技
    assert SkillResolver.resolve_counter(sneak, quick) is False   # 快速移动不应对攻击技


@pytest.mark.parametrize("my_skill,their_skill", [
    ("猛烈撞击", "猛烈撞击"),      # 纯攻击对攻
    ("猛烈撞击", "风墙"),          # 被防御技应对 → 我的伤害被减伤
    ("偷袭", "快速移动"),          # 我的攻击应对它的状态技 → 威力×3 + 反击一次基础伤害
    ("风墙", "猛烈撞击"),          # 我用防御技应对它的攻击 → 减伤
    ("猛烈撞击", "快速移动"),      # 它用状态技：我不被减伤、它不造成伤害
    ("猛烈撞击", None),            # 它聚能
])
def test_payoff_terms_match_engine(my_skill, their_skill):
    """预估的 dealt/taken 必须等于引擎真打一手的结果（容差只留给伤害取整）。"""
    est = _battle()
    me, opp_player, ctx = _ctx(est)
    cand = ev.Candidate('skill', _skill_index(est, my_skill), my_skill)
    terms = ev.payoff_terms(est, me, est.player_a, opp_player, cand,
                            SCENARIO_OF[their_skill], ctx, ev.EVParams(), "A")

    run = _battle()
    dealt, taken = _run_one_turn(run, cand, their_skill)
    assert terms["dealt"] == pytest.approx(dealt, abs=3), \
        f"{my_skill} vs {their_skill}: dealt 预估 {terms['dealt']} 实际 {dealt}"
    assert terms["taken"] == pytest.approx(taken, abs=3), \
        f"{my_skill} vs {their_skill}: taken 预估 {terms['taken']} 实际 {taken}"


def test_being_countered_costs_multiplier_once():
    """被应对的代价：它用「应对状态」的攻击技抓我的状态技 → 它先手，伤害×倍率，**只打一次**。

    原文（游戏描述 1015 应对状态）只写「应对成功 → 本次行动必定先手 + 触发应对效果」，
    没有「被应对方再多吃一次基础伤害」。旧引擎在结算被应对方时重放了应对方技能的无条件
    效果（含编译期注入的隐式 HitOp），实测会打出两份伤害；该注入已移除。
    """
    b = _battle(my_skills=["快速移动", "猛烈撞击"],
                opp_skills=["龙卷风", "猛烈撞击"])          # 龙卷风：物攻·counter=状态·威力×1.5
    me, opp_player, ctx = _ctx(b)
    cand = ev.Candidate('skill', _skill_index(b, "快速移动"), "快速移动")
    terms = ev.payoff_terms(b, me, b.player_a, opp_player, cand, "attack", ctx,
                            ev.EVParams(), "A")
    assert terms["they_counter"] == 1.0
    run = _battle(my_skills=["快速移动", "猛烈撞击"], opp_skills=["龙卷风", "猛烈撞击"])
    _dealt, taken = _run_one_turn(run, cand, "龙卷风")
    assert terms["taken"] == pytest.approx(taken, abs=3)
    from backend.sim import tactics

    base = ev._damage(b, opp_player.active, me, ctx["attack"][1], tactics.opponent_team("A"), True)
    assert terms["taken"] == pytest.approx(1.5 * base, rel=0.05), \
        "被应对时应是「倍率一击」，不应再追加一次基础伤害"


def test_counter_bonus_and_penalty_show_up_in_terms():
    """应对三角的收益/代价要在数值上体现：×3 倍率、被减伤、被应对的惩罚。"""
    b = _battle()
    me, opp_player, ctx = _ctx(b)
    sneak = ev.Candidate('skill', _skill_index(b, "偷袭"), "偷袭")
    strike = ev.Candidate('skill', _skill_index(b, "猛烈撞击"), "猛烈撞击")

    p = ev.EVParams()
    sneak_vs_status = ev.payoff_terms(b, me, b.player_a, opp_player, sneak, "status", ctx, p, "A")
    sneak_vs_attack = ev.payoff_terms(b, me, b.player_a, opp_player, sneak, "attack", ctx, p, "A")
    assert sneak_vs_status["i_counter"] == 1.0
    assert sneak_vs_status["dealt"] > 2.5 * sneak_vs_attack["dealt"]   # 威力 ×3

    strike_vs_defense = ev.payoff_terms(b, me, b.player_a, opp_player, strike, "defense", ctx, p, "A")
    strike_vs_gather = ev.payoff_terms(b, me, b.player_a, opp_player, strike, "gather", ctx, p, "A")
    assert strike_vs_defense["they_counter"] == 1.0
    assert strike_vs_defense["dealt"] <= 0.6 * strike_vs_gather["dealt"]  # 被减伤 50%


def test_switch_payoff_uses_incoming_sprite():
    """换人先于技能（battle.py:1056）→ 吃伤害的是换上来的那只。"""
    b = _battle(my_bench=MY_BENCH)
    me, opp_player, ctx = _ctx(b)
    incoming = b.player_a.team[1]
    cand = ev.Candidate('switch', 1, f"→{incoming.name}")
    terms = ev.payoff_terms(b, me, b.player_a, opp_player, cand, "attack", ctx,
                            ev.EVParams(), "A")
    assert terms["dealt"] == 0                       # 换人这一手我不打伤害
    from backend.sim import tactics

    expected = ev._damage(b, opp_player.active, incoming, ctx["attack"][1],
                          tactics.opponent_team("A"), True)
    assert terms["taken"] == pytest.approx(expected, rel=0.03)
    # 留在场上挨打 vs 换人挨打：谁吃伤害不同，数值应各自对得上
    stay = ev.payoff_terms(b, me, b.player_a, opp_player,
                           ev.Candidate('skill', _skill_index(b, "猛烈撞击"), "猛烈撞击"),
                           "attack", ctx, ev.EVParams(), "A")
    assert stay["taken"] != terms["taken"] or incoming.max_hp == me.max_hp


def test_phantom_kill_removed_when_opponent_acts_first_and_lethal():
    """对手先手且这一击能杀我 → 我的伤害算 0（我根本没机会出手）。"""
    b = _battle(my_skills=["猛烈撞击", "风墙"],
                opp_skills=["猛烈撞击", "龙卷风"])       # 龙卷风 先手+1 → 它先出手
    me, opp_player, ctx = _ctx(b)
    me.current_hp = 1                                  # 它先手必杀我
    cand = ev.Candidate('skill', _skill_index(b, "猛烈撞击"), "猛烈撞击")
    terms = ev.payoff_terms(b, me, b.player_a, opp_player, cand, "attack", ctx,
                            ev.EVParams(), "A")
    from backend.sim import tactics

    assert tactics.best_attack_priority(opp_player.active) == 1
    assert tactics.moves_first(b, me, "A", opp_player.active, "B") is False
    assert terms["dealt"] == 0
    assert terms["taken"] >= me.current_hp              # 直接判我力竭


# ══ 矩阵 / 期望值 / 混合采样 ══


def _candidates(b):
    cands = [ev.Candidate('skill', i, s.name) for i, s in enumerate(b.player_a.active.skills)]
    cands += [ev.Candidate('switch', i, f"→{s.name}")
              for i in range(1, len(b.player_a.team))]
    cands.append(ev.Candidate('gather', 0, "聚能"))
    return cands


def test_matrix_and_expected_values_shape():
    b = _battle()
    me, opp_player, _ = _ctx(b)
    cands = _candidates(b)
    dist = {"attack": 0.5, "status": 0.3, "switch": 0.2}
    matrix = ev.value_matrix(b, me, b.player_a, opp_player, cands, dist, ev.EVParams(), "A")
    assert set(matrix) == set(cands)
    for row in matrix.values():
        assert set(row) == {"attack", "status", "switch"}
    evs = ev.expected_values(matrix, dist, ev.EVParams())
    assert set(evs) == set(cands)
    # 期望值必须等于按分布加权求和（无风险项时）
    for cand, row in matrix.items():
        assert evs[cand] == pytest.approx(sum(row[sc] * dist[sc] for sc in row))


def test_risk_lambda_penalizes_spread():
    b = _battle()
    me, opp_player, _ = _ctx(b)
    cands = _candidates(b)
    dist = {"attack": 0.5, "defense": 0.5}
    matrix = ev.value_matrix(b, me, b.player_a, opp_player, cands, dist, ev.EVParams(), "A")
    neutral = ev.expected_values(matrix, dist, ev.EVParams())
    cautious = ev.expected_values(matrix, dist, ev.EVParams(risk_lambda=0.5))
    for cand, row in matrix.items():
        spread = max(row.values()) - min(row.values())
        assert cautious[cand] == pytest.approx(neutral[cand] - 0.5 * spread)
    # 风险厌恶会改变选择：波动最小的行相对占优
    wild = max(neutral, key=lambda c: neutral[c])
    safe = max(cautious, key=lambda c: cautious[c])
    assert (max(matrix[safe].values()) - min(matrix[safe].values())
            <= max(matrix[wild].values()) - min(matrix[wild].values()))


def test_softmax_pick_semantics():
    c1, c2 = ev.Candidate('skill', 0, "A"), ev.Candidate('skill', 1, "B")
    evs = {c1: 0.5, c2: 0.1}
    assert ev.softmax_pick(evs, 0.0) is c1                 # T=0 → argmax
    rng = random.Random(0)
    picks = [ev.softmax_pick(evs, 0.5, rng) for _ in range(200)]
    assert picks.count(c1) > picks.count(c2)               # T>0 → 混合，但偏好高 EV
    assert set(picks) == {c1, c2}                          # 混合意味着两边都会出
    tie = {c1: 0.3, c2: 0.3}
    rng = random.Random(1)
    tied = {ev.softmax_pick(tie, 0.0, rng) for _ in range(50)}
    assert tied == {c1, c2}                                # 完全平局 → 随机（不可被预测）


def test_choose_returns_evs_and_belief():
    b = _battle()
    me, opp_player, _ = _ctx(b)
    cands = _candidates(b)
    picked, evs, dist = ev.choose(b, me, b.player_a, opp_player, cands, "A",
                                  temperature=0.0, my_best_dmg=40)
    assert picked in cands
    assert set(evs) == set(cands)
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    again, evs2, _ = ev.choose(b, me, b.player_a, opp_player, cands, "A",
                               temperature=0.0, my_best_dmg=40)
    assert again is picked and evs2 == evs                # 确定性：同局面同结果
