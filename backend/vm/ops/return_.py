"""return opcode — leave and re-enter the field.

V2: Supports typed ReturnOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Return, Mutation
from ..ir_skill import ReturnOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_return(ctx: Ctx, effect) -> list[Mutation]:
    """Exit the battlefield and immediately re-enter.

    Distinct from escape: same sprite leaves and comes back (not a different one).
    """
    target = _get(effect, "target", "sprite_self")
    return [Return(target=target)]
