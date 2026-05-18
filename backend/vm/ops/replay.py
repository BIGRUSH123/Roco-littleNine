"""replay opcode — replay previously used skills or bursts."""

from ..ctx import Ctx
from ..journal import Replay, Mutation


def op_replay(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Replay skills from self's history or team's burst history.

    from: "sprite_self" (own skill history) | "team_burst" (team burst history)
    skill_filter: optional dict with tag/skill_type/element to filter history
    what: "burst" (when from="team_burst")
    """
    from_ = effect["from"]
    return [Replay(
        from_=from_,
        skill_filter=effect.get("skill_filter"),
    )]
