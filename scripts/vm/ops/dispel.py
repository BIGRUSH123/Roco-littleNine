"""dispel opcode — remove effects from a target."""

from ..ctx import Ctx
from ..journal import Dispel, Mutation


def op_dispel(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Remove positive/negative/abnormal/mark effects from a target.

    what: "positive" | "negative" | "abnormal" | "mark"
    name: optional, specific mark/abnormal name (all if omitted)
    limit: optional, max total stacks to remove (randomly distributed)
    type_limit: optional, max types to remove (each fully cleared)
    """
    target = effect["target"]
    what = effect["what"]
    return [Dispel(
        target=target,
        what=what,
        name=effect.get("name"),
        limit=effect.get("limit"),
        type_limit=effect.get("type_limit"),
    )]
