"""mark opcode — add, dispel, steal, or convert marks on a team.

V2: Supports typed MarkOp with action dispatch.
"""

from ..ctx import Ctx
from ..journal import MarkChange, Mutation
from ..resolve import resolve


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_mark(ctx: Ctx, effect) -> list[Mutation]:
    """Apply/dispel/steal/convert mark stacks on a team.

    action="apply": add stacks (default)
    action="dispel": remove stacks from target_team
    action="steal": remove from target_team, add to own
    action="convert": convert abnormal stacks on self → marks on target_team

    Uses 'stacks' for fixed stacks or 'value' (query) for dynamic stacks.
    """
    target = _get(effect, "target", "sprite_self")
    name = _get(effect, "name")
    action = _get(effect, "action", "apply")

    if isinstance(effect, dict):
        delta = effect.get("stacks", resolve(ctx, effect.get("value", 1)))
    else:
        delta = resolve(ctx, effect.value) if effect.value is not None else effect.stacks

    # Normalize: 5 target values → own/opp
    team = "own" if target in ("team_own", "own_team", "sprite_self") else "opp"

    result = [MarkChange(
        target_team=team,
        name=name,
        delta=int(delta),
        action=action,
        ratio=_get(effect, "ratio", 1.0),
        source_abnormal=_get(effect, "source_abnormal", None),
    )]

    per_hit = _get(effect, "per_hit", False)
    combo = max(1, ctx.combo_self)
    if per_hit and combo > 1 and result:
        result = result * combo

    return result
