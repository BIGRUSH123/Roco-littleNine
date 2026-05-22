"""dispel opcode — remove effects from a target.

V2: Supports typed DispelOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Dispel, Mutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_dispel(ctx: Ctx, effect) -> list[Mutation]:
    """Remove positive/negative/abnormal/mark effects from a target.

    what: "positive" | "negative" | "abnormal" | "mark"
    name: optional, specific mark/abnormal name (all if omitted)
    limit: optional, max total stacks to remove (randomly distributed)
    type_limit: optional, max types to remove (each fully cleared)
    """
    target = _get(effect, "target")
    what = _get(effect, "what")
    return [Dispel(
        target=target,
        what=what,
        name=_get(effect, "name"),
        limit=_get(effect, "limit"),
        type_limit=_get(effect, "type_limit"),
        source=_get(effect, "source"),
    )]
