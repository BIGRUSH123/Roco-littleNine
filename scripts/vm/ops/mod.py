"""mod opcode — the catch-all modifier.

Produces StatChange (steps-based stage mods), ModifierInjection (value-based
stat/skill mods), Heal (hp recovery), EnergyChange, or devotion registration.
"""

from ..ctx import Ctx
from ..resolve import resolve
from ..journal import (
    StatChange, ModifierInjection, Heal, Damage, EnergyChange,
    Mutation,
)

# Stats modified via stage steps (always produce StatChange when steps present)
_STAT_STAGES = frozenset({"atk", "def", "sp_atk", "sp_def", "speed"})

# Stats that produce ModifierInjection (skill-level or formula modifiers)
_SKILL_MOD_STATS = frozenset({
    "power", "energy_cost", "combo", "priority",
    "power_mult", "damage_mult", "damage_reduction", "life_drain",
    "energy_cost_mult", "combo_mult",
})

# Flag-type stats (boolean / sentinel values)
_FLAG_STATS = frozenset({
    "pre_charged", "ignore_mods", "ignore_resistance", "cooldown",
    "life_as_energy", "survive", "extra_action", "extra_turn_end",
    "heal_reverse", "immune", "drive",
})

# Stats with special handling
_SPECIAL_STATS = frozenset({"hp", "energy", "devotion"})

# Map target to ctx field for hp_max lookup
_HP_MAX_MAP = {
    "sprite_self": "hp_self_max",
    "sprite_opp": "hp_opp_max",
}


def _resolve_value(ctx: Ctx, effect: dict) -> int | float:
    """Resolve the numeric value from steps, value literal, or value query."""
    if "steps" in effect:
        raw = effect["steps"]
        return resolve(ctx, raw)
    raw = effect.get("value", 0)
    return resolve(ctx, raw)


def _calc_heal_amount(value: float, hp_max: int) -> int:
    """Convert a mod hp value to an absolute heal amount.

    Float 0 < val <= 1 → ratio of max HP; int/float > 1 → absolute.
    Negative values handled by caller (becomes damage).
    """
    if isinstance(value, float) and 0 < value <= 1:
        return max(1, round(value * hp_max))
    return max(1, round(value))


def _metadata(effect: dict) -> dict:
    """Extract optional engine-level metadata common to StatChange and ModifierInjection."""
    meta = {}
    for key in ("name", "element", "per_element", "skill_filter", "skill_where",
                 "on_next", "if_type"):
        if key in effect:
            meta[key] = effect[key]
    return meta


def op_mod(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Evaluate a 'mod' effect and produce zero or more Mutations.

    Dispatch:
        steps + stage stat → StatChange
        value + stage stat  → ModifierInjection (direct multiplier)
        value + skill stat  → ModifierInjection
        value + flag stat   → ModifierInjection
        hp                  → Heal (or Damage if negative)
        energy              → EnergyChange
        devotion            → ModifierInjection (with then block)
    """
    target = effect.get("target", "sprite_self")
    stat = effect["stat"]
    mode = effect.get("mode", "set")
    scope = effect.get("scope", "battlefield")
    meta = _metadata(effect)

    is_steps = "steps" in effect
    raw = _resolve_value(ctx, effect)
    value = float(raw)

    # ── Steps-based stage changes ──
    if is_steps:
        return [StatChange(
            target=target,
            stat=stat,
            steps=int(raw),
            scope=scope,
            **meta,
        )]

    # ── HP healing / damage ──
    if stat == "hp":
        hp_max_field = _HP_MAX_MAP.get(target, "hp_self_max")
        hp_max = getattr(ctx, hp_max_field, 100)
        if value >= 0:
            amount = _calc_heal_amount(raw, hp_max)  # use raw for float/int detection
            return [Heal(target=target, amount=amount)] if amount > 0 else []
        else:
            amount = abs(round(value))
            return [Damage(
                target=target,
                amount=amount,
                element=ctx.element_self,
                type=ctx.skill_type_self,
            )] if amount > 0 else []

    # ── Energy change ──
    if stat == "energy":
        delta = int(value) if mode == "set" else int(value)
        return [EnergyChange(target=target, delta=delta)] if delta != 0 else []

    # ── Devotion ──
    if stat == "devotion":
        return [ModifierInjection(
            target=target,
            stat=stat,
            value=value,
            scope=scope,
            mode=mode,
            name=effect.get("name"),
            then=effect.get("then"),
        )]

    # ── Stage stat with value (multiplier mode) ──
    if stat in _STAT_STAGES:
        return [ModifierInjection(
            target=target,
            stat=stat,
            value=value,
            scope=scope,
            mode=mode,
            **meta,
        )]

    # ── Skill mod stats ──
    if stat in _SKILL_MOD_STATS:
        return [ModifierInjection(
            target=target,
            stat=stat,
            value=value,
            scope=scope,
            mode=mode,
            **meta,
        )]

    # ── Flag stats ──
    if stat in _FLAG_STATS:
        return [ModifierInjection(
            target=target,
            stat=stat,
            value=value,
            scope=scope,
            mode=mode,
            **meta,
        )]

    # Unknown stat — default to ModifierInjection
    return [ModifierInjection(
        target=target,
        stat=stat,
        value=value,
        scope=scope,
        mode=mode,
        **meta,
    )]
