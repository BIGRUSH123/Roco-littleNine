"""mark opcode — add or remove mark stacks on a team.

V2: Supports typed MarkOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..resolve import resolve
from ..journal import MarkChange, Mutation
from ..ir_skill import MarkOp


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_mark(ctx: Ctx, effect) -> list[Mutation]:
    """Apply mark stacks to a team.

    Uses 'stacks' for fixed stacks or 'value' (query) for dynamic stacks.
    Optional 'then' block is passed through for engine-side handling when
    mark application succeeds.
    """
    target = _get(effect, "target", "team_own")
    name = _get(effect, "name")

    if isinstance(effect, dict):
        if "stacks" in effect:
            delta = effect["stacks"]
        else:
            delta = resolve(ctx, effect.get("value", 1))
    else:
        delta = resolve(ctx, effect.value) if effect.value is not None else effect.stacks

    team = "own" if target == "team_own" else "opp"

    return [MarkChange(target_team=team, name=name, delta=int(delta))]
