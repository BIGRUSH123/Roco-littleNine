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

Within each phase, effects are sorted by priority (descending — higher
priority executes first). Ties preserve original relative order.

V2: Supports typed SkillIROp alongside backward-compat dict.
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


def _get(effect, key, default=None):
    """Unified field access: dict .get() or object attribute."""
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def _phase_of(effect) -> int:
    """Return the execution phase index for an effect."""
    feeds = _get(effect, "feeds")
    if feeds and feeds in _PHASE:
        return _PHASE[feeds]
    needs = _get(effect, "needs")
    if needs and needs in _PHASE:
        return _PHASE[needs]
    return _DEFAULT_PHASE


def sort_effects(effects) -> list:
    """Sort effects by execution phase while preserving relative order.

    Effects are bucketed into phases (cost, power, mult, default, counter,
    turn_end) and then concatenated. Within each phase, effects with higher
    priority execute first; ties preserve original order.

    Supports both typed SkillIROp and dict effects.
    """
    if not effects:
        return []

    # Tag each effect with (original_index, phase, -priority)
    tagged = [
        (i, _phase_of(eff), -(_get(eff, "priority") or 0), eff)
        for i, eff in enumerate(effects)
    ]

    # Sort by (phase, -priority, original_index)
    tagged.sort(key=lambda x: (x[1], x[2], x[0]))

    return [eff for _, _, _, eff in tagged]
