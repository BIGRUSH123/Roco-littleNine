"""replay opcode — replay previously used skills or bursts.

V2: Supports typed ReplayOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Replay, Mutation
from ..ir_skill import ReplayOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_replay(ctx: Ctx, effect) -> list[Mutation]:
    """Replay skills from self's history or team's burst history.

    from: "sprite_self" (own skill history) | "team_burst" (team burst history)
    skill_filter: optional dict with tag/skill_type/element to filter history
    what: "burst" (when from="team_burst")
    """
    from_ = _get(effect, "from")
    return [Replay(
        from_=from_,
        skill_filter=_get(effect, "skill_filter"),
    )]
