"""蓄力（charge）语义：本回合 0 伤 + 下一回合释放 + 专家估伤同口径。

钉住三条：
1. 未蓄力时用蓄力技能 → 该回合只「开始蓄力」，0 伤、不付能耗；
2. 蓄力中**能释放**（此前 `_execute_skill_vm` 的「蓄力中禁止聚能」守卫漏了
   `action.kind == 'gather'` 限定，把释放也挡了 → 精灵被永久锁死在蓄力中）；
3. 估伤与实战同口径：蓄力回合 0、释放回合满值（`resolver._would_charge`）。
"""
from pathlib import Path

import pytest

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.battleskill import SkillUse
from backend.sim.factory import SimFactory
from backend.vm.effect import StateEffect

_PROJ = Path(__file__).resolve().parent.parent.parent

_CHARGE_SKILL = "升龙咆哮"


def _battle(skill=_CHARGE_SKILL):
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "水灵", "skills": [skill, "猛烈撞击"]}])
    p2 = factory.build_player("B", [{"name": "水灵", "skills": ["猛烈撞击"]}])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    b.player_a.active.energy = 10
    b.player_b.active.energy = 10
    return b


def _estimate(b, idx=0):
    me, opp = b.player_a.active, b.player_b.active
    return b._resolver.calc_damage(
        me, opp, SkillUse(battle_skill=me.skills[idx], skill_index=idx),
        b.globals, attacker_team='A')[0]


def _live(b, idx=0):
    opp = b.player_b.active
    hp0 = opp.current_hp
    ev = b._execute_skill_vm('A', Action(kind='skill', skill_index=idx))
    return hp0 - opp.current_hp, ev


def test_charge_turn_deals_zero_and_estimates_zero():
    """未蓄力：实战 0 伤（开始蓄力），估伤也必须是 0（此前估 175，让斩杀规则误判）。"""
    b = _battle()
    me = b.player_a.active
    est = _estimate(b)
    dmg, ev = _live(b)
    assert est == 0, f"蓄力回合估伤应为 0，实得 {est}"
    assert dmg == 0 and any("开始蓄力" in e for e in ev)
    assert getattr(me, '_charging', False) is True
    assert me.energy == 10, "蓄力回合不付能耗"


def test_charge_releases_and_pays_cost_next_turn():
    """蓄力中：释放并造成满伤，能耗在**释放**回合支付（此前被守卫挡住、永久卡死）。"""
    b = _battle()
    me, opp = b.player_a.active, b.player_b.active
    _live(b)                                   # 回合 1：开始蓄力
    assert me.energy == 10

    est = _estimate(b)
    hp0 = opp.current_hp
    dmg, ev = _live(b)                         # 回合 2：释放
    assert dmg > 0, f"释放回合应打出伤害，事件={ev}"
    assert me.energy == 7, "升龙咆哮 3 费应在释放回合支付"
    assert getattr(me, '_charging', False) is False
    assert est == dmg, f"释放回合估伤 {est} != 实战 {dmg}"


def test_gather_still_blocked_while_charging():
    """蓄力中禁止聚能（守卫的作用范围只剩聚能）。"""
    b = _battle()
    me = b.player_a.active
    _live(b)
    ev = b._execute_skill_vm('A', Action(kind='gather'))
    assert any("蓄力中无法聚能" in e for e in ev), ev
    assert getattr(me, '_charging', False) is True, "被挡的聚能不应清掉蓄力状态"


@pytest.mark.parametrize("skill", ["升龙咆哮", "吹炎", "怨力打击", "龙之利爪", "龙吟"])
def test_all_charge_skills_estimate_zero_when_not_charging(skill):
    """全库 5 个蓄力技能：未蓄力时估伤一律 0（`Battle._skill_has_charge` 判据）。"""
    b = _battle(skill)
    assert _estimate(b) == 0
