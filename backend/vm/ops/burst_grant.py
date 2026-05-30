"""burst_grant opcode — grant burst effects to matching skills."""

from ..ctx import Ctx
from ..journal import BurstGrant, Mutation


def op_burst_grant(ctx: Ctx, op) -> list[Mutation]:
    target = getattr(op, "target", "sprite_self")
    skill_where = getattr(op, "skill_where", None)
    skill_filter = getattr(op, "skill_filter", None)
    then_effects = tuple(getattr(op, "then", ()))
    source = getattr(op, "source", "") or ""

    return [BurstGrant(
        target=target,
        skill_where=skill_where,
        skill_filter=skill_filter,
        effects=then_effects,
        source=source,
    )]
