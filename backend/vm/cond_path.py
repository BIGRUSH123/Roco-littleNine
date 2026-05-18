"""Path-based condition evaluator for trait conditions.

Extracted from backend.sim.traits.trait_engine.ConditionEvaluator.
Evaluates TraitCondition (PathCond, FnCond, AndCond, OrCond, NotCond)
against a context dict.

Usage:
    from backend.vm.cond_path import eval_path_cond
    result = eval_path_cond(condition, ctx_dict)
"""

from __future__ import annotations
import re
from typing import Any

from .ir_trait import TraitCondition, PathCond, FnCond, AndCond, OrCond, NotCond
from .ir_values import Literal, Query, RefExpr, IRValue


# ── Condition function registry (for FnCond) ──

_CONDITION_FNS: dict[str, callable] = {}


def register_condition_fn(name: str, fn):
    """Register a condition function for FnCond dispatch."""
    _CONDITION_FNS[name] = fn


def _cmp(op: str, actual, expected) -> bool:
    """Type-coercing comparison operator."""
    if actual is None:
        return False

    _OP_MAP: dict[str, str] = {
        '=': 'eq', '==': 'eq', '!=': 'neq', '<>': 'neq',
        '>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte',
    }
    op = _OP_MAP.get(op, op)

    if expected is not None and type(actual) is not type(expected):
        if isinstance(expected, bool) and not isinstance(actual, bool):
            actual = bool(actual)
        elif isinstance(expected, (int, float)) and isinstance(actual, str):
            try:
                actual = float(actual)
            except ValueError:
                return False
        elif isinstance(expected, str) and isinstance(actual, (int, float)):
            try:
                expected = float(expected) if isinstance(actual, float) else int(expected)
            except ValueError:
                return False

    if op == 'eq':
        return actual == expected
    if op == 'neq':
        return actual != expected
    if op == 'gt':
        return actual > expected
    if op == 'gte':
        return actual >= expected
    if op == 'lt':
        return actual < expected
    if op == 'lte':
        return actual <= expected
    if op == 'in':
        return actual in (expected if isinstance(expected, (list, tuple, set)) else [expected])
    if op == 'not_in':
        return actual not in (expected if isinstance(expected, (list, tuple, set)) else [expected])
    if op == 'contains':
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        return False

    return True


def _resolve_value(value: IRValue | Any, ctx: dict) -> Any:
    """Resolve an IRValue to a concrete Python value."""
    if isinstance(value, Literal):
        return value.value
    if isinstance(value, Query):
        root = ctx.get("self") or ctx.get("target")
        if root is not None:
            val = getattr(root, value.field, value.default)
            if val is None:
                val = value.default or 0
            return val
        return value.default if value.default is not None else 0
    if isinstance(value, RefExpr):
        root = ctx.get(value.root)
        if root is not None:
            current = root
            for key in value.path:
                if current is None:
                    return 0
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    current = getattr(current, key, None)
            if current is not None and isinstance(current, (int, float)):
                return float(current) * value.multiplier + value.offset
            return current if current is not None else 0
        return 0
    return value


# ── Main entry point ──


def eval_path_cond(cond: TraitCondition | dict | None, ctx: dict[str, Any]) -> bool:
    """Evaluate a TraitCondition against a context dict.

    Supports both typed TraitCondition and backward-compat raw dict.
    """
    if cond is None:
        return True

    # ── V2: Typed TraitCondition ──
    if isinstance(cond, PathCond):
        return _eval_path(cond, ctx)
    if isinstance(cond, FnCond):
        return _eval_fn(cond, ctx)
    if isinstance(cond, AndCond):
        return all(eval_path_cond(c, ctx) for c in cond.conditions)
    if isinstance(cond, OrCond):
        return any(eval_path_cond(c, ctx) for c in cond.conditions)
    if isinstance(cond, NotCond):
        return not eval_path_cond(cond.condition, ctx)

    # ── Backward compat: raw dict ──
    if isinstance(cond, dict):
        return _eval_dict_condition(cond, ctx)

    return False


# ── Typed evaluators ──


def _eval_path(cond: PathCond, ctx: dict) -> bool:
    """Evaluate a path-based condition: walk the path, compare result."""
    # Walk the path from ctx root
    obj = ctx
    for key in cond.path:
        if obj is None:
            return False
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            obj = getattr(obj, key, None)
        if obj is None:
            return False

    actual = obj
    expected = _resolve_value(cond.value, ctx)

    return _cmp(cond.op, actual, expected)


def _eval_fn(cond: FnCond, ctx: dict) -> bool:
    """Evaluate a function-based condition."""
    fn = _CONDITION_FNS.get(cond.name)
    if fn:
        return bool(fn(ctx))
    return True


# ── Raw dict evaluator (backward compat, mirrors ConditionEvaluator) ──

# Context keys expected by path-based conditions
_CONTEXT_KEYS: set[str] = {
    'self', 'skill', 'use', 'target', 'attacker',
    'victim', 'killer', 'enemy_old', 'enemy_new',
    'battle', 'team', 'is_faint', 'delta', 'new_energy',
    'damage', 'effect', 'effect_name',
    'player', 'opponent',
    'player_fainted_count', 'opponent_fainted_count',
    'player_unique_elements', 'opponent_unique_elements',
    'player_lives', 'opponent_lives',
    'team_elements',
}


def _eval_dict_condition(condition: dict, ctx: dict[str, Any]) -> bool:
    """Evaluate a raw dict condition (backward compat).

    Supports formats:
    - Path comparison: {"path": "self.energy", "op": "gt", "value": 0}
    - Boolean combinators: {"and": [...]}, {"or": [...]}, {"not": {...}}
    - Function: {"fn": "name"}
    """
    if not condition:
        return True

    kind = condition.get('kind', 'path')

    if kind == 'and':
        return all(
            _eval_dict_condition(c, ctx)
            for c in condition.get('conditions', [])
        )
    if kind == 'or':
        return any(
            _eval_dict_condition(c, ctx)
            for c in condition.get('conditions', [])
        )
    if kind == 'not':
        return not _eval_dict_condition(
            condition.get('condition', {}), ctx,
        )
    if kind == 'fn':
        fn_name = condition.get('name', '')
        fn = _CONDITION_FNS.get(fn_name)
        if fn:
            return bool(fn(ctx))
        return True

    # Path-based condition
    path: str = condition.get('path', '')
    op: str = condition.get('op', 'eq')
    expected = condition.get('value')

    if isinstance(expected, str) and expected.startswith('='):
        expected = _resolve_ref_string(expected, ctx)

    actual = _resolve_path(path, ctx)
    return _cmp(op, actual, expected)


def _resolve_path(path: str, ctx: dict[str, Any]):
    """Resolve a dotted path string against ctx.

    Supports: effects[...], counters[...], skills[filter].count,
              team_counters[...], and plain attribute chains.
    """
    # Handle effects[...] paths
    if '.effects[' in path:
        return _resolve_effects_path(path, ctx)

    # Handle counters[...] paths
    if '.counters[' in path:
        return _resolve_counters_path(path, ctx)

    # Handle skills[filter].count paths
    if '.skills[' in path:
        return _resolve_skills_path(path, ctx)

    # Handle team_counters[...] paths (including opponent./player. prefixes)
    if 'team_counters[' in path:
        opp_match = re.match(r'opponent\.team_counters\[([^\]]+)\]', path)
        if opp_match:
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            opp_team = 'B' if team == 'A' else 'A'
            key = opp_match.group(1).strip()
            return battle.get_team_counter(opp_team, key) if battle else 0

        ply_match = re.match(r'player\.team_counters\[([^\]]+)\]', path)
        if ply_match:
            battle = ctx.get('battle')
            player_obj = ctx.get('player')
            if battle and player_obj:
                return battle.get_team_counter(player_obj.team_letter, ply_match.group(1).strip())
            return 0

        # Plain team_counters[key]
        m = re.match(r'team_counters\[([^\]]+)\]', path)
        if m:
            key = m.group(1).strip()
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            if battle is None:
                return 0
            return battle.get_team_counter(team, key)

    # Plain attribute chain
    parts = path.split('.')
    obj = ctx.get(parts[0])
    if obj is None:
        return None

    for attr in parts[1:]:
        if obj is None:
            return None
        obj = _resolve_attr(obj, attr)
    return obj


def _resolve_attr(obj, attr: str):
    """Resolve object attribute, prefer getattr, fallback to dict/private attrs."""
    val = getattr(obj, attr, None)
    if val is not None:
        return val
    if isinstance(obj, dict):
        return obj.get(attr)
    if attr.startswith('_'):
        return getattr(obj, attr, None)
    return None


def _resolve_counters_path(path: str, ctx: dict[str, Any]):
    """Resolve sprite counters: <target>.counters[<key>]"""
    m = re.match(r'(\w+)\.counters\[([^\]]+)\]', path)
    if not m:
        return None
    target_key = m.group(1)
    counter_key = m.group(2).strip()
    sprite = ctx.get(target_key)
    if sprite is None:
        return None
    counters = getattr(sprite, 'counters', {})
    return counters.get(counter_key, 0)


def _resolve_skills_path(path: str, ctx: dict[str, Any]):
    """Resolve skill filter count: <target>.skills[<filter>].<property>"""
    m = re.match(r'(\w+)\.skills\[([^\]]+)\]\.(\w+)', path)
    if not m:
        return None
    target_key = m.group(1)
    filter_str = m.group(2)
    prop = m.group(3)
    sprite = ctx.get(target_key)
    if sprite is None:
        return None

    filters: list[tuple[str, str, str]] = []
    for part in filter_str.split(','):
        fm = re.match(r'(\w+)([<>=!]+)(.+)', part.strip())
        if fm:
            filters.append((fm.group(1), fm.group(2), fm.group(3)))

    skills = getattr(sprite, 'skills', [])
    matched = []
    for bs in skills:
        ok = True
        for fkey, fop, fval in filters:
            if fkey == 'element':
                ev = getattr(bs, 'element', '')
            elif fkey in ('is_attack', 'is_defense', 'is_status'):
                ev = getattr(bs.base, fkey, False) if hasattr(bs, 'base') else False
            elif fkey == 'name':
                ev = getattr(bs, 'name', '')
            elif fkey == 'energy_cost':
                ev = getattr(bs, 'energy_cost', 0)
            else:
                ev = getattr(bs, fkey, None)
            if not _cmp(fop, ev, fval):
                ok = False
                break
        if ok:
            matched.append(bs)

    if prop == 'count':
        return len(matched)
    if prop == 'exists':
        return len(matched) > 0
    return None


def _resolve_effects_path(path: str, ctx: dict[str, Any]):
    """Resolve effects: <target>.effects[<filter>].<property>"""
    m = re.match(r'(\w+)\.effects\[([^\]]+)\]\.(\w+)', path)
    if not m:
        return None

    target_key = m.group(1)
    filter_str = m.group(2)
    prop = m.group(3)

    sprite = ctx.get(target_key)
    if sprite is None:
        return None

    filters: list[tuple[str, str, str]] = []
    for part in filter_str.split(','):
        fm = re.match(r'(\w+)([<>=!]+)(.+)', part.strip())
        if fm:
            filters.append((fm.group(1), fm.group(2), fm.group(3)))

    effects = getattr(sprite, 'effects', [])
    matched = []
    for e in effects:
        ok = True
        for fkey, fop, fval in filters:
            if fkey == 'name':
                ev = getattr(e, 'name', '')
            elif fkey == 'type':
                ev = getattr(e, 'category', '')
            else:
                ev = getattr(e, fkey, None)
            if not _cmp(fop, ev, fval):
                ok = False
                break
        if ok:
            matched.append(e)

    if prop == 'exists':
        return len(matched) > 0
    if prop == 'count':
        return len(matched)
    if prop == 'stacks':
        return sum(getattr(e, 'stacks', 0) for e in matched)
    if prop == 'steps':
        return sum(getattr(e, 'steps', 0) for e in matched)
    return None


def _resolve_ref_string(expr: str, ctx: dict[str, Any]):
    """Resolve a ref expression string (e.g. '=@self.energy') to a value."""
    if not expr.startswith('='):
        return expr
    expr = expr[1:]

    # Arithmetic expression
    if re.search(r'[\+\-\*\/\(]', expr):
        return _eval_arithmetic(expr, ctx)

    return _resolve_single(expr, ctx)


def _resolve_single(expr: str, ctx: dict[str, Any]):
    """Resolve a single ref expression."""
    if not expr.startswith('@'):
        try:
            return int(expr)
        except ValueError:
            try:
                return float(expr)
            except ValueError:
                return expr

    path = expr[1:]  # e.g. "self.energy"
    return _resolve_path(path, ctx)


def _eval_arithmetic(expr: str, ctx: dict[str, Any]) -> int | float:
    """Evaluate a ref arithmetic expression."""
    def replace_ref(m):
        ref_expr = m.group(0)
        val = _resolve_single(ref_expr, ctx)
        return str(val) if val is not None else '0'

    resolved = re.sub(
        r'@[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*(?:\[[^\]]*\])?)*',
        replace_ref, expr,
    )

    try:
        return eval(resolved, {'__builtins__': {}}, {
            'int': int, 'float': float, 'round': round, 'max': max, 'min': min,
        })
    except Exception:
        return 0
