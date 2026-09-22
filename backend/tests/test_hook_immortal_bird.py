"""引擎级 hook：不死鸟（`sim/traits/_hooks.py::_immortal_bird_fatal`）。

钉住两条：
1. 「敌方获得 15 层灼烧」的**敌方**是持有者的对面（此前用行动方 `team` 取对面，
   等于把 15 层灼烧加到被打的持有者自己头上）；
2. 连击不再被锁血挡住（2026-09-22 用户定：连击 = N 次独立命中，
   **挡不住连击暂定为正常**）——第 3 段触发锁血后，后续段仍会把它打死。
"""
from pathlib import Path

import pytest

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory

_PROJ = Path(__file__).resolve().parent.parent.parent

#: 带「不死鸟」特性的精灵（data/sprites/377_长尾火鸟.json）
_HOLDER = "长尾火鸟"


def _battle(a_skill="午夜噪音", b_name=_HOLDER):
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "水灵", "skills": [a_skill, "猛烈撞击"]}])
    p2 = factory.build_player("B", [{"name": b_name, "skills": ["猛烈撞击"]}])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    b.player_a.active.energy = 10
    b.player_b.active.energy = 10
    return b


def test_immortal_bird_has_hook_registered():
    from backend.sim.traits.trait_engine import has_hook
    assert has_hook("on_fatal_damage"), "不死鸟的致命拦截 hook 应已注册"


def test_burn_lands_on_the_attacker_not_the_holder():
    """15 层灼烧给**对面**（进攻方），持有者自己不吃。"""
    b = _battle()
    me, opp = b.player_a.active, b.player_b.active
    assert opp.species.ability == "不死鸟"
    opp.current_hp = 30                      # 一击致命
    hp0 = opp.current_hp

    ev = b._execute_skill_vm('A', Action(kind='skill', skill_index=0))

    assert opp.get_counter("不死鸟_used") == 1, "锁血应触发一次"
    assert opp.current_hp == 1 or opp.is_fainted
    assert me.get_stacks("灼烧") == 15, f"进攻方应吃 15 层灼烧: {me.get_stacks('灼烧')}"
    assert opp.get_stacks("灼烧") == 0, f"持有者不该吃自己的灼烧: {opp.get_stacks('灼烧')}"
    assert any("保留1HP" in e for e in ev)


def test_combo_pierces_immortal_bird_by_design():
    """连击**不被**锁血挡住（用户 2026-09-22 定：暂定为正常）。

    5 连击每段 23：第 3 段触发锁血（留 1 HP），第 4 段照常打死。
    """
    b = _battle()
    opp = b.player_b.active
    per = 23
    opp.current_hp = per * 2 + per // 2          # 第 3 段致命
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    assert opp.get_counter("不死鸟_used") == 1
    assert opp.is_fainted, "按当前口径：锁血只吸收一段，后续段仍然结算"
