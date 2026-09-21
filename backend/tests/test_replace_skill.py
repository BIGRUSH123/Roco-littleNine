"""透射转换（3026 的引用）——透镜实验「应对状态时被应对的技能变为透射」。

`replace_skill` 把对手本回合的技能替换为指定技能，**本回合有效**、回合末还原；
与巧变（`_morph_temp`）共用 `replaced_by` 但互不破坏（见 data/IR_GUIDE.md §3C）。
"""

from __future__ import annotations

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry

factory = SimFactory()


def _battle(skills_a=("透镜实验", "甩水"), skills_b=("重金属粉尘", "猛烈撞击"),
            name_a="草衣虫", name_b="花衣蝶"):
    p1 = factory.build_player("A", [{"name": name_a, "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": name_b, "skills": list(skills_b)}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    # 对手本回合要用的技能（引擎在 _resolve_both_skills 里登记，单测手工置入）
    battle._turn_skills.setdefault("B", {})["name"] = battle.player_b.active.skills[0].name
    return battle


# ═══════════════════════════════════════════════════════════════════
# replace_skill op
# ═══════════════════════════════════════════════════════════════════

def test_counter_branch_replaces_opponent_skill():
    battle = _battle()
    opp = battle.player_b.active
    slot = opp.skills[0]
    assert slot.name == "重金属粉尘"

    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=1),
                             is_first=True, countered_skill=slot)

    assert slot.replaced_by is not None
    assert slot.name == "透射"
    assert slot.element == "光"
    assert slot.energy_cost == 1
    assert slot.power == 60
    assert battle._replaced_restore == {("B", 0): True}


def test_replacement_is_turn_scoped_only():
    """只借 replaced_by，不写 _morph_temp（巧变状态由 morph 独占）。"""
    battle = _battle()
    slot = battle.player_b.active.skills[0]

    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=1),
                             is_first=True, countered_skill=slot)

    assert slot._morph_temp is False


def test_turn_end_restores_original_skill():
    battle = _battle()
    slot = battle.player_b.active.skills[0]
    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=1),
                             is_first=True, countered_skill=slot)
    assert slot.name == "透射"

    battle._phase_turn_end()

    assert slot.replaced_by is None
    assert slot.name == "重金属粉尘"
    assert battle._replaced_restore == {}


def test_branch0_boosts_power_when_opponent_carries_light_skill():
    battle = _battle(skills_b=("透射", "猛烈撞击"))
    user = battle.player_a.active

    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=0), is_first=True)

    assert user.skills[0]._modifiers.get("power") == 50


def test_branch0_no_boost_without_light_skill():
    battle = _battle(skills_b=("猛烈撞击", "甩水"))
    user = battle.player_a.active

    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=0), is_first=True)

    assert user.skills[0]._modifiers.get("power") in (None, 0)


def test_counter_branch_falls_back_when_not_countered():
    """应对未成功 → 暗 分支条件不成立 → 回退到 0 号无条件分支（不加透射）。"""
    battle = _battle()
    opp = battle.player_b.active
    slot = opp.skills[0]

    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=1),
                             is_first=True, countered_skill=None)

    assert slot.replaced_by is None


def test_replace_skill_does_not_break_morph_state():
    """槽位原本是巧变产物（_morph_temp=True）时：替换照常生效，巧变标记不被清。"""
    from backend.sim.skill import Skill

    battle = _battle()
    slot = battle.player_b.active.skills[0]
    slot.replaced_by = Skill(name="假技能", element="普通", skill_type="状态",
                             power=0, energy_cost=1)
    slot._morph_temp = True
    # 引擎在 resolve 时按**生效技能名**登记对手本回合要用的技能
    battle._turn_skills["B"]["name"] = slot.name

    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=1),
                             is_first=True, countered_skill=slot)

    assert slot.name == "透射"
    assert slot._morph_temp is True

    # 回合末只清 replaced_by，巧变标记仍在（由 morph 自己的结算负责还原）
    battle._phase_turn_end()
    assert slot.replaced_by is None
    assert slot._morph_temp is True


def test_replaced_restore_is_in_mcts_state_snapshot():
    """MCTS 仿真回滚需要覆盖新的还原登记表。"""
    battle = _battle()
    slot = battle.player_b.active.skills[0]
    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=1),
                             is_first=True, countered_skill=slot)
    saved = battle.save_mutable_state()

    battle._phase_turn_end()
    assert battle._replaced_restore == {}

    battle.restore_mutable_state(saved)
    assert battle._replaced_restore == {("B", 0): True}
