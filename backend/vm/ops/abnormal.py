"""abnormal opcode — apply abnormal status stacks to a sprite.

V2: Supports typed AbnormalOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..ir_skill import AbnormalOp
from ..journal import AbnormalChange, Mutation
from ..resolve import resolve


def op_abnormal(ctx: Ctx, effect) -> list[Mutation]:
    """Apply abnormal status stacks to a sprite.

    Uses 'stacks' for fixed stacks or 'value' (query) for dynamic stacks.
    """
    if isinstance(effect, dict):
        target = effect.get("target", "sprite_opp")
        name = effect.get("name")
        delta = effect["stacks"] if "stacks" in effect else resolve(ctx, effect.get("value", 1))
        scope = effect.get("scope", "battlefield")
        per_hit = effect.get("per_hit", False)
    elif isinstance(effect, AbnormalOp):
        target = effect.target
        name = effect.name
        delta = effect.stacks
        scope = effect.scope
        per_hit = getattr(effect, "per_hit", False)
    else:
        target = getattr(effect, "target", "sprite_opp")
        name = getattr(effect, "name", "")
        delta = getattr(effect, "stacks", 1)
        scope = getattr(effect, "scope", "battlefield")
        per_hit = getattr(effect, "per_hit", False)

    result = [AbnormalChange(target=target, name=name, delta=int(delta), scope=scope)]

    combo = max(1, ctx.combo_self)
    if per_hit and combo > 1 and result:
        result = result * combo

    return result
