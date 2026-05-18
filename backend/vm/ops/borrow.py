"""borrow opcode — copy opponent's current skill properties.

V2: Supports typed BorrowOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Borrow, Mutation
from ..ir_skill import BorrowOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_borrow(ctx: Ctx, effect) -> list[Mutation]:
    """Borrow the opponent's current skill properties (power, type, effects).

    from_skill: "skill_opp_current"
    """
    from_skill = _get(effect, "from", "skill_opp_current")
    return [Borrow(from_skill=from_skill)]
