"""reset opcode — reset a stat to its base value (undo permanent increments)."""

from ..ctx import Ctx
from ..journal import Reset, Mutation


def op_reset(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Reset a stat to its original value, removing all permanent modifiers."""
    target = effect.get("target", "skill_off_0")
    stat = effect["stat"]
    return [Reset(target=target, stat=stat)]
