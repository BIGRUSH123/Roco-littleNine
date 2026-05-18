"""VM executor — pure (Ctx, effects[]) -> Journal transform.

The executor processes effects sequentially, dispatching each to the
appropriate opcode handler. 'when' blocks are handled recursively via
eval_one condition evaluation.

Implicit damage (from skill power + attack type) is NOT added by the
executor — the engine handles that separately.

Entry points:
    execute(ctx, effects)        — sort + process all effects
    process_effects(ctx, effs)   — process unsorted effects
    process_one(ctx, effect)     — process a single effect
"""

from .ctx import Ctx
from .cond import eval_one
from .ops import OP_DISPATCH
from .sort import sort_effects
from .journal import Mutation, Journal


def execute(ctx: Ctx, effects: list[dict], *, sort: bool = True) -> Journal:
    """Main VM entry point: sort effects by phase, then process.

    Returns a Journal (list of Mutations) for the engine to replay.
    """
    if sort:
        effects = sort_effects(effects)
    return process_effects(ctx, effects)


def process_effects(ctx: Ctx, effects: list[dict]) -> list[Mutation]:
    """Process a list of effects sequentially and return accumulated mutations.

    Each effect is either:
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


def process_one(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Process a single effect dict and return its mutations.

    Handles:
        - "when" blocks → recursive branch evaluation
        - Regular opcodes → OP_DISPATCH lookup
    """
    # ── Conditional branching: {"when": cond, "then": [...], "else": [...]} ──
    # Must NOT have "op" key — only pure when-blocks, not opcodes with when fields
    if "when" in effect and "op" not in effect:
        cond = effect["when"]
        # Skip legacy kind-based conditions (not yet migrated)
        if "cond" not in cond:
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
        # Skip legacy-format effects (kind-based) — engine will handle migration
        if "kind" in effect:
            return []
        return []

    handler = OP_DISPATCH.get(op)
    if handler is None:
        raise KeyError(f"Unknown opcode: {op}")

    return handler(ctx, effect)


# Convenience alias for the single-effect entry point
execute_one = process_one
