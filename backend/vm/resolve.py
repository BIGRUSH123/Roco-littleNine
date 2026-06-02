"""Value resolution — literal pass-through or query expression against Ctx.

resolve(ctx, value) -> int | float | str
    Literals returned as-is. Query dicts resolved via ADDRESS_MAP -> getattr().
    Typed IRValue (Literal, Query, RefExpr) also supported for V2 type dispatch.

QueryRef is a pre-indexed query for O(1) runtime lookup (used by SkillLoader).
"""

import re
from dataclasses import dataclass

from .ctx import ADDRESS_MAP, Ctx
from .ir_values import Literal, Query, RefExpr

# Dict-type register queries that require a 'name' key for sub-indexing
_NAMED_DICT_QUERIES = frozenset({
    "counter_value", "abnormal_stacks", "devotion", "mark_stacks", "skill_count", "team_counter",
})


@dataclass
class QueryRef:
    """Pre-indexed query for O(1) runtime lookup.

    Built at load time by SkillLoader.pre_index_queries(). At runtime,
    field access is a single getattr() — no map lookup.
    """
    field: str              # Ctx attribute name (from ADDRESS_MAP)
    name: str | None = None # sub-key for dict registers
    scale: float = 1.0
    offset: int = 0
    per: float | None = None
    default: int | float | str | None = None
    # If set, this is a dict-type query and name is the sub-key
    is_dict_query: bool = False
    # Which sub-key field to use: "name" | "skill_type" | "element" | "tag"
    sub_key_field: str = "name"


def resolve(ctx: Ctx, value) -> int | float | str:
    """Resolve a value against the Ctx snapshot.

    Handles three formats:
    1. Typed IRValue (Literal, Query, RefExpr) — V2 type dispatch
    2. Raw dict queries ({"q": ..., "of": ...}) — backward compat
    3. Primitive literals (int, float, str, bool) — pass through

    Transform chain for Query/RefExpr (applied in order):
        1. per  — int(raw / per)
        2. scale — raw * scale
        3. offset — raw + offset

    Dict registers (abnormal_stacks, devotion, etc.) use the 'name' field
    for sub-indexing. energy_cost_sum uses 'skill_type'/'element'/'tag'.
    """
    # ── Typed IRValue dispatch (V2) ──
    if isinstance(value, Literal):
        v = value.value
        if isinstance(v, dict):
            return _resolve_dict_query(ctx, v)
        if isinstance(v, str) and v.startswith("="):
            return _resolve_formula_string(ctx, v)
        return v
    if isinstance(value, Query):
        raw = getattr(ctx, value.field, value.default)
        if raw is None:
            raw = value.default or 0
        # Dict-type registers: sub-index by name (e.g. skill_count_own["虫鸣"])
        if isinstance(raw, dict):
            raw = raw.get(value.name, 0) if value.name else 0
        # Derived query: mark_count_both = own + opp
        if value.sub_key_field == "mark_count_both":
            raw = (raw if isinstance(raw, (int, float)) else 0) + ctx.mark_count_opp
        result = raw
        if value.pre_scale != 1.0 or value.pre_offset != 0.0:
            result = result * value.pre_scale + value.pre_offset
        if value.per is not None and value.per != 0:
            result = int(result / value.per) if isinstance(result, (int, float)) else result
        if isinstance(result, (int, float)):
            result = result * value.scale + value.offset
        elif isinstance(result, dict):
            return 0
        return result
    if isinstance(value, RefExpr):
        return _resolve_ref(ctx, value)

    # ── Primitive literal pass-through ──
    # bool before int since bool is subclass of int
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if value.startswith("="):
            return _resolve_formula_string(ctx, value)
        return value

    # ── Raw dict query (backward compat) ──
    if isinstance(value, dict):
        return _resolve_dict_query(ctx, value)

    return value


def _resolve_dict_query(ctx: Ctx, value: dict) -> int | float | str:
    """Resolve a raw dict query ({"q": ..., "of": ...}) against Ctx."""
    q = value.get("q")
    if q is None:
        raise KeyError(f"Query dict missing 'q' key: {value}")

    of = value.get("of", "sprite_self")

    # Derived queries — computed from other fields, not direct registers
    if q == "hp_missing_ratio":
        ratio_field = "hp_self_ratio" if of == "sprite_self" else "hp_opp_ratio"
        ratio = getattr(ctx, ratio_field, 1.0)
        return _apply_transforms(1.0 - ratio, value)
    if q == "mark_count_both":
        return _apply_transforms(ctx.mark_count_own + ctx.mark_count_opp, value)
    if q == "is_fainted":
        val = ctx.event.self_koed if of == "sprite_self" else ctx.event.target_fainted
        return val

    # ADDRESS_MAP lookup
    map_key = (of, q)
    if map_key not in ADDRESS_MAP:
        raise KeyError(f"Unknown query (of={of}, q={q}) — not in ADDRESS_MAP")
    field_name = ADDRESS_MAP[map_key]
    raw = getattr(ctx, field_name)

    # Dict-type registers: sub-index by name or type/element/tag
    if q in _NAMED_DICT_QUERIES:
        sub_key = value.get("name")
        if isinstance(raw, dict):
            raw = raw.get(sub_key, 0) if sub_key else 0
        else:
            raw = 0
    elif q == "energy_cost_sum":
        sub_key = value.get("skill_type") or value.get("element") or value.get("tag")
        if isinstance(raw, dict):
            raw = raw.get(sub_key, 0) if sub_key else 0
        else:
            raw = 0

    # Default fallback (when raw is falsy: 0, "", None, empty)
    if "default" in value and not raw:
        raw = value["default"]

    # String / bool values — return without numeric transform
    if isinstance(raw, (str, bool)):
        return raw

    # Numeric transforms: per -> scale -> offset
    if "per" in value:
        per = value["per"]
        raw = int(raw / per) if per != 0 else raw

    if "scale" in value:
        raw = raw * value["scale"]

    if "offset" in value:
        raw = raw + value["offset"]

    if isinstance(raw, dict):
        return 0
    return raw


def _apply_transforms(raw, value: dict) -> int | float | str:
    """Apply per/scale/offset transforms from a dict query to a raw value."""
    if isinstance(raw, (str, bool)):
        return raw

    if "per" in value:
        per = value["per"]
        raw = int(raw / per) if per != 0 else raw

    if "scale" in value:
        raw = raw * value["scale"]

    if "offset" in value:
        raw = raw + value["offset"]

    return raw


def _get_ctx_field(ctx: Ctx, field_name: str, default=0):
    """Get a field from Ctx, falling back to ctx.event for event fields."""
    try:
        return getattr(ctx, field_name)
    except AttributeError:
        return getattr(ctx.event, field_name, default)


def _resolve_ref(ctx: Ctx, ref: RefExpr) -> int | float | str:
    """Resolve a RefExpr by walking the path from root through ctx."""
    root_obj = getattr(ctx, ref.root, None)
    if root_obj is None:
        return ref.offset  # default to offset when root not found

    current = root_obj
    for key in ref.path:
        if current is None:
            return ref.offset
        current = current.get(key) if isinstance(current, dict) else getattr(current, key, None)

    if current is None:
        return ref.offset

    if isinstance(current, (int, float)):
        return float(current) * ref.multiplier + ref.offset

    return current


# ── Formula string evaluation (=@ prefix) ──

# Cache for pre-compiled formula expressions to avoid eval() re-parsing overhead
_COMPILED_FORMULAS: dict[str, object] = {}


# Lightweight trait path → Ctx field map (mirrors cond._TRAIT_PATH_MAP,
# duplicated here to avoid circular import)
_FORMULA_PATH_MAP: dict[str, str] = {
    "self.energy": "energy_self",
    "self.hp": "hp_self",
    "self.hp_ratio": "hp_self_ratio",
    "self.max_hp": "hp_self_max",
    "self.is_charging": "is_charging_self",
    "self.first_action": "first_action_self",
    "self.first_action_battle": "first_action_battle_self",
    "self.charged": "charged_self",
    "self.positive_count": "positive_count_self",
    "self.abnormal_count": "abnormal_count_self",
    "self.fainted": "self_koed",
    "self.just_entered": "just_entered",
    "self.damage_reduction": "damage_reduction_self",
    "self.energy_cost_total": "skills_energy_sum_self",
    "self.energy_cost": "energy_cost_self",
    "self.speed": "speed_self",
    "self.atk": "atk_self",
    "self.def": "def_self",
    "self.sp_atk": "sp_atk_self",
    "self.sp_def": "sp_def_self",
    "target.energy": "energy_opp",
    "target.hp": "hp_opp",
    "target.hp_ratio": "hp_opp_ratio",
    "target.max_hp": "hp_opp_max",
    "target.positive_count": "positive_count_opp",
    "target.abnormal_count": "abnormal_count_opp",
    "self.skill_element_count": "skill_element_count_self",
    "target.skill_element_count": "skill_element_count_opp",
    "target.energy_cost": "energy_cost_opp",
    "target.energy_cost_total": "skills_energy_sum_opp",
    "target.speed": "speed_opp",
    "target.atk": "atk_opp",
    "target.def": "def_opp",
    "target.sp_atk": "sp_atk_opp",
    "target.sp_def": "sp_def_opp",
    "skill.power": "power_self",
    "skill.element": "element_self",
    "skill.energy_cost": "energy_cost_self",
    "skill.combo": "combo_self",
    "opponent_skill.power": "power_opp",
    "player_fainted_count": "fainted_own",
    "opponent_fainted_count": "fainted_opp",
    "use.is_first": "is_first",
    "first_action": "first_action_self",
    "first_action_battle": "first_action_battle_self",
    "delta": "energy_delta_self",
    "battle.globals.weather": "weather",
    "opponent.lives": "lives_opp",
    "effect_name": "abnormal_applied_name",
    "player_moe_stacks": "moe_team_stacks",
    "positive_changed_stat": "positive_changed_stat",
    "positive_changed_steps": "positive_changed_steps",
}


# Pre-compiled regex patterns for _resolve_trait_ref (hot path)
_RE_TRAIT_EFFECTS = re.compile(r'(self|target)\.effects\[name=([^\]]+)\]\.(\w+)')
_RE_TRAIT_COUNTERS = re.compile(r'(self|target)\.counters\[([^\]]+)\]')
_RE_TRAIT_TEAM_COUNTERS = re.compile(r'(?:player\.|opponent\.)?team_counters\[([^\]]+)\]')
_RE_TRAIT_SKILLS = re.compile(r'(self|target)\.skills\[element=([^\]]+)\]\.count')


def _resolve_trait_ref(ref: str, ctx: Ctx):
    """Resolve a single @path reference against Ctx.

    Handles: direct field maps, effects[name=X].stacks/exists,
    counters[key], team_counters[key], skills[filter].count.
    """
    path = ref
    if path.startswith("@"):
        path = path[1:]

    # Direct field map
    if path in _FORMULA_PATH_MAP:
        field = _FORMULA_PATH_MAP[path]
        return _get_ctx_field(ctx, field)

    # effects[name=X].stacks / effects[name=X].exists
    m = _RE_TRAIT_EFFECTS.match(path)
    if m:
        target, name, prop = m.group(1), m.group(2), m.group(3)
        stacks = ctx.abnormal_stacks_self if target == "self" else ctx.abnormal_stacks_opp
        val = stacks.get(name, 0)
        return val > 0 if prop == "exists" else val

    # counters[key]
    m = _RE_TRAIT_COUNTERS.match(path)
    if m:
        return ctx.counter_values.get(m.group(2), 0)

    # team_counters[key]
    m = _RE_TRAIT_TEAM_COUNTERS.match(path)
    if m:
        key = m.group(1)
        if path.startswith("opponent."):
            return ctx.team_counters_opp.get(key, 0)
        return ctx.team_counters_own.get(key, 0)

    # skills[element=X].count
    m = _RE_TRAIT_SKILLS.match(path)
    if m:
        target, element = m.group(1), m.group(2)
        counts = ctx.skill_element_counts_self if target == "self" else ctx.skill_element_counts_opp
        return counts.get(element, 0)

    return 0


# Pre-compiled ref-replacement pattern (module-level to avoid re.compile per call)
_REF_PATTERN = re.compile(
    r'@[a-zA-Z_]\w*(?:\[[^\]]*\])?(?:\.[a-zA-Z_]\w*(?:\[[^\]]*\])?)*'
)


def _resolve_formula_string(ctx: Ctx, formula: str) -> int | float:
    """Evaluate a =@ formula string against Ctx.

    Formats:
      =@path.field        → single reference
      =@path.a - @path.b  → arithmetic expression
      =literal            → literal numeric value
    """
    expr = formula[1:]  # strip '='

    # Arithmetic expression: replace @refs with resolved values, then eval
    if _REF_PATTERN.search(expr) and re.search(r'[\+\-\*\/\(\)]', expr):
        def replace_ref(m):
            ref_expr = m.group(0)
            val = _resolve_trait_ref(ref_expr, ctx)
            return str(val) if val is not None else '0'

        resolved = _REF_PATTERN.sub(replace_ref, expr)

        # Cache compiled code objects to avoid eval() re-parsing overhead.
        # Skill formulas are a small finite set — cache grows minimally.
        if resolved not in _COMPILED_FORMULAS:
            _COMPILED_FORMULAS[resolved] = compile(
                resolved, '<formula>', 'eval',
            )
        code = _COMPILED_FORMULAS[resolved]

        try:
            return eval(code, {"__builtins__": {}}, {
                "int": int, "float": float, "round": round,
                "max": max, "min": min, "abs": abs,
            })
        except Exception:
            return 0

    # Single reference
    return _resolve_trait_ref(expr, ctx)
