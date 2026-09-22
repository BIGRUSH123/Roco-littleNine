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
    elif isinstance(effect, AbnormalOp):
        target = effect.target
        name = effect.name
        # value（查询式动态层数）优先于固定 stacks：typed 路径此前只读 stacks，
        # 于是 dict 被编译成 AbnormalOp 后动态层数被丢弃（扩散侵蚀「×2」、蚀刻扣中毒）
        delta = resolve(ctx, effect.value) if effect.value is not None else effect.stacks
        scope = effect.scope
    else:
        target = getattr(effect, "target", "sprite_opp")
        name = getattr(effect, "name", "")
        delta = getattr(effect, "stacks", 1)
        scope = getattr(effect, "scope", "battlefield")

    result = [AbnormalChange(target=target, name=name, delta=int(delta), scope=scope)]

    combo = max(1, ctx.combo_self)
    if combo > 1 and result:
        result = result * combo

    return result
