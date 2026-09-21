"""属性增益/减益（3014 / 3018）——随机 N 层分配 与 正负转换。

- `stat_random`：叠加态「随机16层属性增益」、做好事/吃独食「每使用过1次选择技能
  → 3N 层随机（永久）属性增益/减益」。
- `stat_convert`：掉包「敌方的属性增益变为对应的属性减益」。
"""

from __future__ import annotations

import random

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry
from backend.vm.effect import StatBuffEffect

factory = SimFactory()
_STATS = ("atk", "def", "sp_atk", "sp_def", "speed")


def _battle(skills_a=("猛烈撞击",), skills_b=("猛烈撞击",), name_a="草衣虫", name_b="花衣蝶"):
    p1 = factory.build_player("A", [{"name": name_a, "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": name_b, "skills": list(skills_b)}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _steps(sprite, stat: str) -> int:
    return sum(e.steps for e in sprite.active_effects
               if isinstance(e, StatBuffEffect) and e.stat_key == stat)


def _total(sprite) -> int:
    return sum(_steps(sprite, s) for s in _STATS)


# ═══════════════════════════════════════════════════════════════════
# stat_random
# ═══════════════════════════════════════════════════════════════════

def test_overlay_state_grants_16_random_layers():
    random.seed(7)
    battle = _battle(skills_a=("叠加态",))
    user = battle.player_a.active

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert _total(user) == 16
    # 逐层随机分配：至少分布在两个维度上（16 层只落一维的概率极低，且已播种）
    assert len([s for s in _STATS if _steps(user, s) != 0]) >= 2


def test_stat_random_negative_direction():
    """stat_random direction:"negative" → 全部为负层数。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatRandom

    battle = _battle()
    opp = battle.player_b.active
    r = JournalReplayer(battle.player_a.active, opp, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    random.seed(3)
    r.replay([StatRandom(target="sprite_opp", layers=5, direction="negative")])

    assert _total(opp) == -5
    assert all(_steps(opp, s) <= 0 for s in _STATS)


def test_stat_random_accepts_stats_subset():
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatRandom

    battle = _battle()
    opp = battle.player_b.active
    r = JournalReplayer(battle.player_a.active, opp, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    random.seed(5)
    r.replay([StatRandom(target="sprite_opp", layers=4, direction="positive",
                         stats=("def",))])

    assert _steps(opp, "def") == 4
    assert _total(opp) == 4


def test_haoshiyong_choice_counter_scales_layers():
    """做好事：层数 = 精灵级计数器 choice_skill_used × 3，用完清零。"""
    random.seed(11)
    battle = _battle(skills_b=("做好事",))
    user = battle.player_b.active
    user.inc_counter("choice_skill_used", 2)

    battle._execute_skill_vm("B", Action("skill", skill_index=0), is_first=True)

    assert _total(user) == 6
    assert user.get_counter("choice_skill_used") == 0


def test_chidushi_debuffs_opponent_and_resets_counter():
    random.seed(13)
    battle = _battle(skills_b=("吃独食",))
    user = battle.player_b.active
    opp = battle.player_a.active
    user.inc_counter("choice_skill_used", 1)

    battle._execute_skill_vm("B", Action("skill", skill_index=0), is_first=True)

    assert _total(opp) == -3
    assert user.get_counter("choice_skill_used") == 0


def test_choice_skill_used_counter_incremented_by_engine():
    """引擎在「选择」技能结算后给使用者 +1（做好事/吃独食的数据来源）。"""
    battle = _battle(skills_a=("下注",))
    user = battle.player_a.active
    assert user.get_counter("choice_skill_used") == 0

    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=0), is_first=True)

    assert user.get_counter("choice_skill_used") == 1


# ═══════════════════════════════════════════════════════════════════
# stat_convert
# ═══════════════════════════════════════════════════════════════════

def test_diaobao_converts_positive_to_negative():
    battle = _battle(skills_a=("掉包",))
    opp = battle.player_b.active
    opp.add_effect(StatBuffEffect(name="atk", source="t", stat_key="atk",
                                  steps=5, scope="battlefield"))
    opp.add_effect(StatBuffEffect(name="speed", source="t", stat_key="speed",
                                  steps=3, scope="battlefield"))

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert _steps(opp, "atk") == -5
    assert _steps(opp, "speed") == -3


def test_stat_convert_keeps_layer_count():
    battle = _battle(skills_a=("掉包",))
    opp = battle.player_b.active
    opp.add_effect(StatBuffEffect(name="sp_atk", source="t", stat_key="sp_atk",
                                  steps=2, scope="battlefield"))

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert abs(_steps(opp, "sp_atk")) == 2


def test_stat_convert_leaves_negative_untouched():
    battle = _battle(skills_a=("掉包",))
    opp = battle.player_b.active
    opp.add_effect(StatBuffEffect(name="def", source="t", stat_key="def",
                                  steps=-4, scope="battlefield"))

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert _steps(opp, "def") == -4


def test_stat_convert_reverse_direction():
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatConvert

    battle = _battle()
    opp = battle.player_b.active
    opp.add_effect(StatBuffEffect(name="atk", source="t", stat_key="atk",
                                  steps=-6, scope="battlefield"))
    r = JournalReplayer(battle.player_a.active, opp, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([StatConvert(target="sprite_opp", from_="negative", to="positive")])

    assert _steps(opp, "atk") == 6


def test_stat_convert_energy_cost_uses_inverted_sign_convention():
    """能耗通道：-N 才是增益（与 _match_stat_effect 同口径）。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatConvert

    battle = _battle()
    opp = battle.player_b.active
    opp.add_effect(StatBuffEffect(name="energy_cost", source="t",
                                  stat_key="energy_cost", steps=-2, scope="battlefield"))
    r = JournalReplayer(battle.player_a.active, opp, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([StatConvert(target="sprite_opp", from_="positive", to="negative")])

    assert _steps(opp, "energy_cost") == 2


def test_stat_convert_syncs_modifier_mirror():
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatConvert

    battle = _battle()
    opp = battle.player_b.active
    opp.add_effect(StatBuffEffect(name="power", source="t", stat_key="power",
                                  steps=30, display_value=30.0, scope="battlefield"))
    opp._modifiers["power"] = 30
    r = JournalReplayer(battle.player_a.active, opp, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([StatConvert(target="sprite_opp", from_="positive", to="negative")])

    assert _steps(opp, "power") == -30
    assert opp._modifiers["power"] == -30


def test_stat_convert_does_not_touch_unrelated_modifiers():
    """只翻「有对应 StatBuffEffect 的维度」，不误伤 max_energy / 能耗倍率等。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatConvert

    battle = _battle()
    opp = battle.player_b.active
    opp._modifiers["max_energy"] = 4
    opp._modifiers["energy_cost_delta_mult"] = 1.0
    opp.add_effect(StatBuffEffect(name="atk", source="t", stat_key="atk",
                                  steps=3, scope="battlefield"))
    r = JournalReplayer(battle.player_a.active, opp, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([StatConvert(target="sprite_opp", from_="positive", to="negative")])

    assert _steps(opp, "atk") == -3
    assert opp._modifiers["max_energy"] == 4
    assert opp._modifiers["energy_cost_delta_mult"] == 1.0
