"""interrupt opcode — cancel the opponent's current skill execution."""

from ..ctx import Ctx
from ..journal import Interrupt, Mutation


def op_interrupt(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Immediately stop the opponent's remaining effect execution."""
    target = effect.get("target", "sprite_opp")
    return [Interrupt(target=target)]
