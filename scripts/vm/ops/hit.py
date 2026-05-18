"""hit opcode — independent damage hit separate from the skill's implicit attack."""

from ..ctx import Ctx
from ..resolve import resolve
from ..damage import calc_damage
from ..journal import Damage, Mutation


def op_hit(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Deal independent damage using specified power/type/element.

    Unlike the implicit skill damage (which uses the skill's own power/type),
    hit can specify different values. Element defaults to the skill's element.
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
    else:
        atk_base = ctx.sp_atk_self
        def_base = ctx.sp_def_opp

    amount = calc_damage(
        power, atk_base, def_base,
        damage_reduction=ctx.damage_reduction_opp,
    )

    return [Damage(
        target="sprite_opp",
        amount=amount,
        element=element,
        type=type_,
    )]
