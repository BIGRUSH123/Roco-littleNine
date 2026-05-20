"""abnormal opcode — apply abnormal status stacks to a sprite.

V2: Supports typed AbnormalOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import AbnormalChange, Mutation
from ..resolve import resolve


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_abnormal(ctx: Ctx, effect) -> list[Mutation]:
    """Apply abnormal status stacks to a sprite.

    Uses 'stacks' for fixed stacks or 'value' (query) for dynamic stacks.
    """
    target = _get(effect, "target", "sprite_opp")
    name = _get(effect, "name")

    if isinstance(effect, dict):
        if "stacks" in effect:
            delta = effect["stacks"]
        else:
            delta = resolve(ctx, effect.get("value", 1))
    else:
        delta = resolve(ctx, effect.value) if effect.value is not None else effect.stacks

    return [AbnormalChange(target=target, name=name, delta=int(delta))]
