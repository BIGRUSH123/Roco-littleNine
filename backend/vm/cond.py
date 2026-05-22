"""Condition evaluation — COND_EVAL dispatch table.

Each condition is a pure function (ctx, cond) -> bool. 'and'/'or'/'not' are
recursive combinators. Adding a new condition = adding one row to the table.

All conditions share the same signature regardless of where they're called
(when block, count watcher, and/or/not compound).

V2: Supports typed SkillCondition (CondExpr, AndCond, OrCond, NotCond) via
match/case in eval_one, plus backward-compat dict path.
"""

from .ctx import Ctx
from .ir_skill import (
    AndCond,
    CondExpr,
    NotCond,
    OrCond,
)
from .ir_trait import FnCond
from .resolve import resolve


# ── Function condition registry ──

_FN_COND_REGISTRY: dict[str, callable] = {}


def register_fn_cond(name: str, fn: callable) -> None:
    """Register a function condition for FnCond evaluation."""
    _FN_COND_REGISTRY[name] = fn


def _builtin_is_weekend(_ctx: Ctx) -> bool:
    import datetime
    return datetime.date.today().weekday() >= 5


register_fn_cond('is_weekend', _builtin_is_weekend)


def compare_op(a, op: str, b) -> bool:
    """Generic comparison with trait-system alias support."""
    # Standard comparison
    if op == "lt":
        return a < b
    if op in ("le", "lte"):
        return a <= b
    if op == "eq":
        return a == b
    if op in ("ne", "neq"):
        return a != b
    if op in ("ge", "gte"):
        return a >= b
    if op == "gt":
        return a > b
    # Collection / string membership
    if op == "contains":
        if hasattr(a, "__contains__"):
            return b in a
        return str(b) in str(a)
    if op == "in":
        if hasattr(b, "__contains__"):
            return a in b
        return str(a) in str(b)
    if op == "not_in":
        if hasattr(b, "__contains__"):
            return a not in b
        return str(a) not in str(b)
    return False


# ── Helpers ──

def _sprite_of(ctx: Ctx, of: str):
    """Return (energy, abnormal_stacks, stat_stages, hp_ratio, prev_damage_taken,
    positive_count, skill_elements, just_entered, is_charging, charged) for a sprite target."""
    if of == "sprite_self":
        return {
            "energy": ctx.energy_self,
            "abnormal_stacks": ctx.abnormal_stacks_self,
            "stat_stages": ctx.stat_stages_self,
            "hp_ratio": ctx.hp_self_ratio,
            "prev_damage_taken": ctx.prev_damage_taken_self,
            "positive_count": ctx.positive_count_self,
            "skill_elements": ctx.skill_elements_self,
            "just_entered": ctx.just_entered,
            "is_charging": ctx.is_charging_self,
            "charged": ctx.charged_self,
        }
    else:
        return {
            "energy": ctx.energy_opp,
            "abnormal_stacks": ctx.abnormal_stacks_opp,
            "stat_stages": ctx.stat_stages_opp,
            "hp_ratio": ctx.hp_opp_ratio,
            "prev_damage_taken": ctx.prev_damage_taken_opp,
            "positive_count": ctx.positive_count_opp,
            "skill_elements": ctx.skill_elements_opp,
            "just_entered": False,
            "is_charging": False,
            "charged": ctx.charged_opp,
        }


def _team_of(ctx: Ctx, of: str):
    """Return (mark_count, mark_stacks) for a team target."""
    if of == "team_own":
        return {
            "mark_count": ctx.mark_count_own,
            "mark_stacks": ctx.mark_stacks_own,
            "fainted": ctx.fainted_own,
        }
    else:
        return {
            "mark_count": ctx.mark_count_opp,
            "mark_stacks": ctx.mark_stacks_opp,
            "fainted": ctx.fainted_opp,
        }


def _skill_use_matches(ctx: Ctx, cond: dict) -> bool:
    """Check if the current skill matches filter conditions.

    Used by the 'skill_use' condition (count-only). When no filter is
    specified, always matches (the skill being used is always 'this skill').
    """
    # element filter
    if "element" in cond:
        element = resolve(ctx, cond["element"]) if isinstance(cond["element"], dict) else cond["element"]
        if ctx.element_self != element:
            return False
    # skill_type filter
    if "skill_type" in cond:
        if ctx.skill_type_self != cond["skill_type"]:
            return False
    # tag filter
    if "tag" in cond:
        if ctx.skill_tag_self != cond["tag"]:
            return False
    # energy_cost filter
    if "energy_cost" in cond:
        if ctx.energy_cost_self != cond["energy_cost"]:
            return False
    return True


# ── Condition → Trigger point inference ──
# Maps each condition type to the trigger point(s) where it should be
# evaluated. Used to auto-derive Observer.listen from condition trees.

CONDITION_TRIGGERS: dict[str, frozenset[str]] = {
    # Skill pipeline
    "skill_use":               frozenset({"post_skill"}),
    "counter_succeeded":       frozenset({"post_counter"}),
    "self_was_countered":      frozenset({"post_skill"}),
    "prev_counter_succeeded":  frozenset({"post_counter"}),
    "charged":                 frozenset({"post_skill", "turn_end"}),
    "is_charging":             frozenset({"post_skill", "turn_end"}),
    "burst":                   frozenset({"post_skill"}),
    "first_action":            frozenset({"post_skill"}),
    "opp_is_attack":           frozenset({"post_skill"}),
    "prev_skill_is":           frozenset({"post_skill"}),
    "is_first":                frozenset({"post_skill"}),
    "is_second":               frozenset({"post_skill"}),
    "skill_at":                frozenset({"post_skill"}),
    "skill_position_changed":  frozenset({"post_skill"}),
    "energy_depleted":         frozenset({"post_skill", "post_energy_change"}),
    # KO / Damage
    "on_ko":                   frozenset({"post_ko"}),
    "on_self_ko":              frozenset({"post_ko"}),
    "on_damage_taken":         frozenset({"post_damage"}),
    "prev_damage_taken":       frozenset({"post_damage"}),
    # HP / Energy thresholds
    "hp_below":                frozenset({"post_damage", "post_entry", "post_heal"}),
    "energy_le":               frozenset({"post_skill", "post_energy_change", "post_entry"}),
    "energy_eq":               frozenset({"post_skill", "post_energy_change"}),
    # Weather
    "weather_is":              frozenset({"post_skill", "post_entry", "turn_end"}),
    # Switch
    "opp_switched":            frozenset({"post_switch"}),
    "self_switched":           frozenset({"post_switch"}),
    # Entry
    "sprite_entered":          frozenset({"post_entry"}),
    "have_skill_of":           frozenset({"post_entry", "post_skill"}),
    # Abnormal / state change events
    "on_abnormal_tick":        frozenset({"post_abnormal_tick"}),
    "on_abnormal_changed":     frozenset({"post_abnormal_change"}),
    "on_abnormal_applied":     frozenset({"post_abnormal_apply"}),
    "on_skills_energy_changed": frozenset({"post_energy_change"}),
    "on_positive_changed":     frozenset({"post_positive_change"}),
    "on_energy_changed":       frozenset({"post_energy_change"}),
    # Turn boundaries
    "turn_end":                frozenset({"turn_end"}),
    # Devotion
    "devotion_triggered":      frozenset({"post_skill"}),
    # Have sub-dispatch
    "have":                    frozenset({"post_skill", "post_entry",
                                          "post_abnormal_change", "post_positive_change"}),
}


def infer_triggers(cond) -> frozenset[str]:
    """Walk a condition tree and return the set of trigger points it should listen on.

    For compound conditions:
      - or:  union (any sub-condition could independently match → check at all)
      - and: union (check combined condition at any trigger a sub-condition cares
               about; the other sub-conditions are evaluated as state checks)
      - not: triggers of inner condition (negation doesn't change when to check)

    Returns empty frozenset for unknown/unmappable conditions (these fire on
    ALL triggers as a fallback).
    """
    # Typed SkillCondition
    if isinstance(cond, CondExpr):
        return CONDITION_TRIGGERS.get(cond.cond, frozenset())

    if isinstance(cond, AndCond):
        result = frozenset()
        for c in cond.conditions:
            result = result | infer_triggers(c)
        return result

    if isinstance(cond, OrCond):
        result = frozenset()
        for c in cond.conditions:
            result = result | infer_triggers(c)
        return result

    if isinstance(cond, NotCond):
        return infer_triggers(cond.condition)

    # FnCond — unknown, fire on all triggers as fallback
    if isinstance(cond, FnCond):
        return frozenset()

    # Raw dict (backward compat)
    if isinstance(cond, dict):
        key = cond.get("cond", "")
        if key in ("and", "or"):
            result = frozenset()
            for c in cond.get("conditions", []):
                result = result | infer_triggers(c)
            return result
        if key in ("not",):
            return infer_triggers(cond.get("condition", {}))
        return CONDITION_TRIGGERS.get(key, frozenset())

    return frozenset()


# ── HAVE_EVAL sub-dispatch ──

HAVE_EVAL = {
    "abnormal": lambda ctx, cond: (
        _sprite_of(ctx, cond["of"])["abnormal_stacks"].get(cond.get("name", ""), 0) > 0
    ),
    "mark": lambda ctx, cond: (
        _team_of(ctx, cond.get("of", "team_own"))["mark_stacks"].get(cond.get("name", ""), 0) > 0
    ),
    "stat_positive": lambda ctx, cond: (
        _sprite_of(ctx, cond["of"])["stat_stages"].get(cond.get("stat", ""), 0) > 0
    ),
    "stat_negative": lambda ctx, cond: (
        _sprite_of(ctx, cond["of"])["stat_stages"].get(cond.get("stat", ""), 0) < 0
    ),
    "any_stat_positive": lambda ctx, cond: any(
        v > 0 for v in _sprite_of(ctx, cond["of"])["stat_stages"].values()
    ),
    "any_stat_negative": lambda ctx, cond: any(
        v < 0 for v in _sprite_of(ctx, cond["of"])["stat_stages"].values()
    ),
    "counter": lambda ctx, cond: (
        ctx.counter_values.get(cond.get("name", ""), 0) > 0
    ),
}


# ── COND_EVAL dispatch table ──
# Handlers receive (ctx, params) where params is a dict of the condition's
# parameters (without the "cond" key). When called from the dict path,
# params is the full dict (which includes "cond" — but that's harmless).
#
# V2: when called from CondExpr path, params = cond.params (clean params only).

COND_EVAL = {
    # ── Counter / response ──
    "counter_succeeded": lambda ctx, cond: ctx.counter_succeeded,
    "self_was_countered": lambda ctx, cond: ctx.was_countered,
    "prev_counter_succeeded": lambda ctx, cond: ctx.prev_counter_succeeded,

    # ── Charge / action state ──
    "charged": lambda ctx, cond: ctx.charged_self,
    "is_charging": lambda ctx, cond: ctx.is_charging_self,
    "burst": lambda ctx, cond: ctx.first_action_self,
    "first_action": lambda ctx, cond: ctx.first_action_self,

    # ── KO ──
    "on_ko": lambda ctx, cond: ctx.target_fainted,
    "on_self_ko": lambda ctx, cond: ctx.self_koed,

    # ── Damage ──
    "on_damage_taken": lambda ctx, cond: ctx.damage_taken_this_turn > 0,
    "prev_damage_taken": lambda ctx, cond: (
        _sprite_of(ctx, cond.get("of", "sprite_self"))["prev_damage_taken"]
    ),

    # ── Switch ──
    "opp_switched": lambda ctx, cond: ctx.opp_switched,
    "self_switched": lambda ctx, cond: ctx.self_switched,

    # ── Skill type checks ──
    "opp_is_attack": lambda ctx, cond: ctx.skill_type_opp in ("物攻", "魔攻", "动态攻击"),
    "prev_skill_is": lambda ctx, cond: (
        ctx.prev_skill_type in ("物攻", "魔攻", "动态攻击")
        if cond.get("what") == "attack"
        else ctx.prev_skill_type == cond.get("skill_type")
    ),

    # ── Turn order ──
    "is_first": lambda ctx, cond: ctx.is_first,
    "is_second": lambda ctx, cond: not ctx.is_first,

    # ── HP / Energy threshold ──
    "hp_below": lambda ctx, cond: (
        _sprite_of(ctx, cond.get("of", "sprite_self"))["hp_ratio"] < cond["ratio"]
    ),
    "energy_le": lambda ctx, cond: (
        _sprite_of(ctx, cond.get("of", "sprite_self"))["energy"] <= cond["value"]
    ),
    "energy_eq": lambda ctx, cond: (
        _sprite_of(ctx, cond.get("of", "sprite_self"))["energy"] == cond["value"]
    ),
    "energy_depleted": lambda ctx, cond: (
        _sprite_of(ctx, cond.get("of", "sprite_self"))["energy"] == ctx.energy_cost_self
    ),

    # ── Weather ──
    "weather_is": lambda ctx, cond: (
        ctx.weather == resolve(ctx, cond["weather"])
        if isinstance(cond["weather"], dict)
        else ctx.weather == cond["weather"]
    ),

    # ── Skill position ──
    "skill_at": lambda ctx, cond: ctx.skill_index == cond["position"],
    "skill_position_changed": lambda ctx, cond: ctx.skill_position_changed,

    # ── Skill use (count only) ──
    "skill_use": lambda ctx, cond: _skill_use_matches(ctx, cond),

    # ── Skill element possession ──
    "have_skill_of": lambda ctx, cond: (
        resolve(ctx, cond["element"]) in _sprite_of(ctx, cond["of"])["skill_elements"]
        if isinstance(cond.get("element"), dict)
        else cond["element"] in _sprite_of(ctx, cond["of"])["skill_elements"]
    ),

    # ── Entry / abnormal / state change events ──
    "sprite_entered": lambda ctx, cond: _sprite_of(ctx, cond.get("of", "sprite_self"))["just_entered"],
    "on_abnormal_tick": lambda ctx, cond: (
        ctx.last_tick_abnormal == cond["name"]
        and ctx.last_tick_target == cond.get("of", "sprite_opp")
    ),
    "on_abnormal_changed": lambda ctx, cond: (
        ctx.abnormal_changed_name == cond["name"]
        and ctx.abnormal_changed_target == cond.get("of", "sprite_opp")
    ),
    "on_abnormal_applied": lambda ctx, cond: (
        ctx.abnormal_applied_name == cond["name"]
        and ctx.abnormal_applied_target == cond.get("of", "sprite_opp")
    ),
    "on_skills_energy_changed": lambda ctx, cond: (
        ctx.skills_energy_changed_of == cond.get("of", "sprite_self")
    ),
    "on_positive_changed": lambda ctx, cond: (
        ctx.positive_changed_of == cond.get("of", "sprite_opp")
    ),
    "on_energy_changed": lambda ctx, cond: (
        ctx.energy_changed_of == cond.get("of", "sprite_self")
    ),

    # ── Turn end ──
    "turn_end": lambda ctx, cond: ctx.turn_end,

    # ── Generic comparison ──
    "compare": lambda ctx, cond: compare_op(
        resolve(ctx, cond), cond["op"], resolve(ctx, cond["value"])
        if isinstance(cond["value"], dict) else cond["value"]
    ),

    # ── Devotion ──
    "devotion_triggered": lambda ctx, cond: ctx.devotion_triggered,

    # ── Logic gates — recursive combinators ──
    "and": lambda ctx, cond: all(eval_one(ctx, c) for c in cond["conditions"]),
    "or": lambda ctx, cond: any(eval_one(ctx, c) for c in cond["conditions"]),
    "not": lambda ctx, cond: not eval_one(ctx, cond["condition"]),

    # ── have sub-dispatch ──
    "have": lambda ctx, cond: HAVE_EVAL[cond["what"]](ctx, cond),

    # ── Trait path bridge (Phase C3) ──
    "trait_path": lambda ctx, cond: _eval_trait_path(ctx, cond),
}


def eval_one(ctx: Ctx, cond) -> bool:
    """Evaluate a single condition against Ctx.

    Supports three input formats:
    1. Typed CondExpr — dispatches by cond.cond, passes cond.params to handler
    2. Typed AndCond/OrCond/NotCond — recursive evaluation
    3. Raw dict — backward compat (dispatches by cond["cond"])

    Logic gates (and/or/not) recurse through this function.
    """
    # ── V2: Typed SkillCondition ──
    if isinstance(cond, CondExpr):
        key = cond.cond
        if key not in COND_EVAL:
            raise KeyError(f"Unknown condition: {key}")
        return COND_EVAL[key](ctx, cond.params)

    if isinstance(cond, AndCond):
        return all(eval_one(ctx, c) for c in cond.conditions)

    if isinstance(cond, OrCond):
        return any(eval_one(ctx, c) for c in cond.conditions)

    if isinstance(cond, NotCond):
        return not eval_one(ctx, cond.condition)

    if isinstance(cond, FnCond):
        fn = _FN_COND_REGISTRY.get(cond.name)
        return fn(ctx) if fn else False

    # ── Backward compat: raw dict ──
    if isinstance(cond, dict):
        key = cond["cond"]
        if key not in COND_EVAL:
            raise KeyError(f"Unknown condition: {key}")
        return COND_EVAL[key](ctx, cond)

    return False


# ── Trait path bridge (Phase C3) ──

# Map common trait path roots to Ctx fields
_TRAIT_PATH_MAP: dict[str, str] = {
    # Self
    "self.energy": "energy_self",
    "self.energy_self": "energy_self",
    "self.hp": "hp_self",
    "self.hp_ratio": "hp_self_ratio",
    "self.hp_self_ratio": "hp_self_ratio",
    "self.max_hp": "hp_self_max",
    "self.is_charging": "is_charging_self",
    "self._charging": "is_charging_self",
    "self.first_action": "first_action_self",
    "first_action": "first_action_self",
    "self.charged": "charged_self",
    "self.positive_count": "positive_count_self",
    "self.abnormal_count": "abnormal_count_self",
    "self.fainted": "self_koed",
    "self.just_entered": "just_entered",
    "self.damage_reduction": "damage_reduction_self",
    # Target / opponent
    "target.energy": "energy_opp",
    "target.hp": "hp_opp",
    "target.hp_ratio": "hp_opp_ratio",
    "target.max_hp": "hp_opp_max",
    "target.positive_count": "positive_count_opp",
    "target.abnormal_count": "abnormal_count_opp",
    "target.fainted": "self_koed",
    # Skill
    "skill.power": "power_self",
    "skill.skill_type": "skill_type_self",
    "skill.element": "element_self",
    "skill.energy_cost": "energy_cost_self",
    "skill.combo": "combo_self",
    "opponent_skill.power": "power_opp",
    "use.combo": "combo_self",
    "use.is_first": "is_first",
    # Battle / weather
    "battle.globals.weather": "weather",
    # Team aggregates
    "player_fainted_count": "fainted_own",
    "opponent_fainted_count": "fainted_opp",
    # Event-specific (set by engine before trigger fire)
    "effect_name": "abnormal_applied_name",
    "effect.name": "abnormal_applied_name",
}


def _resolve_trait_path_value(ctx: Ctx, path: str):
    """Resolve a trait path expression to a value from Ctx.

    Handles: direct field maps, computed paths (skill.is_attack etc.),
    effects[name=X], counters[key], skills[filter].count, team_counters[key].
    """
    import re

    # Direct field map
    if path in _TRAIT_PATH_MAP:
        return getattr(ctx, _TRAIT_PATH_MAP[path])

    # Computed paths
    if path == "skill.is_attack":
        return ctx.skill_type_self in ("物攻", "魔攻", "动态攻击")
    if path == "skill.is_defense":
        return ctx.skill_type_self in ("防御",)
    if path == "skill.is_status":
        return ctx.skill_type_self in ("状态", "变化")
    if path == "target.is_fainted":
        return ctx.target_fainted
    if path == "is_faint":
        return ctx.self_koed
    if path == "self.energy_cost_total":
        return ctx.skills_energy_sum_self
    if path == "target_bloodline":
        return getattr(ctx, "bloodline_opp", "")
    if path == "skill":
        return getattr(ctx, "skill_name_self", "")
    if path == "type_mult":
        return getattr(ctx, "type_mult", 1.0)
    if path == "opponent.lives":
        return getattr(ctx, "lives_opp", 5)
    if path == "self._migration_cycle":
        return ctx.counter_values.get("_migration_cycle", 0)
    if path == "self._burst_extended_once":
        return ctx.counter_values.get("_burst_extended_once", False)
    if path == "team_elements":
        return list(ctx.skill_elements_self) if ctx.skill_elements_self else []
    if path == "effect.is_stat":
        return getattr(ctx, "effect_is_stat", False)

    # effects[name=X].exists / effects[name=X].stacks
    m = re.match(r'(self|target)\.effects\[name=([^\]]+)\]\.(\w+)', path)
    if m:
        target, name, prop = m.group(1), m.group(2), m.group(3)
        stacks = ctx.abnormal_stacks_self if target == "self" else ctx.abnormal_stacks_opp
        val = stacks.get(name, 0)
        if prop == "exists":
            return val > 0
        if prop == "stacks":
            return val
        return 0

    # counters[key]
    m = re.match(r'(self|target)\.counters\[([^\]]+)\]', path)
    if m:
        key = m.group(2)
        return ctx.counter_values.get(key, 0)

    # skills[element=X].count / skills[is_attack=True].count
    m = re.match(r'(self|target)\.skills\[([^\]]+)\]\.(\w+)', path)
    if m:
        target, filter_str, prop = m.group(1), m.group(2), m.group(3)
        elements = ctx.skill_elements_self if target == "self" else ctx.skill_elements_opp
        filters = {}
        for part in filter_str.split(','):
            fm = re.match(r'(\w+)=(.+)', part.strip())
            if fm:
                k, v = fm.group(1), fm.group(2)
                filters[k] = v
        if "element" in filters:
            count = 1 if filters["element"] in elements else 0
            if prop == "count":
                return count
        return 0

    # team_counters[key] / player.team_counters[key] / opponent.team_counters[key]
    m = re.match(r'(?:player\.|opponent\.)?team_counters\[([^\]]+)\]', path)
    if m:
        key = m.group(1)
        # Default to own team
        if path.startswith("opponent."):
            return ctx.team_counters_opp.get(key, 0)
        return ctx.team_counters_own.get(key, 0)

    # Fallback: try attribute access on ctx
    return getattr(ctx, path, None)


def _eval_trait_path(ctx: Ctx, cond: dict) -> bool:
    """Evaluate a trait-style path condition against Ctx.

    cond format: {"cond": "trait_path", "path": "...", "op": "...", "value": ...}
    """
    path = cond.get("path", "")
    op = cond.get("op", "eq")
    expected = cond.get("value")
    actual = _resolve_trait_path_value(ctx, path)
    return compare_op(actual, op, expected)


def _eval_dict(ctx: Ctx, cond: dict) -> bool:
    """Backward compat alias: evaluate a raw dict condition."""
    return eval_one(ctx, cond)
