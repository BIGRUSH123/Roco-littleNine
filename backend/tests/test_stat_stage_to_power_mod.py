"""`stat_stage` 只管五维：越界维度（power/energy_cost/combo）已迁到 `power_mod`。

背景（2026-09-22）：`stat_stage` 写的是 `StatBuffEffect`，只有 `Sprite.effective_stat`
（五维）与 UI 读它；`power`/`energy_cost`/`combo` 三个维度**实战无人读**，属静默失效。
6 处数据（traits：斗技/守护者/捉迷藏/自由飘/稀兽花宝×2）已迁到 `power_mod`：

| 数据 | 迁移后 | 落地通道 |
|---|---|---|
| 斗技 全技能威力永久+30 | `power_mod attr:power skill_filter:"all" scope:permanent` | 每技能 `_modifiers["power"]`（= 实战 `bs.power`） |
| 守护者 全技能能耗-N | `power_mod attr:energy_cost skill_filter:"all"` | 每技能 `_modifiers["energy_cost"]` |
| 捉迷藏 敌方全技能能耗+1 | 同上（target sprite_opp） | 同上 |
| 自由飘/稀兽花宝 连击±N | `power_mod attr:combo`（**不带** skill_filter） | 精灵级 `_modifiers["combo"]`（连击词条门控） |

本文件钉住两件事：数据面不留越界 `stat_stage`（双向 lint），以及迁移后的效果真的落地。
"""
import json
from pathlib import Path

import pytest

from backend.engine.replayer import JournalReplayer
from backend.sim.battle import Battle
from backend.sim.battleskill import effective_combo
from backend.sim.factory import SimFactory
from backend.sim.skill import Skill
from backend.vm.effect import AbnormalEffect
from backend.vm.executor import compile_effects_batch

_PROJ = Path(__file__).resolve().parent.parent.parent
_DATA = _PROJ / "data"

#: `stat_stage` 的合法维度（与 `vm/compiler/passes/skill_validate.VALID_STAGE_STATS` 一致）
VALID_STAGE_STATS = frozenset({"atk", "def", "sp_atk", "sp_def", "speed", "speed_flat"})


# ── 双向 lint：数据面不允许越界 stat_stage ──

def _walk_stat_stages(node, path, out):
    if isinstance(node, dict):
        if node.get("op") == "stat_stage":
            stat = node.get("stat")
            if isinstance(stat, str) and not stat.startswith("=") and stat not in VALID_STAGE_STATS:
                out.append((path, stat))
        for key, value in node.items():
            _walk_stat_stages(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk_stat_stages(value, f"{path}[{i}]", out)


def test_no_out_of_band_stat_stage_in_data():
    """skills + traits 全库扫描：`stat_stage` 的 stat 只能是五维（或动态公式）。"""
    offenders = []
    for sub in ("skills", "traits"):
        for path in sorted((_DATA / sub).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            hits: list = []
            _walk_stat_stages(data.get("effects", []), "effects", hits)
            offenders += [(f"{sub}/{path.name}", p, s) for p, s in hits]
    assert not offenders, f"越界 stat_stage（该迁 power_mod）: {offenders}"


def test_validator_whitelist_matches_this_lint():
    """白名单与 lint 用同一份维度集合（防两边漂移）。"""
    from backend.vm.compiler.passes.skill_validate import VALID_STAGE_STATS as _V
    assert set(_V) == set(VALID_STAGE_STATS)


# ── 迁移后的效果真的落地（走引擎自己的 op + replayer）──

def _battle(a_skills, a_name="草衣虫", team_extra=None):
    factory = SimFactory()
    a = [{"name": a_name, "skills": list(a_skills)}]
    if team_extra:
        a += team_extra
    p1 = factory.build_player("A", a)
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    b = Battle(p1, p2, verbose=False)
    b.player_a.active_index = b.player_b.active_index = 0
    return b


def _trait_then(trait_file: str) -> list:
    data = json.loads((_DATA / "traits" / trait_file).read_text(encoding="utf-8"))
    return data["effects"][0]["then"]


def _fire(b, team: str, then_ops: list, team_index: int = 0):
    """按特性 JSON 的 then[] 走引擎 op + replayer（= observer 触发时的同一路径）。"""
    compiled = compile_effects_batch(then_ops)
    self_sprite = b.get_player(team).team[team_index]
    opp = b.get_opponent(team).active
    ctx = b._make_ctx(self_sprite, opp, None, None, b.globals, team=team, turn=b.turn)
    journal = b._vm_engine.execute_effects(ctx, compiled)
    rp = JournalReplayer(self_sprite, opp, b.globals, b._vm_engine.registry,
                         team=team, self_skill=None, battle=b)
    rp.replay(journal)
    return self_sprite, opp


def _add_moe(sprite, stacks: int) -> None:
    sprite.active_effects.append(AbnormalEffect(
        name="萌化", source="test", scope="persistent", stacks=stacks, tick_damage_pct=0.0))
    sprite._invalidate_effects_cache()


def test_douji_all_skill_power_plus_30():
    """斗技：全技能威力永久+30 —— 迁移前只有 AI 估伤读到，实战无效。"""
    b = _battle(["猛烈撞击", "虫刺"])
    me = b.player_a.active
    assert [bs.power for bs in me.skills] == [65, 15]
    sprite, _ = _fire(b, "A", _trait_then("斗技.json"))
    assert [bs.power for bs in sprite.skills] == [95, 45]

    # 实战与估伤同口径（迁移前：估伤 67、实战 46 —— 只有 AI 生效）
    from backend.sim.action import Action
    from backend.sim.battleskill import SkillUse
    est, _ = b._resolver.calc_damage(
        me, b.player_b.active, SkillUse(battle_skill=me.skills[0], skill_index=0),
        b.globals, attacker_team='A')
    opp = b.player_b.active
    hp0 = opp.current_hp
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    assert est == (hp0 - opp.current_hp), f"估伤 {est} ≠ 实战 {hp0 - opp.current_hp}"


def test_shouhuzhe_all_skill_energy_cost_reduced_by_moe_stacks():
    """守护者：己方其他精灵每层萌化 → 全技能能耗-1。"""
    b = _battle(["猛烈撞击"], team_extra=[{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    me = b.player_a.active
    mate = b.player_a.team[1]
    _add_moe(mate, 2)
    b._invalidate_ctx_team_cache()
    assert [bs.energy_cost for bs in me.skills] == [1]
    sprite, _ = _fire(b, "A", _trait_then("守护者.json"))
    assert [bs.energy_cost for bs in sprite.skills] == [-1]      # 1 - 2 层


def test_zhuomicang_raises_opponent_all_skill_energy_cost():
    """捉迷藏：敌方全技能能耗+1。"""
    b = _battle(["猛烈撞击"])
    opp = b.player_b.active
    assert [bs.energy_cost for bs in opp.skills] == [1]
    _, opp_after = _fire(b, "A", _trait_then("捉迷藏.json"))
    assert [bs.energy_cost for bs in opp_after.skills] == [2]


def test_ziyoupiao_combo_plus_3_per_moe_layer_is_keyword_gated():
    """自由飘：每层萌化连击数+3（精灵级通道 → 只对带连击词条的技能生效）。"""
    b = _battle(["虫刺", "猛烈撞击"])
    me = b.player_a.active
    _add_moe(me, 2)
    before = [effective_combo(bs, me) for bs in me.skills]
    assert before == [3, 1]
    sprite, _ = _fire(b, "A", _trait_then("自由飘.json"))
    after = [effective_combo(bs, sprite) for bs in sprite.skills]
    assert after == [9, 1], "连击词条门控失效（无词条的猛烈撞击不该变连击）"


def test_xishouhuabao_combo_plus_minus_3():
    """稀兽花宝：+3 连击给自己、-3 连击给敌方（同样门控在词条技能上）。"""
    trait = json.loads((_DATA / "traits" / "稀兽花宝.json").read_text(encoding="utf-8"))
    then_ops = [
        op
        for effect in trait["effects"]
        for op in (effect.get("then") or [])
        if isinstance(op, dict) and op.get("op") == "power_mod" and op.get("attr") == "combo"
    ]
    assert len(then_ops) == 2, f"稀兽花宝应有两处 combo 迁移: {then_ops}"

    b = _battle(["虫刺"])
    me = b.player_a.active
    opp = b.player_b.active
    opp.skills[0] = type(opp.skills[0])(
        base=Skill.load(json.loads((_DATA / "skills" / "虫刺.json").read_text(encoding="utf-8"))))
    # `sprite_opp` 是**相对施法者**解析的：两处都从特性持有者视角执行（= 引擎行为）
    _fire(b, "A", then_ops)
    assert effective_combo(me.skills[0], me) == 6      # 3 + 3
    assert effective_combo(opp.skills[0], opp) == 1    # 3 - 3 → 下限 1
