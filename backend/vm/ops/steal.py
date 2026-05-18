"""steal opcode — steal effects/energy from a target to self."""

from ..ctx import Ctx
from ..journal import Steal, Mutation


def op_steal(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Steal effects or energy from a target.

    what: "positive" | "mark" | "energy"
    name: optional, specific mark name (all if omitted)
    amount: for energy steal, max amount to steal
    """
    target = effect["target"]
    what = effect["what"]
    return [Steal(
        from_target=target,
        what=what,
        name=effect.get("name"),
        amount=effect.get("amount"),
    )]
