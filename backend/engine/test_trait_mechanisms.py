"""机制型特性测试 — IR 原语（aura/counter/element_convert/morph/grant_choice/convert_all）。

覆盖 15 个特性：与星星同行、先知、合拍、和弦共振、噼啪噼啪！、守护之心、安眠、
展翅、异类、扫荡、拉拉队长、换碟、草木苏醒时、长久保存制法、魔术帽。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.engine import mechanisms, morph
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.battleskill import BattleSkill
from backend.sim.factory import SimFactory
from backend.sim.skill import Skill
from backend.sim.traits import dispatch_entry
from backend.vm.effect import AbnormalEffect, StatBuffEffect

factory = SimFactory()


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _battle(trait_a: str = "", trait_b: str = "",
            skills_a=("猛烈撞击", "甩水", "防御"),
            skills_b=("猛烈撞击", "甩水", "防御"),
            name_a: str = "草衣虫", name_b: str = "花衣蝶"):
    p1 = factory.build_player("A", [{"name": name_a, "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": name_b, "skills": list(skills_b)}])
    if trait_a:
        p1.team[0].species.ability = trait_a
        p1.team[0].species.ability_id = 0
    if trait_b:
        p2.team[0].species.ability = trait_b
        p2.team[0].species.ability_id = 0
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db  # 形态变换（萌化链）查询用
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _steps(sprite, stat: str) -> int:
    return sum(e.steps for e in sprite.active_effects
               if isinstance(e, StatBuffEffect) and e.stat_key == stat)


def _add_marks(battle, team: str, **marks):
    for name, stacks in marks.items():
        battle.globals.apply_mark(team, name, battle.globals.classify_mark(name), stacks)


# ═══════════════════════════════════════════════════════════════════
# 与星星同行 — 印记合并为星陨
# ═══════════════════════════════════════════════════════════════════

def test_convert_all_marks_merges_into_starfall():
    battle = _battle("与星星同行")
    b = battle.player_b.active
    # 正负各一枚（同类印记互斥，与游戏规则一致）
    _add_marks(battle, "B", 湿润印记=3, 减速=2)

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    names = {m.name: m.stacks for m in battle.globals.mark_effects.get("B", [])}
    assert names == {"星陨印记": 5}, names


def test_convert_all_matches_reflected_in_ctx():
    battle = _battle("与星星同行")
    b = battle.player_b.active
    _add_marks(battle, "B", 减速=4)
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    names = {m.name: m.stacks for m in battle.globals.mark_effects.get("B", [])}
    assert names == {"星陨印记": 4}, names


# ═══════════════════════════════════════════════════════════════════
# 先知 — 致死预测 aura
# ═══════════════════════════════════════════════════════════════════

def test_lethal_forecast_aura_applies_and_clears():
    battle = _battle("先知")
    a, b = battle.player_a.active, battle.player_b.active
    b.skills = [BattleSkill(Skill(name="巨力", element="普通", skill_type="物攻",
                                 power=400, energy_cost=0))]
    a.current_hp = 1

    mechanisms.refresh(battle)
    assert _steps(a, "speed") == 5, _steps(a, "speed")
    assert _steps(a, "atk") == 5
    assert _steps(a, "sp_atk") == 5

    # 解除致命威胁 → 光环撤销
    a.current_hp = a.max_hp
    battle.player_b.active.skills = []
    mechanisms.refresh(battle)
    assert _steps(a, "speed") == 0
    assert _steps(a, "atk") == 0


# ═══════════════════════════════════════════════════════════════════
# 合拍 — 回合技能比对
# ═══════════════════════════════════════════════════════════════════

def test_turn_match_counter_and_bonus():
    battle = _battle("合拍")
    a, b = battle.player_a.active, battle.player_b.active
    battle._turn_skills = {
        "A": {"element": "火", "skill_type": "物攻", "energy_cost": 3},
        "B": {"element": "火", "skill_type": "物攻", "energy_cost": 2},
    }
    battle._write_turn_match_counters()
    assert battle.team_counters["A"]["turn_match"] == 2
    assert battle.team_counters["B"]["turn_match"] == 2

    ctx = battle._make_ctx(a, b, None, None, battle.globals, team="A",
                           turn=1, turn_end=True)
    battle._vm_engine.fire_trigger("turn_end", ctx, a, b, battle.globals,
                                   team="A", battle=battle)
    assert _steps(a, "atk") == 2
    assert _steps(a, "def") == 2


# ═══════════════════════════════════════════════════════════════════
# 和弦共振 / 守护之心 — 种类计数 aura
# ═══════════════════════════════════════════════════════════════════

def test_mark_kinds_aura_tracks_battlefield():
    battle = _battle("和弦共振")
    a = battle.player_a.active
    mechanisms.refresh(battle)
    assert _steps(a, "sp_atk") == 0

    _add_marks(battle, "A", 湿润印记=1)
    _add_marks(battle, "B", 减速=2)
    mechanisms.refresh(battle)
    assert _steps(a, "sp_atk") == 10  # 2 种（正/负各一）× 5 步

    battle.globals.mark_effects["B"] = []
    mechanisms.refresh(battle)
    assert _steps(a, "sp_atk") == 5

    # 离场（battlefield 清除）后归零
    a.clear_effects("battlefield")
    battle.player_b.active.clear_effects("battlefield")
    mechanisms.refresh(battle)
    assert _steps(a, "sp_atk") == 0
    assert a.counters.get("aura:和弦共振:sp_atk", 0) == 0


def test_positive_kinds_aura():
    battle = _battle("守护之心")
    a, b = battle.player_a.active, battle.player_b.active
    mechanisms.refresh(battle)
    assert _steps(a, "def") == 0

    a.add_effect(StatBuffEffect(name="atk", source="t", stat_key="atk", steps=2))
    b.add_effect(StatBuffEffect(name="atk", source="t", stat_key="atk", steps=1))
    b.add_effect(StatBuffEffect(name="def", source="t", stat_key="def", steps=1))
    mechanisms.refresh(battle)
    assert _steps(a, "def") == 4  # 2 种不同增益（atk/def 各按一种计）× 2 步


# ═══════════════════════════════════════════════════════════════════
# 噼啪噼啪！ — 使用次数 +1 与行动后回能
# ═══════════════════════════════════════════════════════════════════

def test_use_count_bonus_and_energy_refund():
    battle = _battle("噼啪噼啪！")
    a = battle.player_a.active
    assert a._modifiers.get("use_count_bonus") == 1

    a.energy = 5
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert a.get_counter("skill_used:猛烈撞击") == 2, a.counters
    assert a._modifiers.get("use_count_bonus") in (None, 0)
    assert a.energy == 5 - 1 + 2  # 耗 1 回 2


# ═══════════════════════════════════════════════════════════════════
# 安眠 — 入夜世界状态
# ═══════════════════════════════════════════════════════════════════

def test_night_energy_cost_and_turn_end_recovery():
    battle = _battle("安眠")
    a = battle.player_a.active
    battle.globals.night = True
    a.entry_turn = battle.turn
    ctx = battle._make_ctx(a, battle.player_b.active, None, None, battle.globals,
                           team="A", turn=battle.turn)
    battle._vm_engine.fire_trigger("post_entry", ctx, a, battle.player_b.active,
                                   battle.globals, team="A", battle=battle,
                                   species_lookup=battle.lookup_species_by_number)
    costs = [bs.energy_cost for bs in a.skills]
    base = [bs.base.energy_cost for bs in a.skills]
    assert costs == [c + 2 for c in base], (costs, base)

    a.current_hp = 50
    a.energy = 3
    ctx_te = battle._make_ctx(a, battle.player_b.active, None, None, battle.globals,
                              team="A", turn=battle.turn, turn_end=True)
    battle._vm_engine.fire_trigger("turn_end", ctx_te, a, battle.player_b.active,
                                   battle.globals, team="A", battle=battle,
                                   species_lookup=battle.lookup_species_by_number)
    assert a.current_hp > 50
    assert a.energy == 4


def test_day_night_flag_defaults_off():
    battle = _battle("安眠")
    a = battle.player_a.active
    assert battle.globals.night is False
    assert [bs.energy_cost for bs in a.skills] == [bs.base.energy_cost for bs in a.skills]


# ═══════════════════════════════════════════════════════════════════
# 展翅 — 属性转换 + 后手受伤
# ═══════════════════════════════════════════════════════════════════

def test_element_convert_and_second_action_penalty():
    battle = _battle("展翅", skills_a=("猛烈撞击",))
    a, b = battle.player_a.active, battle.player_b.active
    bs = a.skills[0]
    assert bs.base.element == "普通"
    assert mechanisms.element_for(battle, a, bs) == "翼"

    # 先手：无补正
    battle._fire_pre_resolve("A", "B", bs, None)
    assert a._modifiers.get("damage_reduction", 0.0) == 0.0

    # 后手：-0.25 受伤补正
    battle._fire_pre_resolve("B", "A", None, bs)
    assert round(a._modifiers.get("damage_reduction", 0.0), 4) == -0.25


def test_element_convert_only_for_matching_skills():
    battle = _battle("展翅", skills_a=("猛烈撞击", "甩水"))
    a = battle.player_a.active
    water = a.skills[1]
    assert water.base.element == "水"
    assert mechanisms.element_for(battle, a, water) == ""


# ═══════════════════════════════════════════════════════════════════
# 异类 — 分支授予
# ═══════════════════════════════════════════════════════════════════

def test_granted_choice_only_for_matching_skills():
    battle = _battle("异类", skills_a=("俯冲", "猛烈撞击"))
    a = battle.player_a.active
    wing, normal = a.skills[0], a.skills[1]
    assert wing.base.element == "翼" and wing.base.is_attack
    assert len(mechanisms.choices_for(battle, a, "", wing)) == 1
    assert mechanisms.choices_for(battle, a, "", normal) == []


def test_granted_choice_branch_costs_energy_and_drains():
    battle = _battle("异类", skills_a=("俯冲",))
    a, b = battle.player_a.active, battle.player_b.active
    a.energy = 8
    b.current_hp = b.max_hp
    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=1), is_first=True)
    # 俯冲 7 能耗 +1 分支能耗 = 8
    assert a.energy == 0, a.energy
    assert a.current_hp > a.max_hp - a.max_hp  # 吸血生效 → 血量高于初始


# ═══════════════════════════════════════════════════════════════════
# 扫荡 — 敌方行动计数
# ═══════════════════════════════════════════════════════════════════

def test_enemy_action_counter_feeds_entry_bonus():
    battle = _battle("扫荡")
    a = battle.player_a.active
    battle.inc_team_counter("A", "enemy_action", 3)
    a.entry_turn = battle.turn
    ctx = battle._make_ctx(a, battle.player_b.active, None, None, battle.globals,
                           team="A", turn=battle.turn)
    battle._vm_engine.fire_trigger("post_entry", ctx, a, battle.player_b.active,
                                   battle.globals, team="A", battle=battle,
                                   species_lookup=battle.lookup_species_by_number)
    assert _steps(a, "sp_atk") == 6
    assert _steps(a, "sp_def") == 3


def test_gather_and_switch_increment_enemy_action():
    battle = _battle()
    battle._execute_skill_vm("A", Action("gather"), is_first=True)
    assert battle.get_team_counter("B", "enemy_action") == 1
    assert battle.get_team_counter("B", "enemy_gather") == 1


# ═══════════════════════════════════════════════════════════════════
# 拉拉队长 — 萌化反制
# ═══════════════════════════════════════════════════════════════════

def test_leader_cancels_second_moe():
    # 花衣蝶 有前置形态（草衣虫），可萌化
    battle = _battle("拉拉队长", name_a="花衣蝶")
    a = battle.player_a.active
    assert a._moe_position == 0
    a.apply_moe(1, battle)
    assert a._moe_position == 1
    # 再次萌化 → 解除
    a.apply_moe(1, battle)
    assert a._moe_position == 2
    ctx = battle._make_ctx(a, battle.player_b.active, None, None, battle.globals,
                           team="A", turn=1,
                           abnormal_applied_name="萌化",
                           abnormal_applied_target="sprite_self")
    battle._vm_engine.fire_trigger("post_abnormal_apply", ctx, a, battle.player_b.active,
                                   battle.globals, team="A", battle=battle,
                                   species_lookup=battle.lookup_species_by_number)
    assert a._moe_position == 0, a._moe_position


def test_first_moe_is_not_cancelled():
    battle = _battle("拉拉队长", name_a="花衣蝶")
    a = battle.player_a.active
    a.apply_moe(1, battle)
    ctx = battle._make_ctx(a, battle.player_b.active, None, None, battle.globals,
                           team="A", turn=1,
                           abnormal_applied_name="萌化",
                           abnormal_applied_target="sprite_self")
    battle._vm_engine.fire_trigger("post_abnormal_apply", ctx, a, battle.player_b.active,
                                   battle.globals, team="A", battle=battle,
                                   species_lookup=battle.lookup_species_by_number)
    assert a._moe_position == 1


# ═══════════════════════════════════════════════════════════════════
# 换碟 / 魔术帽 — 巧变
# ═══════════════════════════════════════════════════════════════════

def test_disc_swap_morph_is_temporary_and_cheaper():
    """巧变：使用后变为随机同类技能（能耗-1），使用该随机技能后还原原技能。"""
    battle = _battle("换碟", skills_a=("音波弹",))
    a = battle.player_a.active
    bs = a.skills[0]
    assert bs._modifiers.get("power", 0) == 20
    assert mechanisms.morph_category(battle, a, bs) == "same_element"

    pool = morph._morph_pool_for_test("普通", "音波弹")
    assert pool, "同系别技能池不应为空"

    a.energy = 10
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert bs.replaced_by is not None
    assert bs.replaced_by.name in pool
    assert bs.replaced_by.element == "普通"
    assert bs._morph_temp is True

    # 巧变产物：能耗 -1
    morphed_cost = bs.base.energy_cost if bs.replaced_by is None else bs.replaced_by.energy_cost
    assert battle.skill_energy_cost("A", a, bs, 0) == max(0, morphed_cost - 1)

    # 使用该随机技能 → 还原原技能
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert bs.replaced_by is None
    assert bs._morph_temp is False
    assert bs.name == "音波弹"


def test_morph_pool_is_random_but_in_pool():
    pool = set(morph._morph_pool_for_test("普通", "音波弹"))
    picks = {morph.pick(None, None, _Stub("音波弹", "普通"), "same_element", 0)
             for _ in range(60)}
    assert picks and picks <= pool
    assert len(picks) > 1, "随机选池应覆盖多个候选"


def test_magic_hat_morphs_both_sides():
    battle = _battle("魔术帽", skills_b=("甩水",))
    a, b = battle.player_a.active, battle.player_b.active
    bs_b = b.skills[0]
    assert mechanisms.morph_category(battle, b, bs_b) == "same_element"
    battle.turn = 1
    battle._execute_skill_vm("B", Action("skill", skill_index=0), is_first=True)
    assert bs_b.replaced_by is not None
    assert bs_b.replaced_by.element == "水"


def test_morph_pool_excludes_self_and_exclusive():
    assert "音波弹" not in morph._morph_pool_for_test("普通", "音波弹")
    index = morph._load_index()
    for name in morph._morph_pool_for_test("普通", "音波弹"):
        assert not index[name][2], name  # 无专属归属


def test_inherent_qiaobian_metadata_morphs_skill():
    """技能自带的「巧变：恶系攻击技能」（假冒）走同一条 morph 机制。"""
    battle = _battle(skills_a=("假冒",))
    a = battle.player_a.active
    bs = a.skills[0]
    assert bs.base.qiaobian == {"element": "恶", "skill_type": "attack"}
    assert morph.register_skill_morphs(a) == 0  # Battle.__init__ 已登记
    assert mechanisms.morph_category(battle, a, bs) == {"element": "恶",
                                                       "skill_type": "attack"}
    a.energy = 8
    battle.turn = 2
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert bs.replaced_by is not None
    assert bs.replaced_by.element == "恶"
    assert bs.replaced_by.is_attack


def test_filter_pool_by_description_substring():
    """「巧变：冻结相关技能」按描述子串取池。"""
    pool = morph._build_filter(None, None, _Stub("寒潮"), {"contains": "冻结"})
    assert pool, "冻结相关技能池不应为空"
    assert "寒潮" not in pool
    for name in pool:
        assert "冻结" in morph._load_index()[name][4], name


def test_filter_pool_by_exact_name():
    """「巧变：虫鸣」（齐鸣）按精确技能名取池。"""
    pool = morph._build_filter(None, None, _Stub("啃咬"), {"name": "虫鸣"})
    assert pool == ("虫鸣",), pool


# ═══════════════════════════════════════════════════════════════════
# 引电 — 状态层数阈值（nrc 游戏内文本）
# ═══════════════════════════════════════════════════════════════════

def test_yindian_threshold_triggers_at_two_stacks():
    battle = _battle()
    a, b = battle.player_a.active, battle.player_b.active
    b.current_hp = b.max_hp
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    # 手动施加 2 层引电（通电的实际效果见技能测试）
    from backend.vm.journal import AbnormalChange
    r = __import__('backend.engine.replayer', fromlist=['JournalReplayer']).JournalReplayer(
        a, b, battle.globals, battle._vm_engine.registry, team="A", battle=battle)
    r.replay([AbnormalChange(target="sprite_opp", name="引电", delta=1)])
    assert b.get_stacks("引电") == 1
    hp_before = b.current_hp
    r.replay([AbnormalChange(target="sprite_opp", name="引电", delta=1)])
    assert b.get_stacks("引电") == 0, b.get_stacks("引电")
    assert b.current_hp < hp_before, (b.current_hp, hp_before)


def test_yindian_immune_for_electric_species():
    battle = _battle(name_b="拉特")
    a, b = battle.player_a.active, battle.player_b.active
    assert "电" in (getattr(b.species, 'elements', ()) or ()), b.species.elements
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import AbnormalChange
    r = JournalReplayer(a, b, battle.globals, battle._vm_engine.registry, team="A",
                        battle=battle)
    r.replay([AbnormalChange(target="sprite_opp", name="引电", delta=1)])
    hp = b.current_hp
    r.replay([AbnormalChange(target="sprite_opp", name="引电", delta=1)])
    assert b.current_hp == hp, "电系精灵应免疫引电阈值伤害"


def test_yindian_is_abnormal_not_mark():
    """引电是「状态」而非「印记」（nrc 页分类），不应参与印记互斥。"""
    from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
    from backend.engine.mark_config import MARK_TEMPLATES
    assert "引电" in ABNORMAL_TEMPLATES
    assert "引电" not in MARK_TEMPLATES
    assert ABNORMAL_TEMPLATES["引电"].threshold_damage_pct == 0.25


def test_yindian_end_to_end_skill_grants_trigger():
    """通电（技能）连续两次授予引电 → 2 层触发阈值伤害，且不占用印记位。"""
    battle = _battle(skills_a=("通电",), skills_b=("猛烈撞击",))
    a, b = battle.player_a.active, battle.player_b.active
    b.current_hp = b.max_hp
    a.energy = 10
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert b.get_stacks("引电") == 1
    assert not battle.globals.mark_effects.get("B"), "引电不应写入印记"
    hp_before = b.current_hp
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert b.get_stacks("引电") == 0, b.get_stacks("引电")
    assert b.current_hp < hp_before, (b.current_hp, hp_before)


class _Stub:
    __slots__ = ("element", "name")

    def __init__(self, name: str, element: str = "") -> None:
        self.name = name
        self.element = element


# ═══════════════════════════════════════════════════════════════════
# 草木苏醒时 — 累积/重置
# ═══════════════════════════════════════════════════════════════════

def test_counter_accumulates_and_resets_on_attack():
    battle = _battle("草木苏醒时")
    a, b = battle.player_a.active, battle.player_b.active
    ctx = battle._make_ctx(a, b, None, None, battle.globals, team="A", turn=1,
                           energy_changed_of="sprite_self")
    ctx.energy_delta_self = 2
    battle._vm_engine.fire_trigger("post_energy_change", ctx, a, b, battle.globals,
                                   team="A", battle=battle)
    assert a.counters.get("grass_wake") == 2
    assert _steps(a, "atk") == 4
    assert _steps(a, "sp_atk") == 4

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert a.counters.get("grass_wake") == 0
    assert _steps(a, "atk") == 0
    assert _steps(a, "sp_atk") == 0


# ═══════════════════════════════════════════════════════════════════
# 长久保存制法 — 聚能分支
# ═══════════════════════════════════════════════════════════════════

def test_gather_branch_steals_energy():
    battle = _battle("长久保存制法")
    a, b = battle.player_a.active, battle.player_b.active
    a.energy, b.energy = 3, 8
    assert mechanisms.choices_for(battle, a, action="gather")
    battle._execute_skill_vm("A", Action("gather", branch=1), is_first=True)
    assert b.energy == 5, b.energy
    assert a.energy == 6, a.energy


def test_gather_default_branch_is_normal():
    battle = _battle("长久保存制法")
    a, b = battle.player_a.active, battle.player_b.active
    a.energy, b.energy = 3, 8
    battle._execute_skill_vm("A", Action("gather"), is_first=True)
    assert a.energy == 8
    assert b.energy == 8


# ═══════════════════════════════════════════════════════════════════
# 原语单测：counter / aura 幂等 / 分支能耗
# ═══════════════════════════════════════════════════════════════════

def test_counter_op_add_and_set():
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute
    from backend.vm.journal import CounterWrite

    ctx = Ctx()
    journal = execute(ctx, [{"op": "counter", "key": "k", "delta": 3}])
    assert isinstance(journal[0], CounterWrite) and journal[0].delta == 3

    journal = execute(ctx, [{"op": "counter", "key": "k", "mode": "set", "value": 0}])
    assert journal[0].mode == "set" and journal[0].delta == 0


def test_aura_op_emits_mechanism_grant():
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute
    from backend.vm.journal import MechanismGrant

    ctx = Ctx()
    journal = execute(ctx, [{"op": "aura", "stat": "atk", "per_unit": 3,
                             "count": "mark_kinds_both", "source": "T"}])
    m = journal[0]
    assert isinstance(m, MechanismGrant)
    assert m.mechanism == "aura" and m.payload["stat"] == "atk"


def test_unknown_count_source_is_ignored():
    from backend.vm.ctx import Ctx
    from backend.vm.executor import execute

    battle = _battle()
    a = battle.player_a.active
    ctx = Ctx()
    journal = execute(ctx, [{"op": "aura", "stat": "atk", "count": "nope",
                             "source": "T"}])
    from backend.engine.replayer import JournalReplayer
    r = JournalReplayer(a, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay(journal)
    mechanisms.refresh(battle)
    assert _steps(a, "atk") == 0


def test_branch_energy_cost_applies_to_payment_only():
    """龙守望「本次能耗-1」只作用于本次支付，不残留。"""
    battle = _battle(skills_a=("龙守望",))
    a = battle.player_a.active
    a.energy = 5
    battle._execute_skill_vm("A", Action("skill", skill_index=0, branch=0), is_first=True)
    assert a.energy == 5, a.energy       # 1 能耗 - 1 = 0
    assert a.skills[0]._modifiers.get("energy_cost", 0) == 0



# ═══════════════════════════════════════════════════════════════════
# 变形活画 / 陨落 / 齐鸣 — 三条新语义（speed_flat / turn_end_block / affects=team）
# ═══════════════════════════════════════════════════════════════════

def test_speed_flat_uses_point_channel():
    """变形活画「速度+5」走 speed_flat 点数通道：1 点 = 1 点，不是 10 点/步。"""
    battle = _battle("变形活画", skills_a=("猛烈撞击",))
    a, b = battle.player_a.active, battle.player_b.active
    base_speed = a.initial_stats["speed"]
    assert a.effective_stat("speed") == base_speed

    # 敌方 1 层增益 → 本次行动技能威力倍率应为 1 + 0.1×1（总量语义）
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatChange

    JournalReplayer(b, a, battle.globals, battle._vm_engine.registry,
                    team="B", battle=battle).replay([
        StatChange(target="sprite_self", stat="atk", steps=2, scope="battlefield")])

    battle.turn = 1
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert _steps(a, "speed_flat") == 5, _steps(a, "speed_flat")
    assert a.effective_stat("speed") == base_speed + 5
    assert abs(a._modifiers.get("power_mult", 1.0) - 1.1) < 1e-9, a._modifiers


def test_speed_flat_snapshot_and_ratio_stack():
    """速度三段口径：base + 步数×10 + 点数，最后乘百分比修正。"""
    battle = _battle()
    a = battle.player_a.active
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import StatChange

    base_speed = a.initial_stats["speed"]
    r = JournalReplayer(a, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([
        StatChange(target="sprite_self", stat="speed", steps=2, scope="battlefield"),
        StatChange(target="sprite_self", stat="speed_flat", steps=5, scope="battlefield"),
    ])
    assert a.effective_stat("speed") == base_speed + 25

    a._modifiers["speed"] = 0.2          # 「攻防速+20%」通道
    assert a.effective_stat("speed") == round((base_speed + 25) * 1.2)


def test_meteor_fall_blocks_turn_end_triggers():
    """陨落：在场时双方回合末触发全部不发生（含 trait turn_end 观察者）。"""
    battle = _battle("陨落", "吸积盘")
    battle.turn = 3
    battle._phase_turn_end()
    assert battle.globals.get_mark_by_name("A", "星陨印记") is None

    # 对照组：无陨落时，吸积盘 正常在回合末叠 2 层
    control = _battle("", "吸积盘")
    control.turn = 3
    control._phase_turn_end()
    mark = control.globals.get_mark_by_name("A", "星陨印记")
    assert mark is not None and mark.stacks == 2


def test_turn_end_block_leaves_bookkeeping_alone():
    """抑制只作用于「触发」：turn 作用域清理与 ttl 衰减照常。"""
    from backend.vm.effect import StatBuffEffect

    battle = _battle("陨落")
    a = battle.player_a.active
    a.active_effects.append(StatBuffEffect(name="T", source="T", scope="turn",
                                           stat_key="atk", steps=2))
    a._invalidate_effects_cache()
    battle.turn = 2
    battle._phase_turn_end()
    assert _steps(a, "atk") == 0, "turn 作用域效果应照常清除"


def test_qiming_grants_team_wide_qiaobian():
    """齐鸣：己方精灵（含场下）携虫系技能获得巧变：虫鸣；自身虫鸣威力+20。"""
    p1 = factory.build_player("A", [
        {"name": "暮风隐者（金黄的样子）", "skills": ["虫鸣", "甩水"]},
        {"name": "花衣蝶", "skills": ["假寐", "甩水"]},
    ])
    p2 = factory.build_player("B", [{"name": "草衣虫", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p1.team[1], battle, "A")

    qi, mate = p1.team[0], p1.team[1]
    assert qi.species.ability == "齐鸣"
    assert qi.skills[0].base.name == "虫鸣"
    assert qi.skills[0]._modifiers.get("power", 0) == 20, qi.skills[0]._modifiers

    mate_bs = next(bs for bs in mate.skills if bs.element == "虫")
    assert mate_bs.base.name == "假寐"
    assert mechanisms.morph_category(battle, mate, mate_bs) == {"name": "虫鸣"}

    # 场下队友使用该虫系技能 → 变为 虫鸣（一次性巧变）
    morph.apply_after_use(battle, "A", mate, 0, mate_bs)
    assert mate_bs.replaced_by is not None
    assert mate_bs.replaced_by.name == "虫鸣"
    assert mate_bs._morph_temp is True

    # 授予者离场（battlefield 作用域清理）后，队伍级巧变仍在（persistent）
    qi.clear_effects("battlefield")
    assert mechanisms.morph_category(battle, mate, mate_bs) == {"name": "虫鸣"}


def test_team_morph_absent_without_grantor():
    """对照：没有齐鸣时，虫系技能不带巧变。"""
    p1 = factory.build_player("A", [{"name": "花衣蝶", "skills": ["假寐", "甩水"]}])
    p2 = factory.build_player("B", [{"name": "草衣虫", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    mate_bs = next(bs for bs in p1.team[0].skills if bs.element == "虫")
    assert mechanisms.morph_category(battle, p1.team[0], mate_bs) == ""
