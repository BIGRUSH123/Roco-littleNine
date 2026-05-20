"""double opcode — double the stacks/steps of effects on a target.

V2: Supports typed DoubleOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Double, Mutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_double(ctx: Ctx, effect) -> list[Mutation]:
    """Double effect stacks/steps on a target.

    what: "positive" | "negative" | "abnormal" | "mark"
    name: optional, specific abnormal/mark name to double (all if omitted)
    """
    target = _get(effect, "target", "sprite_self")
    what = _get(effect, "what")
    name = _get(effect, "name")
    return [Double(target=target, what=what, name=name)]
