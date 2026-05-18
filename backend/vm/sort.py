"""Topological sort for skill effects based on feeds/needs declarations.

Pipeline phases (in execution order):
    0. cost     — feeds:cost     (modify energy cost before Gate)
    1. power    — feeds:power    (modify power before power determination)
    2. mult     — feeds:mult     (modify damage multipliers before formula)
    3. default  — undeclared or needs:result (after damage, before counter)
    4. counter  — needs:counter  (wait for counter phase)
    5. turn_end — needs:turn_end (wait for turn-end settlement)

Effects without feeds/needs go to the default phase and keep their
original relative order.
"""

# Phase index for each feeds/needs token
_PHASE = {
    "cost": 0,
    "power": 1,
    "mult": 2,
    "result": 3,    # needs:result = default phase
    "counter": 4,
    "turn_end": 5,
}

_DEFAULT_PHASE = 3


def _phase_of(effect: dict) -> int:
    """Return the execution phase index for an effect."""
    feeds = effect.get("feeds")
    if feeds and feeds in _PHASE:
        return _PHASE[feeds]
    needs = effect.get("needs")
    if needs and needs in _PHASE:
        return _PHASE[needs]
    return _DEFAULT_PHASE


def sort_effects(effects: list[dict]) -> list[dict]:
    """Sort effects by execution phase while preserving relative order.

    Effects are bucketed into phases (cost, power, mult, default, counter,
    turn_end) and then concatenated. Within each phase, original order
    is preserved (stable sort).
    """
    if not effects:
        return []

    # Tag each effect with its original index and phase
    tagged = [(i, _phase_of(eff), eff) for i, eff in enumerate(effects)]

    # Sort by (phase, original_index) — stable within each phase
    tagged.sort(key=lambda x: (x[1], x[0]))

    return [eff for _, _, eff in tagged]
