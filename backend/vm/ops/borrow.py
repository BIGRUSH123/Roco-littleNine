"""borrow opcode — copy opponent's current skill properties.

V2: Supports typed BorrowOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Borrow, Mutation



def op_borrow(ctx: Ctx, effect) -> list[Mutation]:
    """Borrow the opponent's current skill properties (power, type, effects).

    from_skill: "skill_opp_current"
    """
    if isinstance(effect, dict):
        from_skill = effect.get("from", "skill_opp_current")
    else:
        from_skill = getattr(effect, "from_", "skill_opp_current")
    return [Borrow(from_skill=from_skill)]
