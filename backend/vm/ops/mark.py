"""mark opcode — add or remove mark stacks on a team.

V2: Supports typed MarkOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import MarkChange, Mutation
from ..resolve import resolve


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
    target = _get(effect, "target", "sprite_self")
    name = _get(effect, "name")

    if isinstance(effect, dict):
        if "stacks" in effect:
            delta = effect["stacks"]
        else:
            delta = resolve(ctx, effect.get("value", 1))
    else:
        delta = resolve(ctx, effect.value) if effect.value is not None else effect.stacks

    # Normalize: 5 target values in skill data → own/opp
    team = "own" if target in ("team_own", "own_team", "sprite_self") else "opp"

    result = [MarkChange(target_team=team, name=name, delta=int(delta))]

    per_hit = _get(effect, "per_hit", False)
    combo = max(1, ctx.combo_self)
    if per_hit and combo > 1 and result:
        result = result * combo

    return result
