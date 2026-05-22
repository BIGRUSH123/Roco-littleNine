"""lives_change opcode — modify player lives (魔力).

V2: Typed LivesChange op.
"""

from ..ctx import Ctx
from ..journal import LivesDelta, Mutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_lives_change(ctx: Ctx, effect) -> list[Mutation]:
    target_team = _get(effect, "target_team", "own")
    delta = _get(effect, "delta", 1)
    return [LivesDelta(target_team=target_team, delta=int(delta))]
