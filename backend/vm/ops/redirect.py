"""redirect opcode — redirect damage target.

V2: Supports typed RedirectOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Redirect, Mutation
from ..ir_skill import RedirectOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_redirect(ctx: Ctx, effect) -> list[Mutation]:
    """Redirect this skill's damage to a different target."""
    target = _get(effect, "target", "sprite_self")
    return [Redirect(target=target)]
