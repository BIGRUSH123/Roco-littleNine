"""backend/tests/test_agent_v2_tactics.py — RuleAgentV2 的战术预判（`backend/sim/tactics.py`）。

覆盖四条"高级思考"能力，每条都对着引擎自己的行为验证（不是对着实现复述）：

  1. **出手顺序含先手值 + 印记减速**（引擎 `battle.py:1136`：先比 先手值+priority_mod，
     再比"速度 − 印记减速"）；
  2. **回合末致死**：异常 tick + 印记末伤的预测量必须**等于**引擎实际扣的血
     （`resolver.turn_end` / `globals.mark_turn_end_effects`）；
  3. **星陨印记追加伤害**：预测值必须等于 `globals.trigger_starfall` 的实际伤害，
     并能被 agent 用成"我这一击 + 印记 = 斩杀"；
  4. **换人安全与对手换人倾向**：换上场会被印记进场伤害打死的替补不选；
     "我这边能斩杀 + 对面有替补"或"对面被 tick 死"→ 判断对面会撤人。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim import tactics
from backend.sim.agent import _GATHER_ACTION
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy
from backend.sim.battleskill import SkillUse
from backend.sim.factory import SimFactory

factory = SimFactory()


class _Gatherer:
    """只会聚能的假 agent：让引擎跑到回合末而不引入攻击伤害。"""

    def choose_action(self, battle):
        return _GATHER_ACTION

    def choose_lead(self, battle):
        return 0

    def choose_replacement(self, battle):
        return 0

    def on_game_end(self, winner):
        pass


def _battle(a_specs, b_specs, a_item=None):
    p1 = factory.build_player("A", a_specs, item=a_item)
    p2 = factory.build_player("B", b_specs)
    return factory.build_battle(p1, p2)


def _damage(battle, attacker, defender, skill, team="A") -> int:
    dmg, _ = battle._resolver.calc_damage(
        attacker, defender, SkillUse(battle_skill=skill), battle.globals, attacker_team=team)
    return dmg


# ══ ① 出手顺序：先手值 → 印记减速后的速度 ══


def test_moves_first_from_priority():
    """先手值优先于速度：慢的那方带 +1 先手攻击时反而先出手。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击", "龙卷风"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    slow, fast = b.player_a.active, b.player_b.active
    assert slow.effective_stat("speed") > fast.effective_stat("speed")  # 我更快是真的
    assert tactics.best_attack_priority(slow) == 1                      # 龙卷风 先手+1
    assert tactics.best_attack_priority(fast) == 0
    assert tactics.moves_first(b, fast, "B", slow, "A") is False        # 更快但先手值低 → 后手
    b2 = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                 [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    assert tactics.moves_first(b2, b2.player_a.active, "A",
                               b2.player_b.active, "B") is True         # 先手值相同 → 比速度


def test_moves_first_counts_mark_speed_penalty():
    """印记减速要计入：减速 2 层（-20）会把速度领先 11 的一方压到后手。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    me, opp = b.player_a.active, b.player_b.active
    assert tactics.moves_first(b, me, "A", opp, "B") is True
    category = b.globals.classify_mark("减速")
    b.globals.apply_mark("A", "减速", category, 2)
    assert tactics.effective_speed(b, me, "A") < tactics.effective_speed(b, opp, "B")
    assert tactics.moves_first(b, me, "A", opp, "B") is False


# ══ ② 回合末致死：预测量必须等于引擎实际扣血 ══


def test_turn_end_prediction_matches_engine():
    """中毒印记 + 异常 tick 的预测值 == 引擎结算后真实掉的血。"""
    from backend.vm.effect import AbnormalEffect

    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    b.globals.apply_mark("A", "中毒印记", b.globals.classify_mark("中毒印记"), 1)
    b.globals.apply_mark("B", "中毒印记", b.globals.classify_mark("中毒印记"), 2)
    b.player_a.active.active_effects.append(
        AbnormalEffect(name="中毒", source="测试", stacks=3,
                       tick_damage_pct=0.02, tick_per_stack=True))

    before = {team: (s, s.current_hp) for team, s in
              (("A", b.player_a.active), ("B", b.player_b.active))}
    predicted = {team: tactics.predict_turn_end_damage(b, s, team)
                 for team, (s, _hp) in before.items()}
    assert predicted["A"] > 0 and predicted["B"] > 0

    b.execute_turn(_Gatherer(), _Gatherer())          # 双方只聚能 → 本回合只有 tick 伤害

    for team, (sprite, hp) in before.items():
        assert hp - sprite.current_hp == predicted[team], (
            f"{team} 侧预测 {predicted[team]} 实际掉血 {hp - sprite.current_hp}")


# ══ ③ 星陨印记：预测 == 引擎，且能当斩杀用 ══


@pytest.mark.parametrize("stacks", [1, 2, 4])
def test_starfall_bonus_matches_engine(stacks: int):
    """星陨追加伤害的预测值必须等于引擎 trigger_starfall 的真实伤害。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    b.globals.apply_mark("B", "星陨印记", b.globals.classify_mark("星陨印记"), stacks)
    me, opp = b.player_a.active, b.player_b.active
    skill = me.skills[0]
    predicted = tactics.starfall_bonus(b, me, opp, skill, "B")
    before = opp.current_hp
    b.globals.trigger_starfall("B", me, opp, skill)     # 会落伤，故每轮新建对局
    assert predicted == before - opp.current_hp > 0


def test_agent_uses_starfall_for_combo_kill():
    """单招打不死、加上印记追加伤害才够斩杀 → agent 按"斩杀"处理（挑的是组合杀那招）。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击", "甩水"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    me, opp = b.player_a.active, b.player_b.active
    pure = [_damage(b, me, opp, sk) for sk in me.skills]
    opp.current_hp = max(pure) + 1                      # 纯伤害总是差 1 点
    assert max(pure) < opp.current_hp

    no_mark = RuleAgentV2("A", b.player_a).choose_action(b)
    assert no_mark.kind == "skill"
    assert pure[no_mark.skill_index] < opp.current_hp   # 没印记时确实不是斩杀

    b.globals.apply_mark("B", "星陨印记", b.globals.classify_mark("星陨印记"), 4)
    bonus = tactics.starfall_bonus(b, me, opp, me.skills[no_mark.skill_index], "B")
    assert bonus >= 1
    opp.current_hp = pure[no_mark.skill_index] + bonus  # 组合杀刚好够

    combo = RuleAgentV2("A", b.player_a).choose_action(b)
    assert combo.kind == "skill"
    assert pure[combo.skill_index] < opp.current_hp, "选中的这招纯伤害不够——说明走的是印记组合杀"
    assert pure[combo.skill_index] + tactics.starfall_bonus(
        b, me, opp, me.skills[combo.skill_index], "B") >= opp.current_hp


# ══ ④ 换人安全 + 对手换人倾向 ══


def test_switch_avoids_mark_lethal_bench():
    """上场就吃印记进场伤害致死的替补不会被选中（换宠与力竭补位都算）。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]},
                 {"name": "草衣虫", "skills": ["猛烈撞击"]},
                 {"name": "花衣蝶", "skills": ["猛烈撞击"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    p = b.player_a
    doomed, safe = p.team[1], p.team[2]
    doomed.current_hp = 5
    # 棘刺 3 层 → 进场吃 3×6% 最大生命：残血的必死，满血的安全
    b.globals.apply_mark("A", "棘刺", b.globals.classify_mark("棘刺"), 3)
    assert tactics.switch_in_damage(b, "A", doomed) >= doomed.current_hp
    assert tactics.switch_in_damage(b, "A", safe) < safe.current_hp
    agent = RuleAgentV2("A", p)
    assert agent._safe_bench(b, p) == [2]                # 只剩安全的那只

    p.active.current_hp = 0                              # 力竭补位
    assert agent.choose_replacement(b) == 2


def test_panic_switch_respects_opponent_priority():
    """旧的 panic 换人门槛（规则 2）要先用先手值判定：对手先手能杀我 → 撤。

    注意：`ev_decide=False` 才走这条旧启发式；开了期望值（默认）时，即使我更快，
    "留在场上会被下回合打死"也会让 EV 层自己选择撤人（见下一条断言）。
    """
    # 钉住 plan_depth=0：本用例测的是**旧启发式**；规划层开着时会按价值自己撤人（见文末断言）
    legacy = TeamStrategy(default=SpriteStrategy(ev_decide=False, plan_depth=0))
    specs_me = [{"name": "水灵", "skills": ["猛烈撞击"]},
                {"name": "花衣蝶", "skills": ["猛烈撞击"]}]
    with_priority = _battle(specs_me, [{"name": "雪怪", "skills": ["猛烈撞击", "龙卷风"]}])
    with_priority.player_a.active.current_hp = 30
    assert tactics.moves_first(with_priority, with_priority.player_a.active, "A",
                               with_priority.player_b.active, "B") is False
    assert RuleAgentV2("A", with_priority.player_a, strategy=legacy)\
        .choose_action(with_priority).kind == "switch"

    without = _battle(specs_me, [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    without.player_a.active.current_hp = 30
    assert tactics.moves_first(without, without.player_a.active, "A",
                               without.player_b.active, "B") is True
    assert RuleAgentV2("A", without.player_a, strategy=legacy)\
        .choose_action(without).kind != "switch"          # 旧规则：我先手，不撤
    # 期望值口径（显式开启）：我 30 血、它一击能打我 47，留在场上等于送命 → 撤
    ev_on = TeamStrategy(default=SpriteStrategy(ev_decide=True))
    assert RuleAgentV2("A", without.player_a, strategy=ev_on).choose_action(without).kind == "switch"


def test_panic_switch_not_suppressed_by_own_lethal():
    """我虽然能一击杀它，但它先手且能杀我 → 撤，不做幻影斩杀。

    "对面大概率要撤"是启发式、不是事实：它先手时完全可以选择先把我打死。
    这条同时钉住两件事：① 不用撤人预测去抑制 panic 换人；② **先手权反了且对手
    这一击能杀我时，规则 1 的"斩杀"不算数**（我根本没机会出手）。
    """
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]},
                 {"name": "花衣蝶", "skills": ["猛烈撞击"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击", "龙卷风"]},
                 {"name": "草衣虫", "skills": ["猛烈撞击"]}])       # 它也有替补 → 启发式说它会撤
    me, opp = b.player_a.active, b.player_b.active
    me.current_hp = 30
    opp.current_hp = 20                                  # 我一击就能杀它
    from backend.sim import tactics as t

    assert t.opponent_likely_switch(b, "A", b.player_b, 9999) is True
    assert t.moves_first(b, me, "A", opp, "B") is False   # 但它先手（龙卷风 先手+1）
    assert RuleAgentV2("A", b.player_a).choose_action(b).kind == "switch"

    # 反过来：我先手 → 斩杀成立，该出招不该撤（别把这条规则做过头）
    b2 = _battle([{"name": "水灵", "skills": ["猛烈撞击"]},
                  {"name": "花衣蝶", "skills": ["猛烈撞击"]}],
                 [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    me2, opp2 = b2.player_a.active, b2.player_b.active
    me2.current_hp = 30
    opp2.current_hp = 20
    assert t.moves_first(b2, me2, "A", opp2, "B") is True
    assert RuleAgentV2("A", b2.player_a).choose_action(b2).kind == "skill"


def test_opponent_likely_switch_heuristic():
    """对面留场必死 + 有活的替补 → 判断会撤人；没替补则不会。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]},
                 {"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    opp_player, opp = b.player_b, b.player_b.active
    assert tactics.opponent_likely_switch(b, "A", opp_player, 0) is False       # 我打不动它
    assert tactics.opponent_likely_switch(b, "A", opp_player, opp.current_hp)   # 我一击必杀
    b.globals.apply_mark("B", "中毒印记", b.globals.classify_mark("中毒印记"), 40)
    assert tactics.opponent_likely_switch(b, "A", opp_player, 0)                # 被 tick 死

    single = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                     [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    assert tactics.opponent_likely_switch(
        single, "A", single.player_b, single.player_b.active.current_hp) is False  # 没替补可换
