"""lock opcode — prevent target from switching."""

from ..ctx import Ctx
from ..journal import Lock, Mutation


def op_lock(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Lock a sprite from switching/escaping for N turns."""
    target = effect.get("target", "sprite_opp")
    turns = effect.get("turns", 1)
    return [Lock(target=target, turns=turns)]
