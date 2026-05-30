"""schedule opcode — register delayed effects for a future turn.

V2: Typed Schedule op.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from ..ctx import Ctx
from ..ir_values import Literal, Query, RefExpr
from ..journal import Mutation, ScheduleEntry
from ..resolve import resolve

if TYPE_CHECKING:
    pass


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


# ── Freeze: resolve all IRValues → Literals at schedule time ──

def _freeze_value(value, ctx: Ctx):
    """Resolve a single IRValue/query/formula to a frozen raw value.

    Returns raw primitives (int/float/str), NOT Literal wrappers,
    so the frozen dict can be re-parsed without creating nested Literals.
    """
    if value is None:
        return None
    if isinstance(value, Literal):
        if isinstance(value.value, str) and value.value.startswith("="):
            return resolve(ctx, value)
        return value.value
    if isinstance(value, (Query, RefExpr)):
        return resolve(ctx, value)
    if isinstance(value, dict) and "q" in value:
        return resolve(ctx, value)
    if isinstance(value, str) and value.startswith("=@"):
        return resolve(ctx, value)
    return value


def _freeze_effects(effects, ctx: Ctx):
    """Recursively freeze all IRValues in effects to Literals."""
    if not effects:
        return effects
    return tuple(_freeze_one(e, ctx) for e in effects)


def _freeze_one(effect, ctx: Ctx):
    """Freeze a single effect (dict or SkillIROp dataclass)."""
    if isinstance(effect, dict):
        return _freeze_dict(effect, ctx)
    return _freeze_op(effect, ctx)


def _freeze_dict(d: dict, ctx: Ctx) -> dict:
    """Freeze IRValues in a dict effect recursively."""
    result = {}
    for k, v in d.items():
        if k in ("then", "effects", "else_"):
            if isinstance(v, (list, tuple)):
                result[k] = list(_freeze_effects(v, ctx))
            else:
                result[k] = v
        elif k == "elif_":
            if isinstance(v, (list, tuple)):
                result[k] = [
                    _freeze_dict(b, ctx) if isinstance(b, dict) else b
                    for b in v
                ]
            else:
                result[k] = v
        elif k in ("delta", "value", "ratio", "power", "hp_ratio", "stacks"):
            result[k] = _freeze_value(v, ctx)
        else:
            result[k] = v
    return result


def _freeze_op(op, ctx: Ctx):
    """Freeze IRValues in a SkillIROp dataclass, recursing into nested containers."""
    from ..ir_skill import (
        AbnormalOp,
        EnergizeOp,
        FlagSetOp,
        HealOp,
        HitOp,
        MarkOp,
        ModOp,
        MultModOp,
        PowerModOp,
        ReviveOp,
        StatStageOp,
    )

    # Recurse into nested then/else_/elif_ first
    op = _freeze_nested(op, ctx)

    # Freeze IRValue fields by type
    match op:
        case StatStageOp(value=v) if v is not None:
            return dataclasses.replace(op, value=_freeze_value(v, ctx))
        case PowerModOp(delta=d, value=v):
            kw = {}
            if d is not None:
                kw["delta"] = _freeze_value(d, ctx)
            if v is not None:
                kw["value"] = _freeze_value(v, ctx)
            return dataclasses.replace(op, **kw) if kw else op
        case MultModOp(value=v) if v is not None:
            return dataclasses.replace(op, value=_freeze_value(v, ctx))
        case FlagSetOp(value=v) if v is not None:
            return dataclasses.replace(op, value=_freeze_value(v, ctx))
        case HealOp(value=v) if v is not None:
            return dataclasses.replace(op, value=_freeze_value(v, ctx))
        case EnergizeOp(delta=d) if d is not None:
            return dataclasses.replace(op, delta=_freeze_value(d, ctx))
        case ReviveOp(hp_ratio=h) if h is not None:
            return dataclasses.replace(op, hp_ratio=_freeze_value(h, ctx))
        case ModOp(value=v):
            return dataclasses.replace(op, value=_freeze_value(v, ctx))
        case HitOp(power=p):
            return dataclasses.replace(op, power=_freeze_value(p, ctx))
        case MarkOp(value=v):
            kw = {}
            if v is not None:
                kw["value"] = _freeze_value(v, ctx)
            return dataclasses.replace(op, **kw) if kw else op
        case AbnormalOp(value=v):
            kw = {}
            if v is not None:
                kw["value"] = _freeze_value(v, ctx)
            return dataclasses.replace(op, **kw) if kw else op

    return op


def _freeze_nested(op, ctx: Ctx):
    """Recurse into nested then/else_/elif_ containers."""
    from ..ir_skill import (
        AbnormalOp,
        BurstGrantOp,
        CountOp,
        EscapeOp,
        MarkOp,
        ModOp,
        Schedule,
        WhenBlock,
    )

    match op:
        case WhenBlock(then=t, else_=e, elif_=ei):
            kw = {}
            if t:
                kw["then"] = _freeze_effects(t, ctx)
            if e:
                kw["else_"] = _freeze_effects(e, ctx)
            if ei:
                kw["elif_"] = tuple(
                    dataclasses.replace(b, then=_freeze_effects(b.then, ctx))
                    for b in ei
                )
            return dataclasses.replace(op, **kw) if kw else op
        case EscapeOp(then=t) if t:
            return dataclasses.replace(op, then=_freeze_effects(t, ctx))
        case CountOp(then=t) if t:
            return dataclasses.replace(op, then=_freeze_effects(t, ctx))
        case Schedule(then=t) if t:
            return dataclasses.replace(op, then=_freeze_effects(t, ctx))
        case BurstGrantOp(then=t) if t:
            return dataclasses.replace(op, then=_freeze_effects(t, ctx))
        case MarkOp(then=t) if t:
            return dataclasses.replace(op, then=_freeze_effects(t, ctx))
        case AbnormalOp(then=t) if t:
            return dataclasses.replace(op, then=_freeze_effects(t, ctx))
        case ModOp(then=t) if t:
            return dataclasses.replace(op, then=_freeze_effects(t, ctx))

    return op


# ── Main opcode ──

def op_schedule(ctx: Ctx, effect) -> list[Mutation]:
    turns = _get(effect, "turns", None)
    turns = int(_get(effect, "delay_turns", 1)) if turns is None else int(turns)

    at = _get(effect, "at", None) or _get(effect, "phase", None) or "start"
    if at == "turn_end":
        at = "end"
    elif at == "turn_start":
        at = "start"

    then = _get(effect, "then", None) or _get(effect, "effects", ())
    # Freeze: resolve all Query/RefExpr/formula → Literal at schedule time
    then = _freeze_effects(then, ctx)
    return [ScheduleEntry(
        turns=turns,
        at=at,
        then=list(then),
    )]
