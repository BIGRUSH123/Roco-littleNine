"""double opcode — double the stacks/steps of effects on a target."""

from ..ctx import Ctx
from ..journal import Double, Mutation


def op_double(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Double effect stacks/steps on a target.

    what: "positive" | "negative" | "abnormal" | "mark"
    name: optional, specific abnormal/mark name to double (all if omitted)
    """
    target = effect.get("target", "sprite_self")
    what = effect["what"]
    name = effect.get("name")
    return [Double(target=target, what=what, name=name)]
