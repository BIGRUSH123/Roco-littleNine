"""escape opcode — switch out self or force opponent out."""

from ..ctx import Ctx
from ..journal import Escape, Mutation


def op_escape(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Remove a sprite from the field (voluntary or forced switch).

    target: "sprite_self" (voluntary) | "sprite_opp" (forced)
    inherit: if True, incoming sprite inherits positive effects
    urgent: if True, escape happens before damage resolution
    then: effects to execute after escape
    """
    target = effect.get("target", "sprite_self")
    return [Escape(
        target=target,
        inherit=effect.get("inherit", False),
        urgent=effect.get("urgent", False),
        then=effect.get("then"),
    )]
