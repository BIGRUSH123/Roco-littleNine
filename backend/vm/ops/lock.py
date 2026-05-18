"""lock opcode — prevent target from switching.

V2: Supports typed LockOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Lock, Mutation
from ..ir_skill import LockOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_lock(ctx: Ctx, effect) -> list[Mutation]:
    """Lock a sprite from switching/escaping for N turns."""
    target = _get(effect, "target", "sprite_opp")
    turns = _get(effect, "turns", 1)
    return [Lock(target=target, turns=turns)]
