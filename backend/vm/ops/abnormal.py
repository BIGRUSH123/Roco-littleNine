"""abnormal opcode — apply abnormal status stacks to a sprite."""

from ..ctx import Ctx
from ..resolve import resolve
from ..journal import AbnormalChange, Mutation


def op_abnormal(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Apply abnormal status stacks to a sprite.

    Uses 'stacks' for fixed stacks or 'value' (query) for dynamic stacks.
    """
    target = effect.get("target", "sprite_opp")
    name = effect["name"]

    if "stacks" in effect:
        delta = effect["stacks"]
    else:
        delta = resolve(ctx, effect.get("value", 1))

    return [AbnormalChange(target=target, name=name, delta=int(delta))]
