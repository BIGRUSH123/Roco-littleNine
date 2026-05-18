"""exchange opcode — swap something between self and opponent."""

from ..ctx import Ctx
from ..journal import Exchange, Mutation


def op_exchange(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Exchange HP ratios, effects, or skill positions.

    what: "hp_ratio" | "effects" | "skills" | "adjacent_skills"
    """
    what = effect["what"]
    target = effect.get("target", "sprite_opp")
    return [Exchange(target=target, what=what)]
