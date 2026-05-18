"""interrupt opcode — cancel the opponent's current skill execution.

V2: Supports typed InterruptOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Interrupt, Mutation
from ..ir_skill import InterruptOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_interrupt(ctx: Ctx, effect) -> list[Mutation]:
    """Immediately stop the opponent's remaining effect execution."""
    target = _get(effect, "target", "sprite_opp")
    return [Interrupt(target=target)]
