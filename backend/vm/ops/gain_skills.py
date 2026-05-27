"""gain_skills opcode — grant temporary skills to a sprite from a skill pool."""

from ..ctx import Ctx
from ..journal import GainSkillsMutation, Mutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_gain_skills(ctx: Ctx, effect) -> list[Mutation]:
    """Grant temporary skills from learnset or global pool.

    Fields:
        count: int (default 1) — number of skills to grant
        exclude_carried: bool (default true) — skip already-equipped skills
        source: "learnset" | "global" (default "learnset")
        target: str (default "sprite_self")
    """
    return [GainSkillsMutation(
        count=_get(effect, "count", 1),
        exclude_carried=_get(effect, "exclude_carried", True),
        source=_get(effect, "source", "learnset"),
        target=_get(effect, "target", "sprite_self"),
    )]
