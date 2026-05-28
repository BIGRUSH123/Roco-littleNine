"""count opcode — register a persistent counter/watcher.

Unlike other opcodes, count does not execute immediately. It registers a
Counter that fires when future events match the condition. The engine
evaluates counters after each relevant event (skill use, damage, KO, etc.).

V2: Supports typed CountOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import CounterRegister, Mutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_count(ctx: Ctx, effect) -> list[Mutation]:
    """Register a persistent counter that fires on matching events."""
    cond = _get(effect, "cond") or _get(effect, "when")
    then = _get(effect, "then", [])
    scope = _get(effect, "scope", "persistent")
    name = _get(effect, "name")
    threshold = _get(effect, "threshold", 1)
    reset_on_fire = _get(effect, "reset_on_fire", True)

    listen = None
    listen_raw = _get(effect, "listen")
    if listen_raw is not None:
        listen = frozenset([listen_raw]) if isinstance(listen_raw, str) else frozenset(listen_raw)

    return [CounterRegister(
        name=name,
        cond=cond,
        then=then,
        scope=scope,
        listen=listen,
        threshold=threshold,
        reset_on_fire=reset_on_fire,
    )]
