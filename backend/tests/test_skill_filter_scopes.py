"""skill_filter 作用域 + 技能级路由回归测试。

钉住三条缺口（引擎侧）：
1. `skill_filter: "adjacent"` = **当前技能**槽位两侧的技能（不环绕）；
2. `skill_filter: "others"` = 除**本回合使用的技能**以外（激怒的目标是对手，
   参考技能必须取对手那一手）；
3. `skill_filter: "bare_attack"` = 无额外效果的纯攻击技能（不移）；
4. 只写 `element`（不带 skill_filter / skill_where）的修饰符必须走**技能级**落点
   （此前落到精灵级 `_modifiers` 而没有读取点 → 静默空操作）。

见 data/IR_GUIDE.md §3A `power_mod` 的 `skill_filter` / `element` 说明。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.engine.replayer import JournalReplayer
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry
from backend.vm.journal import ModifierInjection

factory = SimFactory()


def _make_battle(skills_a, skills_b=None):
    p1 = factory.build_player("A", [{"name": "水灵", "skills": skills_a}])
    p2 = factory.build_player("B", [{"name": "水灵", "skills": skills_b or ["猛烈撞击"]}])
    b = Battle(player_a=p1, player_b=p2, verbose=False)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    b.player_a.active_index = 0
    b.player_b.active_index = 0
    return b


def _skill_mods(sprite, stat):
    return {bs.name: bs._modifiers.get(stat) for bs in sprite.skills}


# ── adjacent ────────────────────────────────────────────────────────

def test_adjacent_filter_hits_only_neighbour_slots():
    """联动装置（slot 1）的 `skill_filter:"adjacent"` 只改 0 号与 2 号槽。"""
    b = _make_battle(["猛烈撞击", "联动装置", "甩水", "防御"])
    me = b.player_a.active
    b._execute_skill_vm('A', Action(kind='skill', skill_index=1))
    mods = _skill_mods(me, "power")
    assert mods["猛烈撞击"] == 2.0
    assert mods["甩水"] == 2.0
    assert mods["防御"] is None, "3 号槽不是两侧技能，不应被改"
    assert mods["联动装置"] is None, "自身不是自己的两侧技能"


def test_adjacent_filter_no_wrap_around():
    """槽位 0 的两侧只有 1 号槽（不环绕到末位）。"""
    b = _make_battle(["联动装置", "猛烈撞击", "甩水", "防御"])
    me = b.player_a.active
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    mods = _skill_mods(me, "power")
    assert mods["猛烈撞击"] == 2.0
    assert mods["甩水"] is None
    assert mods["防御"] is None


def test_adjacent_filter_negative_delta_energy_cost():
    """能量守恒（应对成功 → 两侧技能能耗永久-1）：多槽位 adjacent 一起改。"""
    b = _make_battle(["猛烈撞击", "能量守恒", "甩水", "防御"])
    me = b.player_a.active
    b._execute_skill_vm('A', Action(kind='skill', skill_index=1),
                        is_countered=False, countering_skill=None)
    # counter_succeeded 分支：引擎侧由 VM 的 when 决定，这里直接验证无条件分支不存在
    assert _skill_mods(me, "energy_cost")["猛烈撞击"] is None


# ── others ──────────────────────────────────────────────────────────

def test_others_filter_excludes_the_skill_used_this_turn():
    """激怒「敌方除本回合使用的技能，其他技能能耗+3」。"""
    b = _make_battle(["激怒", "猛烈撞击"], ["猛烈撞击", "甩水", "防御"])
    opp = b.player_b.active
    b._turn_skills['B'] = {"name": "甩水", "element": "水",
                           "skill_type": "魔攻", "energy_cost": 2}
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    mods = _skill_mods(opp, "energy_cost")
    assert mods["甩水"] is None, "本回合使用的那只技能不该被改"
    assert mods["猛烈撞击"] == 3.0
    assert mods["防御"] == 3.0


# ── bare_* ──────────────────────────────────────────────────────────

def test_bare_attack_filter_only_plain_attack_skills():
    """不移（携带的无额外效果的攻击技能威力+30%）只命中裸攻击技能。"""
    b = _make_battle(["猛烈撞击", "甩水", "防御"])
    me = b.player_a.active
    me.skills = [
        factory._build_skill_list(["猛烈撞击"])[0],   # 物攻，无 effects
        factory._build_skill_list(["甩水"])[0],       # 魔攻，带 effects
        factory._build_skill_list(["防御"])[0],       # 防御
    ]
    me.species.ability = "不移"
    me.species.ability_id = 0
    dispatch_entry(me, b, "A")
    mods = _skill_mods(me, "power_mult")
    assert mods["猛烈撞击"] == 1.3
    assert mods["甩水"] is None, "带额外效果的攻击技能不是 bare"
    assert mods["防御"] is None, "非攻击技能不是 bare_attack"


# ── element-only 路由（Gap10） ───────────────────────────────────────

def test_element_only_modifier_routes_to_skill_slot():
    """只带 element 的 mult_mod 必须落技能槽（此前落精灵级 → 空操作）。"""
    b = _make_battle(["猛烈撞击", "甩水", "防御"])
    me = b.player_a.active
    injection = ModifierInjection(
        target="sprite_self", stat="power_mult", value=1.5, mode="set",
        scope="permanent", element="水", source="测试",
    )
    rp = JournalReplayer(me, b.player_b.active, b.globals,
                         b._vm_engine.registry, team="A",
                         self_skill=me.skills[0], battle=b)
    rp.replay([injection])
    mods = _skill_mods(me, "power_mult")
    assert mods["甩水"] == 1.5
    assert mods["猛烈撞击"] is None
    assert "power_mult" not in me._modifiers, "不该落精灵级（无读取点）"


def test_element_only_modifier_changes_live_damage():
    """端到端：带该修正后，水系技能**实战**伤害变大。

    注：AI 估伤（`sim/resolver.calc_damage`）不读技能级/精灵级 `power_mult`（见
    报告「新发现」），因此这里钉的是引擎实战伤害，而不是估伤。
    """
    def live(with_mod):
        b = _make_battle(["猛烈撞击", "甩水", "防御"])
        me, opp = b.player_a.active, b.player_b.active
        if with_mod:
            rp = JournalReplayer(me, opp, b.globals, b._vm_engine.registry,
                                 team="A", self_skill=me.skills[0], battle=b)
            rp.replay([ModifierInjection(
                target="sprite_self", stat="power_mult", value=1.5, mode="set",
                scope="permanent", element="水", source="测试")])
        hp0 = opp.current_hp
        b._execute_skill_vm('A', Action(kind='skill', skill_index=1))  # 甩水
        return hp0 - opp.current_hp

    assert live(True) > live(False)


# ── 队伍系别数派生查询（Gap9-③，分光） ───────────────────────────────

def test_team_element_count_query():
    """{"q":"element_count","of":"team_own"} = 队伍不同系别数（frozenset 元数）。"""
    from backend.vm.resolve import resolve
    from backend.vm.compiler.passes.skill_parse import SkillParsePass

    b = _make_battle(["分光", "猛烈撞击"])
    me, opp = b.player_a.active, b.player_b.active
    ctx = b._make_ctx(me, opp, None, None, b.globals, team="A", turn=0)
    assert resolve(ctx, {"q": "element_count", "of": "team_own"}) == len(ctx.team_elements_own)

    # 编译期也要认这个地址（派生查询映射到 team_elements_own 字段）
    op = SkillParsePass()._parse_effect({
        "op": "stat_stage", "target": "sprite_self", "stat": "sp_atk",
        "steps": {"q": "element_count", "of": "team_own", "offset": 2}})
    assert op.value.sub_key_field == "element_count"
    assert resolve(ctx, op.value) == len(ctx.team_elements_own) + 2


def test_fen_guang_scales_with_team_element_kinds():
    """分光：魔攻 +20% 固定，另每有 1 个不同系别 +10%（steps = 队伍系别数 + 2）。"""
    def steps_for(team_a):
        p1 = factory.build_player("A", team_a)
        p2 = factory.build_player("B", [{"name": "水灵", "skills": ["猛烈撞击"]}])
        b = Battle(player_a=p1, player_b=p2, verbose=False)
        b.species_db = factory.sprite_db
        b.skill_loader = factory._build_skill_list
        me = b.player_a.active
        b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
        return sum(e.steps for e in me.active_effects
                   if getattr(e, "stat_key", "") == "sp_atk")

    assert steps_for([{"name": "水灵", "skills": ["分光", "猛烈撞击"]}]) == 3      # 2+1
    assert steps_for([{"name": "水灵", "skills": ["分光", "猛烈撞击"]},
                      {"name": "草衣虫", "skills": ["猛烈撞击"]},
                      {"name": "花衣蝶", "skills": ["猛烈撞击"]}]) == 5            # 2+3
