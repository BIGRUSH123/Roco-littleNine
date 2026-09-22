"""burst_grant opcode — grant burst effects to matching skills."""

from ..ctx import Ctx
from ..journal import BurstGrant, Mutation


def op_burst_grant(ctx: Ctx, op) -> list[Mutation]:
    target = getattr(op, "target", "sprite_self")
    skill_where = getattr(op, "skill_where", None)
    skill_filter = getattr(op, "skill_filter", None)
    then_effects = tuple(getattr(op, "then", ()))
    source = getattr(op, "source", "") or ""
    # from:"triggered"（踏雷）：忽略 then，从本队「已触发过的迸发」池取回；count 选条数
    from_ = getattr(op, "from_", "explicit") or "explicit"
    count = getattr(op, "count", 1)

    return [BurstGrant(
        target=target,
        skill_where=skill_where,
        skill_filter=skill_filter,
        effects=then_effects,
        source=source,
        from_=from_,
        count=count,
    )]
