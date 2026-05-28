"""Shared damage formula — pure function, matches prototype exactly.

Called by the VM during the 'mult' phase (after all ModifierInjections are
collected) and by the explicit 'hit' opcode. The engine computes all input
values from collected modifiers; this function is pure math.
"""


def calc_damage(
    power: int,
    atk_base: int,
    def_base: int,
    *,
    atk_stage: float = 0.0,
    def_stage: float = 0.0,
    stab_mult: float = 1.0,
    type_mult: float = 1.0,
    weather_mult: float = 1.0,
    damage_reduction: float = 0.0,
    power_mult: float = 1.0,
    counter_power_mult: float = 1.0,
    additive_power: int = 0,
    damage_mult: float = 1.0,
    combo_count: int = 1,
    mark_bonus: float = 0.0,
) -> int:
    """Compute final damage amount.

    Formula (matches prototype resolver.calc_damage):
        power_term = round((power * counter_power_mult + additive_power) * power_mult)
        core = 37/41 * atk_base / def_base * power_term
        core *= stab * type * weather * (1 - damage_reduction)
        core *= (1 + atk_stage - def_stage + mark_bonus)
        core *= combo_count * damage_mult
        damage = max(1, round(core))

    Args:
        power: Skill base power (after any base power modifications).
        atk_base: Attacker's raw base attack/SpAtk stat.
        def_base: Defender's raw base defense/SpDef stat.
        atk_stage: Attacker stat stage as fraction (e.g., 0.3 = +3 steps).
        def_stage: Defender stat stage as fraction (e.g., -0.2 = -2 steps).
        stab_mult: Same-type attack bonus (default 1.0, typically 1.25).
        type_mult: Type effectiveness multiplier (0.5/1.0/2.0 etc.).
        weather_mult: Weather damage multiplier.
        damage_reduction: Fraction of damage reduced (0.0 to 1.0).
        power_mult: Power multiplier from effects.
        counter_power_mult: Counter success power multiplier.
        additive_power: Flat power bonus.
        damage_mult: Final damage multiplier.
        combo_count: Number of hits (combo).
        mark_bonus: Mark damage bonus fraction (mark_mult - 1.0).
    """
    if atk_base <= 0 or def_base <= 0:
        return 0
    if combo_count < 1:
        combo_count = 1

    power_term = round((power * counter_power_mult + additive_power) * power_mult)
    if power_term <= 0:
        return 0

    core = (37.0 / 41.0) * atk_base / def_base * power_term
    core *= stab_mult * type_mult * weather_mult * (1.0 - damage_reduction)
    core *= (1.0 + atk_stage - def_stage + mark_bonus)
    core *= combo_count * damage_mult

    if damage_reduction >= 1.0:
        return 0
    return max(1, round(core))
