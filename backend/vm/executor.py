"""VM executor — pure (Ctx, effects[]) -> Journal transform.

Dict effects are compiled to typed SkillIROp on-the-fly via SkillParsePass,
then dispatched by typed match/case. WhenBlocks are handled recursively.

Entry points:
    execute(ctx, effects)        — sort + process all effects
    process_effects(ctx, effs)   — process unsorted effects
    process_one(ctx, effect)     — process a single effect
"""

from .cond import eval_one
from .ctx import Ctx

# V2: Typed IR ops for match/case dispatch
from .ir_skill import (
    AbnormalOp,
    BorrowOp,
    ChargeOp,
    CountOp,
    DispelOp,
    DoubleOp,
    EnergizeOp,
    EscapeOp,
    ExchangeOp,
    GainSkills,
    FlagSetOp,
    HealOp,
    HitOp,
    InheritEffects,
    InterruptOp,
    LivesChange,
    LockOp,
    MarkOp,
    ModOp,
    MultModOp,
    PowerModOp,
    RedirectOp,
    ReplayOp,
    ResetOp,
    ReturnOp,
    ReviveOp,
    Schedule,
    StatStageOp,
    StealOp,
    TeamCounterWrite,
    TickOp,
    TraitInteraction,
    Transform,
    WeatherOp,
    WhenBlock,
)
from .journal import Journal, Mutation
from .ops.abnormal import op_abnormal
from .ops.borrow import op_borrow
from .ops.charge import op_charge
from .ops.count import op_count
from .ops.dispel import op_dispel
from .ops.double import op_double
from .ops.escape import op_escape
from .ops.gain_skills import op_gain_skills
from .ops.exchange import op_exchange
from .ops.hit import op_hit
from .ops.interrupt import op_interrupt
from .ops.lock import op_lock
from .ops.mark import op_mark

# Import all op handlers
from .ops.mod import (
    op_energize, op_flag_set, op_heal, op_mod, op_mult_mod,
    op_power_mod, op_revive, op_stat_stage,
)
from .ops.redirect import op_redirect
from .ops.replay import op_replay
from .ops.reset import op_reset
from .ops.return_ import op_return
from .ops.steal import op_steal
from .ops.tick import op_tick
from .ops.weather import op_weather
from .ops.team_counter_write import op_team_counter_write
from .ops.lives_change import op_lives_change
from .ops.schedule import op_schedule
from .ops.inherit_effects import op_inherit_effects
from .ops.transform import op_transform
from .ops.trait_interaction import op_trait_interaction
from .sort import sort_effects

# ── Cached parser for dict → typed op compilation ──

_parser = None

def _get_parser():
    global _parser
    if _parser is None:
        from backend.vm.compiler.passes.skill_parse import SkillParsePass
        _parser = SkillParsePass()
    return _parser


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


def _compile_effect(effect: dict):
    """Compile a raw dict effect to a typed SkillIROp."""
    if "when" in effect and "op" not in effect:
        cond = effect["when"]
        if isinstance(cond, dict) and "cond" not in cond:
            return None  # skip legacy kind-based conditions (dead triggers format)
    parser = _get_parser()
    return parser._parse_effect(effect)


def process_one(ctx: Ctx, op) -> list[Mutation]:
    """Process a single effect and return its mutations.

    Dict effects are compiled to typed SkillIROp on-the-fly, then dispatched
    via typed match/case.
    """
    # ── Compile dict → typed op ──
    if isinstance(op, dict):
        op = _compile_effect(op)
        if op is None:
            return []

    # ── Typed match/case dispatch ──
    match op:
        case WhenBlock():
            return _process_whenblock(ctx, op)
        case ModOp(on_next=True):
            return _defer_mod(ctx, op)
        # RISC register-modifying ops
        case StatStageOp():
            return op_stat_stage(ctx, op)
        case PowerModOp():
            return op_power_mod(ctx, op)
        case MultModOp():
            return op_mult_mod(ctx, op)
        case FlagSetOp():
            return op_flag_set(ctx, op)
        case HealOp():
            return op_heal(ctx, op)
        case EnergizeOp():
            return op_energize(ctx, op)
        case ReviveOp():
            return op_revive(ctx, op)
        # Legacy mega-opcode
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
        case TeamCounterWrite():
            return op_team_counter_write(ctx, op)
        case LivesChange():
            return op_lives_change(ctx, op)
        case Schedule():
            return op_schedule(ctx, op)
        case InheritEffects():
            return op_inherit_effects(ctx, op)
        case Transform():
            return op_transform(ctx, op)
        case TraitInteraction():
            return op_trait_interaction(ctx, op)
        case GainSkills():
            return op_gain_skills(ctx, op)
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
