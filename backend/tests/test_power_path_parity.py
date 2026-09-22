"""技能级威力修正的实战口径：隐式伤害必须走 `ctx.power_self`（含技能级修正）。

回归背景（2026-09-22）：`InjectHitPass` 曾把隐式 HitOp 的威力写死成 JSON 原始威力
（`Literal(value=...)`），导致「永久技能级威力修正」（`power_mod attr:"power"
scope:"permanent"`，数据面 29 个文件：超声波/磁暴/钢铁洪流/流星火雨/联动装置/…）
只对 AI 估伤生效、**实战无效**；同回合增量还会被 `modifiers.py` 的比例换算稀释。
修法：隐式 HitOp 的 power 改为 `Query(field="power_self")`。
"""
from pathlib import Path

import pytest

from backend.sim.action import Action
from backend.sim.battle import Battle, _load_permanent_skill_mods_for_sprite
from backend.sim.battleskill import SkillUse
from backend.sim.factory import SimFactory

_PROJ = Path(__file__).resolve().parent.parent.parent


def _battle():
    factory = SimFactory()
    p1 = factory.build_player("A", [{"name": "草衣虫", "skills": ["猛烈撞击"]}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    b = Battle(p1, p2, verbose=False)
    b.player_a.active_index = b.player_b.active_index = 0
    return b


def _live(b) -> int:
    opp = b.player_b.active
    hp0 = opp.current_hp
    b._execute_skill_vm('A', Action(kind='skill', skill_index=0))
    return hp0 - opp.current_hp


def _estimate(b) -> int:
    me, opp = b.player_a.active, b.player_b.active
    dmg, _ = b._resolver.calc_damage(me, opp, SkillUse(battle_skill=me.skills[0]),
                                     b.globals, attacker_team='A')
    return dmg


def _with_perm_power(delta: int):
    b = _battle()
    me = b.player_a.active
    me._modifiers[f"skill.猛烈撞击.power"] = delta
    _load_permanent_skill_mods_for_sprite(me)
    return b


def test_baseline_live_equals_estimate():
    b = _battle()
    assert _live(b) == _estimate(b)


def test_permanent_skill_power_modifier_applies_in_live_battle():
    """永久技能级威力+30：实战伤害必须上升，且与 AI 估伤同值。"""
    base = _live(_battle())
    boosted = _live(_with_perm_power(30))
    assert boosted > base, f"永久威力修正未进实战伤害: {base} -> {boosted}"
    assert boosted == _estimate(_with_perm_power(30)), "实战与估伤不同口径"


def test_permanent_skill_power_modifier_scales_as_expected():
    """+30 威力对 65 威力技能应约 ×1.46（95/65），不是「只影响估伤」。"""
    base = _live(_battle())
    boosted = _live(_with_perm_power(30))
    assert boosted == pytest.approx(base * 95 / 65, rel=0.05), f"{base} -> {boosted}"
