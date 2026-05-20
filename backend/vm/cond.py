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
from .resolve import resolve


def compare_op(a, op: str, b) -> bool:
    """Generic comparison: lt / le / eq / ge / gt."""
    if op == "lt":
        return a < b
    if op == "le":
        return a <= b
    if op == "eq":
        return a == b
    if op == "ge":
        return a >= b
    if op == "gt":
        return a > b
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
    return True


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

    # ── Backward compat: raw dict ──
    if isinstance(cond, dict):
        key = cond["cond"]
        if key not in COND_EVAL:
            raise KeyError(f"Unknown condition: {key}")
        return COND_EVAL[key](ctx, cond)

    return False


def _eval_dict(ctx: Ctx, cond: dict) -> bool:
    """Backward compat alias: evaluate a raw dict condition."""
    return eval_one(ctx, cond)
