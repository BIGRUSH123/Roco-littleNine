"""引擎缺口回归测试（第二批）：heal 按次结算 / Skill.tag / reset / skill_name_self /
生命代替能量。

覆盖的缺口：
- Gap1  `heal` 按每次释放结算（`per_hit` 数据 flag 已删除）
- Gap3  `Skill.tag` 未加载 → 技能自带「迅捷」整类失效（入场迅捷）
- Gap4  `reset` op 是空实现（气沉丹田「使用后能耗重置」）
- Gap6  `ctx.skill_name_self` 从未赋值（`trait_path` path:"skill"）
- Gap7  `flag:"life_as_energy"` 无消费者（虚假破产）
- Gap8  `blood_price` 必须预先存在 → 虚假破产/骗局首次使用无法用生命支付
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.engine.replayer import JournalReplayer
from backend.engine.trait_loader import TraitLoader
from backend.sim.action import Action
from backend.sim.battle import (
    Battle,
    declared_hp_energy_price,
    sprite_hp_energy_price,
)
from backend.sim.factory import SimFactory
from backend.sim.round_record import ActionRecord, RoundRecord
from backend.sim.skill import Skill
from backend.sim.traits import dispatch_entry
from backend.vm.compiler.skill_compiler import SkillCompiler
from backend.vm.cond import eval_one
from backend.vm.ctx import Ctx
from backend.vm.executor import process_one
from backend.vm.ir_skill import HealOp
from backend.vm.journal import Reset

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


# ── Gap1: heal 按次结算（`per_hit` 字段已删除，见 IR_GUIDE §八「连击语义」）──

def test_heal_settles_once_per_release():
    """2 连击技能：heal 按每次释放结算（2 次 × 10% = 20）。

    历史：曾由数据 flag `per_hit`（默认 true、可写 false 退出）控制；该字段已整体删除，
    「哪些效果按次结算」现在由 op 与 target 决定（自技能参数类只结算一次）。
    """
    compiler = SkillCompiler()
    base = {"id": 999001, "name": "T_HIT2", "element": "普通",
            "skill_type": "物攻", "power": 50, "energy_cost": 2, "combo": 2}

    effects = [{"op": "heal", "target": "sprite_self", "ratio": 0.1}]
    cs = compiler.compile(dict(base, effects=effects))
    heal_ops = [o for o in cs.effects if isinstance(o, HealOp)]
    assert heal_ops, "heal op 应被编译出来"
    ctx = Ctx()
    ctx.combo_self = 2
    ctx.hp_self_max = 100
    muts = []
    for op in cs.effects:
        muts.extend(process_one(ctx, op))
    heals = [m for m in muts if type(m).__name__ == "Heal"]
    assert len(heals) == 2, f"2 连击应结算 2 次治疗，实际 {len(heals)}"
    assert sum(m.amount for m in heals) == 20


# ── Gap3: Skill.tag + 入场迅捷 ──────────────────────────────────────

def test_skill_load_reads_tag_field():
    """`Skill.load` 必须读 JSON 顶层 `tag`（与 transmission/exclusive_to 同风格）。"""
    for name in ("惊鸿一瞥", "飞羽", "风墙", "翼击", "羽化加速"):
        raw = json.loads((_PROJ / "data" / "skills" / f"{name}.json").read_text("utf-8"))
        assert Skill.load(raw).tag == "迅捷", name


def test_swift_tag_skill_is_found_on_entry_switch():
    """带 tag:"迅捷" 的技能在换人入场时自动出招（此前 `bs.base.tag` 恒空 → 整类失效）。"""
    b = _make_battle(["猛烈撞击"],
                     team_b=None,
                     ) if False else _make_battle(["猛烈撞击"])
    p1 = factory.build_player("A", [
        {"name": "水灵", "skills": ["猛烈撞击"]},
        {"name": "花衣蝶", "skills": ["猛烈撞击", "飞羽", "甩水"]},
    ])
    p2 = factory.build_player("B", [{"name": "水灵", "skills": ["猛烈撞击"]}])
    b = Battle(player_a=p1, player_b=p2, verbose=False)
    b.species_db = factory.sprite_db
    b.skill_loader = factory._build_skill_list
    b.player_a.active_index = 0
    b.player_b.active_index = 0

    bench = b.player_a.team[1]
    idx, bs = b._find_first_swift_skill(bench)
    assert (idx, bs.name if bs else None) == (1, "飞羽")

    record = RoundRecord(1)
    record.action_a = ActionRecord(team="A", actor="花衣蝶", kind="switch")
    record.action_b = ActionRecord(team="B", actor="水灵", kind="skill")
    b._phase_resolve(Action(kind="switch", switch_index=1),
                     Action(kind="skill", skill_index=0), record)
    assert "花衣蝶 迅捷：飞羽" in record.action_a.events


# ── Gap4: reset op ─────────────────────────────────────────────────

def test_reset_op_restores_skill_base_energy_cost():
    """气沉丹田：先被「应对成功 -3」修正两次，使用后能耗重置回基础值。"""
    b = _make_battle(["气沉丹田", "猛烈撞击"])
    me, opp = b.player_a.active, b.player_b.active
    bs = me.skills[0]
    b._turn_skills['A'] = {"name": "气沉丹田"}
    for i in (1, 2):
        b._vm_engine.execute_skill(
            me, opp, bs, None, b.globals, team='A', turn=i,
            effects=[{"op": "power_mod", "target": "skill_off_0",
                      "scope": "permanent", "attr": "energy_cost", "delta": -3}],
            battle=b, battle_skill=bs,
        )
    assert bs._modifiers["energy_cost"] == -6.0
    assert me._modifiers["skill.气沉丹田.energy_cost"] == -6.0

    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))

    assert "energy_cost" not in bs._modifiers, "技能槽增量应被重置"
    assert "skill.气沉丹田.energy_cost" not in me._modifiers, "永久登记应被清掉"
    assert bs.energy_cost == bs.base.energy_cost


def test_reset_op_sprites_can_call_dispatch():
    """`reset` 仍在 dispatch 表里（确保实现替换后仍被调用）。"""
    assert JournalReplayer._DISPATCH[Reset] == JournalReplayer._apply_reset


# ── Gap6: skill_name_self ──────────────────────────────────────────

def test_skill_name_self_register_and_condition():
    b = _make_battle(["暖阳", "猛烈撞击"])
    me, opp = b.player_a.active, b.player_b.active
    ctx = b._make_ctx(me, opp, me.skills[0], None, b.globals, team='A', turn=1)
    assert ctx.skill_name_self == "暖阳"
    ctx2 = b._make_ctx(me, opp, me.skills[1], None, b.globals, team='A', turn=1)
    assert ctx2.skill_name_self == "猛烈撞击"

    cond = {"cond": "trait_path", "path": "skill", "op": "eq", "value": "暖阳"}
    assert eval_one(ctx, cond) is True
    assert eval_one(ctx2, cond) is False


def test_skill_name_self_observer_end_to_end():
    """observer(post_skill, path:"skill"=="暖阳") 只在用暖阳时触发。"""
    b = _make_battle(["暖阳", "猛烈撞击"])
    me = b.player_a.active
    orig = TraitLoader._load_trait_data
    TraitLoader._load_trait_data = lambda self, tid, name: {
        "id": 999999, "name": name,
        "effects": [{
            "op": "observer", "listen": "post_skill", "scope": "persistent",
            "cond": {"cond": "trait_path", "path": "skill", "op": "eq", "value": "暖阳"},
            "then": [{"op": "stat_stage", "target": "sprite_self",
                      "stat": "atk", "steps": 1}],
        }],
    }
    try:
        me.species.ability = "技能名测试特性"
        me.species.ability_id = 999999
        dispatch_entry(me, b, "A")

        def atk_stage():
            return sum(e.steps for e in me.active_effects
                       if getattr(e, "stat_key", "") == "atk")

        b._execute_skill_vm('A', Action(kind='skill', skill_index=1))
        assert atk_stage() == 0
        b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
        assert atk_stage() == 1
    finally:
        TraitLoader._load_trait_data = orig


# ── Gap7 / Gap8: 生命代替能量 ───────────────────────────────────────

def test_false_bankruptcy_pays_hp_on_first_use():
    """虚假破产首次使用（精灵级还没有 flag）即可用生命支付。"""
    b = _make_battle(["虚假破产", "猛烈撞击"])
    me, bs = b.player_a.active, b.player_a.active.skills[0]
    assert sprite_hp_energy_price(me) == 0.0
    assert declared_hp_energy_price(b._get_skill_record("虚假破产")) == 0.05
    assert b.hp_energy_price(me, bs) == 0.05

    me.energy = 0
    hp0 = me.current_hp
    ok, cost, hp_cost = b.can_pay_skill_energy_cost('A', me, bs)
    assert (ok, cost) == (True, 2)
    events = b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    assert any("HP代替2E" in e for e in events)
    assert me.current_hp == hp0 - hp_cost
    # 技能真的生效（减伤 80% 落到精灵级 _modifiers）
    assert me._modifiers.get("damage_reduction") == 0.8


def test_life_as_energy_alias_is_gone():
    """同一机制不留两个拼写：`life_as_energy` 已删除，唯一名字是 `blood_price`。"""
    b = _make_battle(["猛烈撞击"])
    me = b.player_a.active
    # 旧别名不再被识别（写了也不生效）
    me._modifiers["life_as_energy"] = 0.05
    assert sprite_hp_energy_price(me) == 0.0
    me._modifiers.pop("life_as_energy")
    me._modifiers["blood_price"] = 0.05
    assert sprite_hp_energy_price(me) == 0.05


def test_blood_price_sprite_level_still_consumed_by_gate():
    """既有口径不回归：精灵级 blood_price（石头大餐）仍能代替能量。"""
    b = _make_battle(["猛烈撞击"])
    me = b.player_a.active
    me._modifiers["blood_price"] = 0.05
    me.energy = 0
    events = b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    assert any("HP代替1E" in e for e in events)
