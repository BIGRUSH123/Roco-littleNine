"""hit opcode — independent damage hit separate from the skill's implicit attack.

V2: Supports typed HitOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..damage import calc_damage
from ..ir_skill import HitOp
from ..journal import Damage, Mutation
from ..resolve import resolve


def _stage_mult(steps: int) -> float:
    return steps * 0.1


def _type_mult(element: str, defender_elements) -> float:
    """属性克制倍率（对齐 SkillResolver._get_type_mult）。"""
    if not element or not defender_elements:
        return 1.0
    from backend.sim.resolver import _TYPE_CHART
    chart = _TYPE_CHART.get(element, {})
    mult = 1.0
    for attr in defender_elements:
        attr = str(attr).strip()
        if attr:
            mult *= chart.get(attr, 1.0)
    return mult


def _stab_mult(element: str, attacker_elements) -> float:
    """本系加成：技能系别在自身系别内 ×1.25。

    与估伤 `sim/resolver._get_stab` 同值。此前这里写 1.5 —— 结果同一手实战比估伤
    高 20%（用户 2026-09-22 确认游戏真值为 1.25，`vm/damage.py` 注释也写 1.25）。
    """
    if not element or not attacker_elements:
        return 1.0
    return 1.25 if element in tuple(attacker_elements) else 1.0


def _weather_mult(weather: str, element: str) -> float:
    from backend.common.constants import weather_damage_mult
    return weather_damage_mult(weather, element)


def op_hit(ctx: Ctx, effect) -> list[Mutation]:
    """Deal independent damage using specified power/type/element.

    Unlike the implicit skill damage (which uses the skill's own power/type),
    hit can specify different values. Element defaults to the skill's element.

    Passes all ctx-snapshot modifiers to calc_damage. Same-skill modifiers
    (power_mult, damage_mult, etc.) are applied by the engine's modifier
    collection step after VM execution.
    """
    if isinstance(effect, dict):
        power = resolve(ctx, effect.get("power", 0))
        type_ = effect.get("type", "")
        element = effect.get("element")
    elif isinstance(effect, HitOp):
        power = resolve(ctx, effect.power)
        type_ = effect.type
        element = effect.element
    else:
        power = resolve(ctx, getattr(effect, "power", 0))
        type_ = getattr(effect, "type", "")
        element = getattr(effect, "element", None)
    if element is None:
        element = ctx.element_self

    if type_ == "物攻":
        atk_base = ctx.atk_self
        def_base = ctx.def_opp
        atk_stage = _stage_mult(ctx.stat_stages_self.get("atk", 0))
        def_stage = _stage_mult(ctx.stat_stages_opp.get("def", 0))
    else:
        atk_base = ctx.sp_atk_self
        def_base = ctx.sp_def_opp
        atk_stage = _stage_mult(ctx.stat_stages_self.get("sp_atk", 0))
        def_stage = _stage_mult(ctx.stat_stages_opp.get("sp_def", 0))

    # 属性克制 / 本系加成 / 天气 也走实战路径（此前只有评估器口径算了这三项，
    # 引擎实际结算漏掉，等于实战没有克制关系）
    amount = calc_damage(
        power, atk_base, def_base,
        atk_stage=atk_stage,
        def_stage=def_stage,
        stab_mult=_stab_mult(element, ctx.elements_self),
        type_mult=_type_mult(element, ctx.elements_opp),
        weather_mult=_weather_mult(ctx.weather, element),
        damage_reduction=ctx.damage_reduction_opp,
        power_mult=ctx.power_mult_self,
        damage_mult=ctx.damage_mult_self,
        mark_bonus=ctx.mark_bonus_own,
        combo_count=ctx.combo_self,
    )

    return [Damage(
        target="sprite_opp",
        amount=amount,
        element=element,
        type=type_,
    )]
