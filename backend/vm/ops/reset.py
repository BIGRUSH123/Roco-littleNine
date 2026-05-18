"""reset opcode — reset a stat to its base value (undo permanent increments).

V2: Supports typed ResetOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Reset, Mutation
from ..ir_skill import ResetOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_reset(ctx: Ctx, effect) -> list[Mutation]:
    """Reset a stat to its original value, removing all permanent modifiers."""
    target = _get(effect, "target", "skill_off_0")
    stat = _get(effect, "stat")
    return [Reset(target=target, stat=stat)]
