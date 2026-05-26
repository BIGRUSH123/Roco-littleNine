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
    # Support both old (delay_turns/phase/effects) and new RISC (turns/at/then) field names
    turns = _get(effect, "turns", None) or _get(effect, "delay_turns", 1)
    at = _get(effect, "at", None) or _get(effect, "phase", "start")
    then = _get(effect, "then", None) or _get(effect, "effects", ())
    return [ScheduleEntry(
        turns=int(turns),
        at=at,
        then=list(then),
    )]
