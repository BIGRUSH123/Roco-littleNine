"""hit opcode — independent damage hit separate from the skill's implicit attack."""

from ..ctx import Ctx
from ..resolve import resolve
from ..damage import calc_damage
from ..journal import Damage, Mutation

# Stage steps to multiplier: each step = ±0.1
def _stage_mult(steps: int) -> float:
    return steps * 0.1


def op_hit(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Deal independent damage using specified power/type/element.

    Unlike the implicit skill damage (which uses the skill's own power/type),
    hit can specify different values. Element defaults to the skill's element.

    Passes all ctx-snapshot modifiers to calc_damage. Same-skill modifiers
    (power_mult, damage_mult, etc.) are applied by the engine's modifier
    collection step after VM execution.
    """
    power = resolve(ctx, effect["power"])
    type_ = effect["type"]
    element = effect.get("element")
    if element is None:
        element = ctx.element_self

    # Determine atk/def based on damage type
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
        combo_count=ctx.combo_self,
        counter_power_mult=1.5 if ctx.counter_succeeded else 1.0,
    )

    return [Damage(
        target="sprite_opp",
        amount=amount,
        element=element,
        type=type_,
    )]
