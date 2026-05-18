"""VM executor — pure (Ctx, effects[]) -> Journal transform.

The executor processes effects sequentially, dispatching each via typed
match/case (V2) with backward-compat dict fallback. 'when' blocks are
handled recursively via eval_one condition evaluation.

Implicit damage (from skill power + attack type) is NOT added by the
executor — the engine handles that separately.

Entry points:
    execute(ctx, effects)        — sort + process all effects
    process_effects(ctx, effs)   — process unsorted effects
    process_one(ctx, effect)     — process a single effect
"""

from .ctx import Ctx
from .cond import eval_one
from .sort import sort_effects
from .journal import Mutation, Journal

# V2: Typed IR ops for match/case dispatch
from .ir_skill import (
    SkillIROp, ModOp, HitOp, MarkOp, AbnormalOp, WeatherOp,
    DispelOp, StealOp, TickOp, DoubleOp, ChargeOp,
    EscapeOp, ReturnOp, LockOp, InterruptOp,
    ExchangeOp, ResetOp, RedirectOp, ReplayOp,
    BorrowOp, CountOp, WhenBlock, WhenBranch,
)

# Import all op handlers
from .ops.mod import op_mod
from .ops.hit import op_hit
from .ops.mark import op_mark
from .ops.abnormal import op_abnormal
from .ops.weather import op_weather
from .ops.dispel import op_dispel
from .ops.steal import op_steal
from .ops.tick import op_tick
from .ops.double import op_double
from .ops.charge import op_charge
from .ops.escape import op_escape
from .ops.return_ import op_return
from .ops.lock import op_lock
from .ops.interrupt import op_interrupt
from .ops.exchange import op_exchange
from .ops.reset import op_reset
from .ops.redirect import op_redirect
from .ops.replay import op_replay
from .ops.borrow import op_borrow
from .ops.count import op_count


# ── Backward compat: dict dispatch (used when raw dict is passed) ──

_DICT_DISPATCH = {
    "mod": op_mod,
    "hit": op_hit,
    "mark": op_mark,
    "abnormal": op_abnormal,
    "weather": op_weather,
    "dispel": op_dispel,
    "steal": op_steal,
    "tick": op_tick,
    "double": op_double,
    "charge": op_charge,
    "escape": op_escape,
    "return": op_return,
    "lock": op_lock,
    "interrupt": op_interrupt,
    "exchange": op_exchange,
    "reset": op_reset,
    "redirect": op_redirect,
    "replay": op_replay,
    "borrow": op_borrow,
    "count": op_count,
}


def execute(ctx: Ctx, effects, *, sort: bool = True) -> Journal:
    """Main VM entry point: sort effects by phase, then process.

    Returns a Journal (list of Mutations) for the engine to replay.
    Accepts both typed SkillIROp lists and raw dict lists.
    """
    if sort:
        effects = sort_effects(effects)
    return process_effects(ctx, effects)


def process_effects(ctx: Ctx, effects) -> list[Mutation]:
    """Process a list of effects sequentially and return accumulated mutations.

    Each effect is either:
        - A typed SkillIROp (ModOp, WhenBlock, etc.)
        - A conditional block: {"when": cond, "then": [...], "else": [...]}
        - An opcode effect:   {"op": "mod", ...}

    Conditional blocks evaluate cond against ctx and recursively process
    the chosen branch.
    """
    journal: list[Mutation] = []

    for effect in effects:
        result = process_one(ctx, effect)
        journal.extend(result)

    return journal


def _process_whenblock(ctx, wb: WhenBlock) -> list[Mutation]:
    """Process a typed WhenBlock by evaluating its condition and executing
    the matching branch."""
    if eval_one(ctx, wb.cond):
        return process_effects(ctx, wb.then)
    else:
        for branch in wb.elif_:
            if eval_one(ctx, branch.cond):
                return process_effects(ctx, branch.then)
        return process_effects(ctx, wb.else_)


def _process_dict_effect(ctx, effect: dict) -> list[Mutation]:
    """Backward compat: process a raw dict effect."""
    # ── Conditional branching: {"when": cond, "then": [...], "else": [...]} ──
    if "when" in effect and "op" not in effect:
        cond = effect["when"]
        # Skip legacy kind-based conditions (not yet migrated)
        if isinstance(cond, dict) and "cond" not in cond:
            return []
        if eval_one(ctx, cond):
            return process_effects(ctx, effect.get("then", []))
        else:
            elif_chain = effect.get("elif", [])
            for branch in elif_chain:
                if eval_one(ctx, branch["when"]):
                    return process_effects(ctx, branch.get("then", []))
            return process_effects(ctx, effect.get("else", []))

    # ── Regular opcode ──
    op = effect.get("op")
    if not op:
        if "kind" in effect:
            return []
        return []

    handler = _DICT_DISPATCH.get(op)
    if handler is None:
        raise KeyError(f"Unknown opcode: {op}")

    return handler(ctx, effect)


def process_one(ctx: Ctx, op) -> list[Mutation]:
    """Process a single effect and return its mutations.

    V2: Typed match/case dispatch on SkillIROp types.
    Backward compat: raw dict fallback.

    Handles:
        - WhenBlock → recursive branch evaluation
        - All 21 op types → direct handler call
    """
    # ── Backward compat: raw dict ──
    if isinstance(op, dict):
        return _process_dict_effect(ctx, op)

    # ── V2: Typed match/case dispatch ──
    match op:
        case WhenBlock():
            return _process_whenblock(ctx, op)
        case ModOp(on_next=True):
            return _defer_mod(ctx, op)
        case ModOp():
            return op_mod(ctx, op)
        case HitOp():
            return op_hit(ctx, op)
        case MarkOp():
            return op_mark(ctx, op)
        case AbnormalOp():
            return op_abnormal(ctx, op)
        case WeatherOp():
            return op_weather(ctx, op)
        case DispelOp():
            return op_dispel(ctx, op)
        case StealOp():
            return op_steal(ctx, op)
        case TickOp():
            return op_tick(ctx, op)
        case DoubleOp():
            return op_double(ctx, op)
        case ChargeOp():
            return op_charge(ctx, op)
        case EscapeOp():
            return op_escape(ctx, op)
        case ReturnOp():
            return op_return(ctx, op)
        case LockOp():
            return op_lock(ctx, op)
        case InterruptOp():
            return op_interrupt(ctx, op)
        case ExchangeOp():
            return op_exchange(ctx, op)
        case ResetOp():
            return op_reset(ctx, op)
        case RedirectOp():
            return op_redirect(ctx, op)
        case ReplayOp():
            return op_replay(ctx, op)
        case BorrowOp():
            return op_borrow(ctx, op)
        case CountOp():
            return op_count(ctx, op)
        case _:
            return []


def _defer_mod(ctx, op: ModOp) -> list[Mutation]:
    """Handle ModOp with on_next=True by deferring for next-turn application.

    The engine will apply this on the following turn's skill execution.
    For now, return empty (deferred effects are engine-side behavior).
    """
    return []


# Convenience alias for the single-effect entry point
execute_one = process_one
