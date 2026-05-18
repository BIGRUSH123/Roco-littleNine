"""return opcode — leave and re-enter the field."""

from ..ctx import Ctx
from ..journal import Return, Mutation


def op_return(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Exit the battlefield and immediately re-enter.

    Distinct from escape: same sprite leaves and comes back (not a different one).
    """
    target = effect.get("target", "sprite_self")
    return [Return(target=target)]
