"""连击（combo）语义测试 — 见 data/IR_GUIDE.md §八「连击语义」。

钉住四条规则：
1. 连击 = 技能**释放次数**；AI 估伤（resolver.calc_damage）与引擎实战同口径
2. 技能效果一律按**每次释放**各结算一次（引擎按 op 与 target 自动判定，无数据 flag）
3. 修改**自己本技能参数**的 power_mod/mult_mod（target skill_off_0）只结算一次
4. **精灵级**连击增益（combo/combo_set/combo_mult）只对带连击词条的技能生效
   （词条 = JSON 写了 `combo` 键）；技能自身的连击文本（target skill_off_0）不受门控
"""
from pathlib import Path

import pytest

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.battleskill import SkillUse, effective_combo
from backend.sim.factory import SimFactory
from backend.vm.ctx import Ctx
from backend.vm.ops.abnormal import op_abnormal
from backend.vm.ops.mod import op_power_mod, op_stat_stage

_PROJ = Path(__file__).resolve().parent.parent.parent


# ══════════════════════════════════════════════════════════════════
# 战斗夹具
# ══════════════════════════════════════════════════════════════════

def _make_battle(skills_a, skills_b=None, team_a=None):
    factory = SimFactory()
    roster_a = team_a or [{"name": "水灵", "skills": skills_a}]
    p1 = factory.build_player("A", roster_a)
    p2 = factory.build_player("B", [{"name": "水灵", "skills": skills_b or ["猛烈撞击"]}])
    b = Battle(player_a=p1, player_b=p2)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    return b


def _live_damage(skills_a, patch=None, team=None):
    b = _make_battle(skills_a, team_a=team)
    if patch:
        patch(b)
    opp = b.player_b.active
    hp0 = opp.current_hp
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    return hp0 - opp.current_hp


def _estimate(skills_a, patch=None):
    b = _make_battle(skills_a)
    if patch:
        patch(b)
    me, opp = b.player_a.active, b.player_b.active
    dmg, _ = b._resolver.calc_damage(
        me, opp, SkillUse(battle_skill=me.skills[0]), b.globals, attacker_team='A')
    return dmg


# ══════════════════════════════════════════════════════════════════
# 1) 估伤 == 实战
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("skill", ["虫刺", "午夜噪音", "双响炮", "撒娇"])
def test_estimate_matches_live_for_combo_skill(skill):
    """连击技能的 AI 估伤必须等于实战伤害（此前估伤读 use.multi_hit → 少算 N 倍）。"""
    est = _estimate([skill])
    live = _live_damage([skill])
    assert est == live, f"{skill}: 估伤 {est} != 实战 {live}"


def test_combo_declared_on_sajiao():
    """撒娇 desc「造成魔伤，3连击」→ 3 段伤害（此前漏写 combo 字段，少打 2/3）。

    用 approx：连击是「单次伤害事件 × N」，与 N 次独立命中的差异是每段取整
    （见 IR_GUIDE「连击语义」的伤害结算说明）。
    """
    three = _live_damage(["撒娇"])
    single = _live_damage(["撒娇"], patch=lambda b: setattr(b.player_a.active.skills[0].base,
                                                             "combo", 1))
    assert three == pytest.approx(3 * single, rel=0.03), f"3连击 {three} vs 单发 {single}"


# ══════════════════════════════════════════════════════════════════
# 2) 效果默认按每次释放结算
# ══════════════════════════════════════════════════════════════════

def test_abnormal_repeats_per_release():
    """连续毒针 2 连击「每次连击使敌方获得1层中毒」→ 2 层（效果按次结算）。"""
    b = _make_battle(["连续毒针"])
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    assert b.player_b.active.get_stacks('中毒') == 2


def test_mark_repeats_per_release():
    """星链 2 连击 → 星陨印记 2 层。"""
    b = _make_battle(["星链"])
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    mark = b.globals.get_mark_by_name('B', '星陨印记')
    assert mark is not None and mark.stacks == 2


def test_stat_stage_repeats_per_release():
    """三连破 3 连击「自己获得物攻+30%」→ +9 步。"""
    from backend.vm.effect import StatBuffEffect
    b = _make_battle(["三连破"])
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    atk = [e for e in b.player_a.active.active_effects
           if isinstance(e, StatBuffEffect) and e.stat_key == 'atk']
    assert len(atk) == 1 and atk[0].steps == 9


def test_heal_repeats_per_release():
    """聚盐 2 连击「每次连击自己回复8%生命」→ 共回复 16%（heal 也按次结算，无 flag）。"""
    b = _make_battle(["聚盐"])
    me = b.player_a.active
    me.current_hp = max(1, me.current_hp - 80)
    hp0 = me.current_hp
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    gained = me.current_hp - hp0
    assert gained == pytest.approx(0.16 * me.max_hp, rel=0.12), f"回复 {gained}，应为 2×8%"


# ══════════════════════════════════════════════════════════════════
# 3) 自技能参数类只结算一次
# ══════════════════════════════════════════════════════════════════

def test_self_skill_param_modifier_applies_once():
    """引雷「2连击，迸发：本次技能威力+20」= 2 段各 55 威力（不是 55+75）。"""
    ctx = Ctx(combo_self=2)
    muts = op_power_mod(ctx, {"op": "power_mod", "target": "skill_off_0",
                              "attr": "power", "delta": 20})
    assert len(muts) == 1, "自技能威力修正应只结算一次"


def test_self_skill_combo_modifier_applies_once():
    """传感器「本技能位于1号或3号位时连击+1」= +1（不是 +2）。"""
    ctx = Ctx(combo_self=2)
    muts = op_power_mod(ctx, {"op": "power_mod", "target": "skill_off_0",
                              "attr": "combo", "delta": 1})
    assert len(muts) == 1


def test_opponent_debuff_repeats_per_release():
    """冰捆缚「2连击，每次连击敌方获得全技能能耗+1」→ +2（对方技能是被作用对象）。"""
    ctx = Ctx(combo_self=2)
    muts = op_power_mod(ctx, {"op": "power_mod", "target": "sprite_opp",
                              "attr": "energy_cost", "delta": 1})
    assert len(muts) == 2


def test_stat_stage_repeats_by_default_at_op_level():
    ctx = Ctx(combo_self=3)
    muts = op_stat_stage(ctx, {"op": "stat_stage", "target": "sprite_self",
                               "stat": "atk", "steps": 1})
    assert len(muts) == 3


# ══════════════════════════════════════════════════════════════════
# 4) 连击词条门控
# ══════════════════════════════════════════════════════════════════

def _with_sprite_mod(key, value):
    def patch(b):
        b.player_a.active._modifiers[key] = value
    return patch


def test_external_combo_buff_gated_by_keyword():
    """精灵级『连击数+3』(热身运动)：无词条技能不变，连击技能 +3 段。"""
    plain = _live_damage(["猛烈撞击"], patch=_with_sprite_mod("combo", 3))
    assert plain == _live_damage(["猛烈撞击"]), "单发技能不该被全局连击增益影响"

    combo_skill = _live_damage(["虫刺"], patch=_with_sprite_mod("combo", 3))
    base = _live_damage(["虫刺"])
    assert combo_skill == pytest.approx(2 * base, rel=0.05), "3+3=6 段 ≈ 2 倍"


def test_external_combo_mult_gated_by_keyword():
    """精灵级 combo_mult（暴风眼 +100%）：只对带连击词条的技能翻倍。"""
    assert _live_damage(["猛烈撞击"], patch=_with_sprite_mod("combo_mult", 1.0)) \
        == _live_damage(["猛烈撞击"])
    doubled = _live_damage(["虫刺"], patch=_with_sprite_mod("combo_mult", 1.0))
    assert doubled == pytest.approx(2 * _live_damage(["虫刺"]), rel=0.05)


def test_combo1_skill_has_keyword_and_takes_buff():
    """「1连击」技能（音波弹/落石）：基础 1 次但**带连击词条**，吃得到精灵级连击增益。

    游戏原文里这类技能就是「为吃连击加成、同时避免太强」而写「1连击」的。
    词条判据 = JSON 写了 combo 键（值可以是 1），不是 combo≥2。
    """
    for skill in ("音波弹", "落石"):
        base = _live_damage([skill])
        buffed = _live_damage([skill], patch=_with_sprite_mod("combo", 3))
        assert buffed == pytest.approx(4 * base, rel=0.06), \
            f"{skill}（1连击）应 1+3=4 段: {base} -> {buffed}"

    # 对照：原文没有「连击」的技能不吃增益
    assert _live_damage(["猛烈撞击"], patch=_with_sprite_mod("combo", 3)) \
        == _live_damage(["猛烈撞击"])


def test_combo1_keyword_flag_on_skill_object():
    b = _make_battle(["音波弹"])
    bs = b.player_a.active.skills[0]
    assert bs.base.combo == 1 and bs.combo_keyword is True
    b2 = _make_battle(["猛烈撞击"])
    assert b2.player_a.active.skills[0].combo_keyword is False


def test_own_combo_text_not_gated():
    """虫鸣的自身文本「队伍中的精灵每携带1个虫鸣，本次技能连击数+1」不受门控：
    队伍携带 1 个 → 2 段；携带 2 个 → 3 段（`skill_count_own` 快照寄存器）。"""
    one_carrier = _live_damage(["虫鸣"])
    two_carriers = _live_damage(
        ["虫鸣"], team=[{"name": "水灵", "skills": ["虫鸣"]},
                        {"name": "水灵", "skills": ["虫鸣"]}])
    assert two_carriers > one_carrier, f"携带 2 个应更多段: {two_carriers} vs {one_carrier}"
    assert two_carriers == pytest.approx(1.5 * one_carrier, rel=0.05)


def test_effective_combo_helper_respects_gate():
    b = _make_battle(["猛烈撞击"])
    me = b.player_a.active
    me._modifiers["combo"] = 3
    assert effective_combo(me.skills[0], me) == 1, "无词条 → 忽略精灵级增益"


# ══════════════════════════════════════════════════════════════════
# 5) 数据一致性：desc 写了 N连击 就必须声明 combo
# ══════════════════════════════════════════════════════════════════

def test_combo_data_matches_desc():
    """数据一致性（双向）：技能 desc 里写「N连击」⟺ JSON 声明 `combo` 键，且值 = N。

    `"combo": 1` 是**合法且必要**的写法——游戏原文「1连击」的技能（追打/乘胜追击/
    音波弹/落石/啃咬/多维击打）就是靠它带词条、吃连击加成。
    """
    import json
    import re
    pat = re.compile(r'(\d+)连击')
    missing, stray, mismatch = [], [], []
    for p in sorted((_PROJ / 'data' / 'skills').glob('*.json')):
        j = json.loads(p.read_text(encoding='utf-8'))
        m = pat.search(j.get('description', '') or '')
        if m and 'combo' not in j:
            missing.append(f"{p.stem}(desc {m.group(1)}连击)")
        elif not m and 'combo' in j:
            stray.append(f"{p.stem}(combo={j['combo']})")
        elif m and int(j['combo']) != int(m.group(1)):
            mismatch.append(f"{p.stem}(desc {m.group(1)}连击 vs combo={j['combo']})")
    assert not missing, "desc 有 N连击 但没声明 combo 键：" + "; ".join(missing)
    assert not stray, "desc 无连击却声明了 combo 键（会白吃连击加成）：" + "; ".join(stray)
    assert not mismatch, "连击数与描述不一致：" + "; ".join(mismatch)
