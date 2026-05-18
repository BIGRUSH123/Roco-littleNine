"""charge opcode — enter charging state.

V2: Supports typed ChargeOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Charge, Mutation
from ..ir_skill import ChargeOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_charge(ctx: Ctx, effect) -> list[Mutation]:
    """Put self into charging state for next-turn release."""
    target = _get(effect, "target", "sprite_self")
    return [Charge(target=target)]
