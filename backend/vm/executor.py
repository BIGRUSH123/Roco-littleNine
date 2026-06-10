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
    BurstGrantOp,
    ChargeOp,
    CountOp,
    DispelOp,
    DoubleOp,
    EffectDeltaOp,
    EnergizeOp,
    EscapeOp,
    ExchangeOp,
    FlagSetOp,
    GainSkills,
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
from .ops.burst_grant import op_burst_grant
from .ops.charge import op_charge
from .ops.count import op_count
from .ops.dispel import op_dispel
from .ops.double import op_double
from .ops.effect_delta import op_effect_delta
from .ops.escape import op_escape
from .ops.exchange import op_exchange
from .ops.gain_skills import op_gain_skills
from .ops.hit import op_hit
from .ops.inherit_effects import op_inherit_effects
from .ops.interrupt import op_interrupt
from .ops.lives_change import op_lives_change
from .ops.lock import op_lock
from .ops.mark import op_mark

# Import all op handlers
from .ops.mod import (
    op_energize,
    op_flag_set,
    op_heal,
    op_mod,
    op_mult_mod,
    op_power_mod,
    op_revive,
    op_stat_stage,
)
from .ops.redirect import op_redirect
from .ops.replay import op_replay
from .ops.reset import op_reset
from .ops.return_ import op_return
from .ops.schedule import op_schedule
from .ops.steal import op_steal
from .ops.team_counter_write import op_team_counter_write
from .ops.tick import op_tick
from .ops.trait_interaction import op_trait_interaction
from .ops.transform import op_transform
from .ops.weather import op_weather
from .sort import sort_effects

# ── Cached parser for dict → typed op compilation ──

_parser = None
_SORTED_EFFECTS_CACHE: dict[int, tuple[object, list]] = {}


def _get_parser():
    global _parser
    if _parser is None:
        from backend.vm.compiler.passes.skill_parse import SkillParsePass
        _parser = SkillParsePass()
    return _parser


def compile_effects_batch(effects: list) -> list:
    """批量预编译 dict 效果列表为 typed IR 对象。

    嵌套 when/then/else 由 SkillParsePass._parse_effect 递归处理。
    返回新列表（不妨碍调用方复用原列表）。
    """
    if not effects:
        return effects
    parser = _get_parser()
    result = []
    for eff in effects:
        if isinstance(eff, dict):
            compiled = parser._parse_effect(eff)
            if compiled is not None:
                result.append(compiled)
        else:
            result.append(eff)
    return result


def execute(ctx: Ctx, effects, *, sort: bool = True) -> Journal:
    """Main VM entry point: sort effects by phase, then process.

    Returns a Journal (list of Mutations) for the engine to replay.
    Accepts both typed SkillIROp lists and raw dict lists.
    """
    if sort:
        effects = _sort_effects_cached(effects)
    return process_effects(ctx, effects)


def _sort_effects_cached(effects):
    if type(effects) is not tuple:
        return sort_effects(effects)
    cache_key = id(effects)
    cached = _SORTED_EFFECTS_CACHE.get(cache_key)
    if cached is not None and cached[0] is effects:
        return cached[1]
    sorted_effects = sort_effects(effects)
    _SORTED_EFFECTS_CACHE[cache_key] = (effects, sorted_effects)
    return sorted_effects


def process_effects(ctx: Ctx, effects) -> list[Mutation]:
    """Process a list of effects sequentially and return accumulated mutations.

    热路径优化：高频 typed op 及 WhenBlock 直接通过 type() 恒等比较
    内联 dispatch，消除 process_one 调用帧开销。
    其余 op 类型回退到 process_one 的 match/case 分发。
    """
    journal: list[Mutation] = []

    for op in effects:
        t = type(op)
        # ── Top-8 hot-path inlined dispatch ──
        if t is ModOp:
            if op.on_next:
                journal.extend(_defer_mod(ctx, op))
            else:
                journal.extend(op_mod(ctx, op))
        elif t is StatStageOp:
            journal.extend(op_stat_stage(ctx, op))
        elif t is PowerModOp:
            journal.extend(op_power_mod(ctx, op))
        elif t is MultModOp:
            journal.extend(op_mult_mod(ctx, op))
        elif t is FlagSetOp:
            journal.extend(op_flag_set(ctx, op))
        elif t is HealOp:
            journal.extend(op_heal(ctx, op))
        elif t is EnergizeOp:
            journal.extend(op_energize(ctx, op))
        elif t is ReviveOp:
            journal.extend(op_revive(ctx, op))
        elif t is HitOp:
            journal.extend(op_hit(ctx, op))
        elif t is WhenBlock:
            journal.extend(_process_whenblock(ctx, op))
        # ── dict 回退：未预编译的效果（JIT 编译 + 一次性告警） ──
        elif t is dict:
            journal.extend(process_one(ctx, op))
        # ── 其余类型 → process_one match/case ──
        else:
            journal.extend(process_one(ctx, op))

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
        if type(cond) is dict and "cond" not in cond:
            return None  # skip legacy kind-based conditions (dead triggers format)
    parser = _get_parser()
    return parser._parse_effect(effect)


def process_one(ctx: Ctx, op) -> list[Mutation]:
    """Process a single effect and return its mutations.

    Dict effects are compiled to typed SkillIROp on-the-fly, then dispatched
    via typed match/case.  type(op) is dict 比 isinstance 快 ~2×（跳过 MRO 遍历）。
    """
    # ── Compile dict → typed op ──
    if type(op) is dict:
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
        case EffectDeltaOp():
            return op_effect_delta(ctx, op)
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
        case BurstGrantOp():
            return op_burst_grant(ctx, op)
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
