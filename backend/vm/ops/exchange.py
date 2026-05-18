"""exchange opcode — swap something between self and opponent.

V2: Supports typed ExchangeOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Exchange, Mutation
from ..ir_skill import ExchangeOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_exchange(ctx: Ctx, effect) -> list[Mutation]:
    """Exchange HP ratios, effects, or skill positions.

    what: "hp_ratio" | "effects" | "skills" | "adjacent_skills"
    """
    what = _get(effect, "what")
    target = _get(effect, "target", "sprite_opp")
    return [Exchange(target=target, what=what)]
