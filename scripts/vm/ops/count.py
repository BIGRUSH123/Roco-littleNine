"""count opcode — register a persistent counter/watcher.

Unlike other opcodes, count does not execute immediately. It registers a
Counter that fires when future events match the condition. The engine
evaluates counters after each relevant event (skill use, damage, KO, etc.).
"""

from ..ctx import Ctx
from ..journal import CounterRegister, Mutation


def op_count(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Register a persistent counter that fires on matching events.

    condition: the trigger condition dict (shared COND_EVAL table)
    then: effects to execute when the counter fires
    scope: "battlefield" | "persistent" | "permanent"
    name: optional name for counter_value queries

    The engine calls eval_one(ctx, cond) after each event; if true,
    the then effects are executed as if they were part of the current skill.
    """
    cond = effect["when"]
    then = effect.get("then", [])
    scope = effect.get("scope", "persistent")
    name = effect.get("name")

    return [CounterRegister(
        name=name,
        cond=cond,
        then=then,
        scope=scope,
    )]
