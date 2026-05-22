"""team_counter_write opcode — write to team-level counters.

V2: Typed TeamCounterWrite op.
"""

from ..ctx import Ctx
from ..journal import Mutation, TeamCounterDelta


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_team_counter_write(ctx: Ctx, effect) -> list[Mutation]:
    target = _get(effect, "target", "own")
    key = _get(effect, "key", "")
    delta = _get(effect, "delta", 1)
    return [TeamCounterDelta(target=target, key=key, delta=int(delta))]
