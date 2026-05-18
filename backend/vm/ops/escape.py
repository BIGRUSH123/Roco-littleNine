"""escape opcode — switch out self or force opponent out.

V2: Supports typed EscapeOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Escape, Mutation
from ..ir_skill import EscapeOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_escape(ctx: Ctx, effect) -> list[Mutation]:
    """Remove a sprite from the field (voluntary or forced switch).

    target: "sprite_self" (voluntary) | "sprite_opp" (forced)
    inherit: if True, incoming sprite inherits positive effects
    urgent: if True, escape happens before damage resolution
    then: effects to execute after escape
    """
    target = _get(effect, "target", "sprite_self")
    return [Escape(
        target=target,
        inherit=_get(effect, "inherit", False),
        urgent=_get(effect, "urgent", False),
        then=_get(effect, "then"),
    )]
