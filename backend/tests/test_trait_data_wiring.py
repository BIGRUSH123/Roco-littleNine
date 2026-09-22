"""特性数据接线：盗魂铃「在场时自己回复的能量-4」。

数据面（`data/traits/盗魂铃.json`）用 `power_mod{target:"sprite_self", attr:"energy_gain_delta",
delta:-4, scope:"battlefield"}` 声明；落点是精灵级 `ModifierEffect(attr="energy_gain_delta")`
（`Sprite.energy_gain_delta` 优先读它），由所有 `gain_energy()` 路径消费。

用户 2026-09-22 确认这条口径（同一批决策里还确认了：本系加成 = 1.25、过山车不触发机械变式、
体重 sidecar 的基名歧义取字典序第一个）。
"""
import json
from pathlib import Path

from backend.engine.replayer import JournalReplayer
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.vm.executor import compile_effects_batch

_PROJ = Path(__file__).resolve().parent.parent.parent


def _battle():
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "草衣虫", "skills": ["猛烈撞击"]}])
    p2 = factory.build_player("B", [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    b = Battle(p1, p2, verbose=False)
    b.player_a.active_index = b.player_b.active_index = 0
    return b


def test_daohunling_declares_energy_gain_delta_minus_4():
    """数据面必须真的带上这条（否则整条子句静默缺失）。"""
    trait = json.loads((_PROJ / "data" / "traits" / "盗魂铃.json").read_text(encoding="utf-8"))
    ops = [
        op
        for effect in trait["effects"]
        for op in (effect.get("then") or [])
        if isinstance(op, dict)
        and op.get("op") == "power_mod"
        and op.get("attr") == "energy_gain_delta"
    ]
    assert len(ops) == 1, f"盗魂铃应有且仅有一条 energy_gain_delta: {ops}"
    assert ops[0]["delta"] == -4 and ops[0]["target"] == "sprite_self"


def test_daohunling_observer_makes_gather_gain_1_instead_of_5():
    """按特性 JSON 的 then[] 走引擎 op + replayer（= observer 触发路径）后：
    在场时聚能 +5 → 实得 +1。"""
    b = _battle()
    me = b.player_a.active
    opp = b.player_b.active
    trait = json.loads((_PROJ / "data" / "traits" / "盗魂铃.json").read_text(encoding="utf-8"))
    then_ops = trait["effects"][0]["then"]
    ctx = b._make_ctx(me, opp, None, None, b.globals, team="A", turn=b.turn)
    journal = b._vm_engine.execute_effects(ctx, compile_effects_batch(then_ops))
    JournalReplayer(me, opp, b.globals, b._vm_engine.registry,
                    team="A", self_skill=None, battle=b).replay(journal)

    assert me.energy_gain_delta == -4
    me.energy = 0
    assert me.gain_energy(5) == 1, "回能修正没被 gain_energy 消费"
    assert me.energy == 1

    # 对照组：不带这条修正时聚能 +5
    b2 = _battle()
    me2 = b2.player_a.active
    me2.energy = 0
    assert me2.gain_energy(5) == 5
