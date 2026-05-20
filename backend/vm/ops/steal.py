"""steal opcode — steal effects/energy from a target to self.

V2: Supports typed StealOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Mutation, Steal


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_steal(ctx: Ctx, effect) -> list[Mutation]:
    """Steal effects or energy from a target.

    what: "positive" | "mark" | "energy"
    name: optional, specific mark name (all if omitted)
    amount: for energy steal, max amount to steal
    """
    target = _get(effect, "target")
    what = _get(effect, "what")
    return [Steal(
        from_target=target,
        what=what,
        name=_get(effect, "name"),
        amount=_get(effect, "amount"),
    )]
