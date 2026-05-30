"""effect_delta opcode — add delta to all matching effects on a target."""

from ..ctx import Ctx
from ..journal import EffectDelta, Mutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_effect_delta(ctx: Ctx, effect) -> list[Mutation]:
    target = _get(effect, "target", "sprite_opp")
    what = _get(effect, "what", "negative")
    delta = _get(effect, "delta", 1)
    return [EffectDelta(target=target, what=what, delta=delta)]
