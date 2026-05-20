"""mod opcode — the catch-all modifier.

Produces StatChange (steps-based stage mods), ModifierInjection (value-based
stat/skill mods), Heal (hp recovery), EnergyChange, or devotion registration.

V2: Supports typed ModOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import (
    Damage,
    EnergyChange,
    Heal,
    ModifierInjection,
    Mutation,
    StatChange,
)
from ..resolve import resolve

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


def _resolve_value(ctx: Ctx, effect) -> int | float:
    """Resolve the numeric value from steps, value literal, or value query."""
    # Support both dict and typed access
    if isinstance(effect, dict):
        if "steps" in effect:
            return resolve(ctx, effect["steps"])
        return resolve(ctx, effect.get("value", 0))
    # Typed ModOp: steps take priority (mirrors dict behavior)
    if hasattr(effect, 'steps') and effect.steps:
        return resolve(ctx, effect.steps)
    if effect.value is not None:
        return resolve(ctx, effect.value)
    return 0


def _calc_heal_amount(value: float, hp_max: int) -> int:
    """Convert a mod hp value to an absolute heal amount.

    Float 0 < val <= 1 → ratio of max HP; int/float > 1 → absolute.
    Negative values handled by caller (becomes damage).
    """
    if isinstance(value, float) and 0 < value <= 1:
        return max(1, round(value * hp_max))
    return max(1, round(value))


def _metadata(effect) -> dict:
    """Extract optional engine-level metadata common to StatChange and ModifierInjection."""
    meta = {}
    if isinstance(effect, dict):
        for key in ("name", "element", "per_element", "skill_filter", "skill_where",
                     "on_next", "if_type"):
            if key in effect:
                meta[key] = effect[key]
    else:
        # Typed ModOp
        for key in ("name", "element", "per_element", "skill_filter", "skill_where",
                     "if_type"):
            val = getattr(effect, key, None)
            if val is not None:
                meta[key] = val
    return meta


def _get_field(effect, key, default=None):
    """Unified field access: dict .get() or object attribute."""
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_mod(ctx: Ctx, effect) -> list[Mutation]:
    """Evaluate a 'mod' effect and produce zero or more Mutations.

    Supports both typed ModOp (V2) and backward-compat dict.

    Dispatch:
        steps + stage stat → StatChange
        value + stage stat  → ModifierInjection (direct multiplier)
        value + skill stat  → ModifierInjection
        value + flag stat   → ModifierInjection
        hp                  → Heal (or Damage if negative)
        energy              → EnergyChange
        devotion            → ModifierInjection (with then block)
    """
    target = _get_field(effect, "target", "sprite_self")
    stat = _get_field(effect, "stat")
    mode = _get_field(effect, "mode", "set")
    scope = _get_field(effect, "scope", "battlefield")
    meta = _metadata(effect)

    is_steps = False
    if isinstance(effect, dict):
        is_steps = "steps" in effect
    else:
        is_steps = effect.steps > 0 if hasattr(effect, 'steps') else False

    raw = _resolve_value(ctx, effect)
    value = float(raw)
    result: list[Mutation] = []

    # ── Steps-based stage changes ──
    if is_steps:
        result = [StatChange(
            target=target,
            stat=stat,
            steps=int(raw),
            scope=scope,
            **meta,
        )]

    # ── HP healing / damage ──
    elif stat == "hp":
        hp_max_field = _HP_MAX_MAP.get(target, "hp_self_max")
        hp_max = getattr(ctx, hp_max_field, 100)
        if value >= 0:
            amount = _calc_heal_amount(raw, hp_max)
            if amount > 0:
                result = [Heal(target=target, amount=amount)]
        else:
            amount = abs(round(value))
            if amount > 0:
                result = [Damage(
                    target=target, amount=amount,
                    element=ctx.element_self, type=ctx.skill_type_self,
                )]

    # ── Energy change ──
    elif stat == "energy":
        delta = int(value) if mode == "set" else int(value)
        if delta != 0:
            result = [EnergyChange(target=target, delta=delta)]

    # ── Devotion ──
    elif stat == "devotion":
        result = [ModifierInjection(
            target=target, stat=stat, value=value,
            scope=scope, mode=mode,
            name=_get_field(effect, "name"),
            then=_get_field(effect, "then"),
        )]

    # ── Stage / skill mod / flag stats ──
    elif stat in _STAT_STAGES or stat in _SKILL_MOD_STATS or stat in _FLAG_STATS:
        result = [ModifierInjection(
            target=target, stat=stat, value=value,
            scope=scope, mode=mode, **meta,
        )]

    # Unknown stat — default to ModifierInjection
    else:
        result = [ModifierInjection(
            target=target, stat=stat, value=value,
            scope=scope, mode=mode, **meta,
        )]

    # ── per_hit: repeat mutations for each combo hit ──
    per_hit = _get_field(effect, 'per_hit', False)
    combo = max(1, ctx.combo_self)
    if per_hit and combo > 1 and result:
        result = result * combo

    return result
