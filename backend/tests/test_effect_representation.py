# -*- coding: utf-8 -*-
"""效果表示契约：**留在引擎状态里的效果列表只准存 IR**（`data/IR_GUIDE.md` §3D）。

为什么单独立一个测试：`BattleSkill._burst_effects` 里混进未编译 dict 曾经造成千局级才撞上的
崩溃（`'WhenBlock' object has no attribute 'get'`，2026-09-23），而读它的人无法从类型上判断
里面是什么。本文件把"三条写入路径都写入前编译"变成可执行的检查：

| 载体 | 写入路径 |
|---|---|
| `BattleSkill._burst_effects` | ①技能显式 `then`（编译期）②`from:"triggered"`（池里就是 IR）③特性 direct-mods（trait_loader 自己编译） |
| `BattleVMEngine._burst_effects[team]` | `execute()` 的 `is_first` 分支登记 |
| `BattleVMEngine._skill_history` | 同上，`replay from:"sprite_self"` 直接执行它们 |
| `Observer.then` | 注册时 `_index()` 编译（注册前是 raw dict 列表） |

对应断言函数 `vm.executor.assert_ir_effects`，调用点都在写入/登记边界。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.engine.battle import BattleVMEngine
from backend.engine.observer import Observer, ObserverRegistry
from backend.engine.replayer import JournalReplayer
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.pipeline import TurnPipeline
from backend.sim.traits import dispatch_entry
from backend.vm.executor import compile_effects_batch
from backend.vm.journal import ReplayChoice

factory = SimFactory()


def make_battle(skills_a):
    p1 = factory.build_player("A", [{"name": "神谕鲨", "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    for sprite in list(p1.team) + list(p2.team):
        sprite.energy = 10
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def use(battle, team, idx, **kw):
    return battle._execute_skill_vm(team, Action("skill", skill_index=idx), **kw)


def _not_dict(entries) -> bool:
    return all(not isinstance(e, dict) for e in entries)


def test_burst_and_history_carriers_hold_ir_only():
    """踏雷（triggered 授予）+ 星火（迸发登记）走完，三类载体里都不许有 dict。"""
    battle = make_battle(["星火", "踏雷", "猛烈撞击"])
    use(battle, "A", 0, is_first=True)                  # 星火以迸发身份使用 → 池 + 历史登记
    battle.player_a.active.first_action = False
    use(battle, "A", 1, is_first=False)                 # 踏雷授予迸发（else 分支，count=1）
    battle._write_turn_match_counters()
    battle.player_a.active.first_action = True
    battle.turn += 1
    battle._ctx_team_cache.clear()
    TurnPipeline.execute_turn_start(battle)
    use(battle, "A", 2, is_first=True)                  # 猛烈撞击以迸发身份执行

    vm = battle._vm_engine
    assert vm._burst_effects["A"], "池应当有登记，否则这个测试没覆盖到契约"
    for skill_name, effects in vm._burst_effects["A"]:
        assert _not_dict(effects), f"迸发池 {skill_name} 存了 dict"

    assert vm._skill_history, "技能历史应当有登记"
    for _sprite_id, history in vm._skill_history.items():
        for skill_name, effects, _tags in history:
            assert _not_dict(effects), f"技能历史 {skill_name} 存了 dict"

    granted = [e for bs in battle.player_a.active.skills for e in bs._burst_effects]
    assert granted, "踏雷应当把迸发效果授予攻击技能"
    assert _not_dict(granted), "迸发槽里混进了未编译的 dict"


def test_trait_direct_burst_grant_compiles_before_write():
    """特性 direct-mods 通道（生物电）要在**写入前**编译，而不是把 JSON dict 塞进槽位。"""
    battle = make_battle(["踏雷", "猛烈撞击"])
    sprite = battle.player_a.active
    loader = battle._vm_engine.trait_loader
    path = Path(__file__).resolve().parents[2] / "data" / "traits" / "生物电.json"
    trait_effect = json.loads(path.read_text(encoding="utf-8"))["effects"][0]

    loader._apply_direct_mods(sprite, [trait_effect])

    tailei = next(bs for bs in sprite.skills if bs.name == "踏雷")
    assert tailei._burst_effects, "生物电（电系技能）应当授予踏雷一个迸发效果"
    assert _not_dict(tailei._burst_effects)
    delta = getattr(tailei._burst_effects[0], "delta", None)
    assert getattr(delta, "value", delta) == -2      # 编译后 delta 是 Literal 节点


def test_get_effects_returns_ir_even_for_dict_records():
    """`_get_effects` 是技能侧效果的唯一出口：给 dict 记录也要编译成 IR 再交出去。"""
    class _DictRecord:
        name = "stub"
        effects = [{"op": "power_mod", "target": "sprite_self", "attr": "power", "delta": 3}]

    out = BattleVMEngine._get_effects(_DictRecord())
    assert out and _not_dict(out), "dict 记录应当被就地编译"

    # 已经是 IR 时必须**原样返回**（身份不变）：execute() 按 id(tuple) 命中排序缓存
    ir = compile_effects_batch([{"op": "power_mod", "target": "sprite_self",
                                 "attr": "power", "delta": 1}])

    class _IRRecord:
        name = "stub"
        effects = ir

    assert BattleVMEngine._get_effects(_IRRecord()) is ir


def test_observer_then_is_compiled_at_registration():
    """Observer.then：注册前是 raw dict 列表，注册后必须是 IR tuple（幂等，可重复注册）。"""
    registry = ObserverRegistry()
    raw = [{"op": "power_mod", "target": "sprite_self", "attr": "power", "delta": 2}]
    obs = Observer(cond={"cond": "turn_start"}, then=list(raw), source="测试")
    assert isinstance(obs.then[0], dict)

    registry.register(obs)
    assert _not_dict(obs.then)

    # 重复索引（同为 IR）不应抛错、也不改变身份
    compiled = obs.then
    registry._index(obs)
    assert obs.then is compiled


def test_replay_choice_handler_actually_replays_the_branch():
    """`ReplayChoice`（一意孤行「额外使用 1 次相同的选择效果」）必须真的重放，不能静默失败。

    2026-09-23 审计：`replayer._apply_replay_choice` 用了**没导入**的 `vm_execute`，
    而唯一触发路径（post_skill observer → `_fire_post_event`）把它包在
    `try/except Exception: continue` 里 —— 异常被吞掉，这条机制一直**静默不生效**。
    """
    battle = make_battle(["猛烈撞击"])
    eff = compile_effects_batch([
        {"op": "abnormal", "target": "sprite_opp", "name": "灼烧", "stacks": 2,
         "scope": "battlefield", "source": "测试"},
    ])
    battle._last_choice_execution = {
        "choices": [{"name": "t", "cond": None, "effects": list(eff)}],
        "branch": 0, "skill_name": "猛烈撞击",
    }
    replayer = JournalReplayer(battle.player_a.active, battle.player_b.active,
                               battle.globals, battle._vm_engine.registry,
                               team="A", battle=battle)
    before = battle.player_b.active._cached_abnormals.get("灼烧", 0)

    replayer.replay([ReplayChoice(which="same")])

    after = battle.player_b.active._cached_abnormals.get("灼烧", 0)
    assert after == before + 2, "分支效果没有被重放（正是缺少 vm_execute 导入时的表现）"
