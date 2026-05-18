"""borrow opcode — copy opponent's current skill properties."""

from ..ctx import Ctx
from ..journal import Borrow, Mutation


def op_borrow(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Borrow the opponent's current skill properties (power, type, effects).

    from_skill: "skill_opp_current"
    """
    from_skill = effect.get("from", "skill_opp_current")
    return [Borrow(from_skill=from_skill)]
