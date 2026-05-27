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
    turns = _get(effect, "turns", None)
    if turns is None:
        turns = int(_get(effect, "delay_turns", 1))
    else:
        turns = int(turns)

    at = _get(effect, "at", None) or _get(effect, "phase", None) or "start"
    # normalize: turn_end → end, turn_start → start (engine uses short form)
    if at == "turn_end":
        at = "end"
    elif at == "turn_start":
        at = "start"

    then = _get(effect, "then", None) or _get(effect, "effects", ())
    return [ScheduleEntry(
        turns=turns,
        at=at,
        then=list(then),
    )]
