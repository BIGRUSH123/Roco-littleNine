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

    amount = calc_damage(
        power, atk_base, def_base,
        atk_stage=atk_stage,
        def_stage=def_stage,
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
