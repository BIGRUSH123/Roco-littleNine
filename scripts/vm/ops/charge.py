"""charge opcode — enter charging state."""

from ..ctx import Ctx
from ..journal import Charge, Mutation


def op_charge(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Put self into charging state for next-turn release."""
    target = effect.get("target", "sprite_self")
    return [Charge(target=target)]
