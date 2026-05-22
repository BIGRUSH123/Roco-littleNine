"""schedule opcode — register delayed effects for a future turn.

V2: Typed Schedule op.
"""

from ..ctx import Ctx
from ..journal import Mutation, ScheduleEntry


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_schedule(ctx: Ctx, effect) -> list[Mutation]:
    delay_turns = _get(effect, "delay_turns", 1)
    phase = _get(effect, "phase", "start")
    effects = _get(effect, "effects", ())
    return [ScheduleEntry(
        delay_turns=int(delay_turns),
        phase=phase,
        effects=list(effects),
    )]
