"""mark opcode — add or remove mark stacks on a team."""

from ..ctx import Ctx
from ..resolve import resolve
from ..journal import MarkChange, Mutation


def op_mark(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Apply mark stacks to a team.

    Uses 'stacks' for fixed stacks or 'value' (query) for dynamic stacks.
    Optional 'then' block is passed through for engine-side handling when
    mark application succeeds.
    """
    target = effect.get("target", "team_own")
    name = effect["name"]

    if "stacks" in effect:
        delta = effect["stacks"]
    else:
        delta = resolve(ctx, effect.get("value", 1))

    team = "own" if target == "team_own" else "opp"

    return [MarkChange(target_team=team, name=name, delta=int(delta))]
