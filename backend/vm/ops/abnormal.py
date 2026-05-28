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
        delta = effect["stacks"] if "stacks" in effect else resolve(ctx, effect.get("value", 1))
    else:
        delta = resolve(ctx, effect.value) if effect.value is not None else effect.stacks

    scope = _get(effect, "scope", "battlefield")
    result = [AbnormalChange(target=target, name=name, delta=int(delta), scope=scope)]

    per_hit = _get(effect, "per_hit", False)
    combo = max(1, ctx.combo_self)
    if per_hit and combo > 1 and result:
        result = result * combo

    return result
