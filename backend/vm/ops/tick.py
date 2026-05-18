"""tick opcode — trigger abnormal tick damage/effect.

V2: Supports typed TickOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Tick, Mutation
from ..ir_skill import TickOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_tick(ctx: Ctx, effect) -> list[Mutation]:
    """Trigger a single tick of an abnormal status on a target."""
    target = _get(effect, "target", "sprite_opp")
    name = _get(effect, "name")
    return [Tick(target=target, abnormal_name=name)]
