"""Observer system — passive effects that fire when conditions are met.

Observers are registered by battle/trait setup and evaluated after each
relevant event (skill use, damage, KO, switch, etc.). Each observer has
a condition, a then-block of effects, and a scope that controls lifetime.
"""

from collections.abc import Callable
from dataclasses import dataclass

from backend.vm.cond import eval_one
from backend.vm.ctx import Ctx
from backend.vm.executor import process_effects
from backend.vm.journal import Mutation

# Trigger points — events that cause observers to be evaluated
TRIGGER_POINTS = frozenset({
    # Pre-execution
    "pre_calc",            # before VM execution (Ctx just built)
    "pre_modifier",        # L0→L1: before skill modifier computation
    "pre_defend",          # L1→L2: before damage taken, defender's trait
    # Post-execution
    "post_skill",          # after skill effects applied
    "post_damage",         # after damage taken
    "post_switch",         # after sprite switch
    "post_entry",          # after sprite enters
    "post_leave",          # after sprite leaves (switch/faint)
    "post_enemy_leave",    # after enemy sprite leaves
    "post_counter",        # after counter succeeds
    "post_ko",             # after a sprite faints
    # Event-driven
    "post_abnormal_tick",  # after abnormal tick damage
    "post_abnormal_change",# after abnormal stacks change
    "post_abnormal_apply", # after abnormal applied
    "post_energy_change",  # after energy changes
    "post_positive_change",# after positive effect count changes
    # Turn boundaries
    "turn_end",            # at turn-end settlement
})


@dataclass
class Observer:
    """A passive effect that triggers when its condition evaluates True.

    Equivalent to a trait-watcher or a persistent counter in the prototype.
    """
    cond: dict                # Trigger condition dict (COND_EVAL-compatible)
    then: list[dict]          # Effects to execute when triggered
    scope: str = "persistent" # "battlefield" | "persistent" | "permanent"
    name: str = ""            # Optional identifier
    source: str = ""          # Where this observer came from (skill/trait name)

    def is_active(self) -> bool:
        """Permanent observers never deactivate; others may be cleared."""
        return True  # engine manages lifecycle via scope


class ObserverRegistry:
    """Holds all registered observers and fires them on trigger points.

    Observers are evaluated in registration order within each trigger point.
    """

    def __init__(self):
        self._observers: list[Observer] = []

    # ── Registration ──

    def register(self, observer: Observer) -> None:
        self._observers.append(observer)

    def register_many(self, observers: list[Observer]) -> None:
        self._observers.extend(observers)

    def register_from_counter(self, counter) -> None:
        """Register an observer from a CounterRegister mutation."""
        self._observers.append(Observer(
            cond=counter.cond,
            then=counter.then,
            scope=counter.scope,
            name=counter.name or "",
        ))

    # ── Firing ──

    def fire(self, trigger: str, ctx: Ctx,
             process_fn: Callable = None) -> list[Mutation]:
        """Evaluate all observers for a given trigger point.

        Only observers whose condition evaluates True under the given Ctx
        will have their 'then' effects processed.

        Args:
            trigger: The trigger point name (e.g. "post_skill")
            ctx: Current Ctx snapshot
            process_fn: Optional custom effect processor (defaults to process_effects)

        Returns:
            Additional Mutations from triggered observers
        """
        if process_fn is None:
            process_fn = process_effects

        mutations: list[Mutation] = []
        for obs in self._observers:
            if not obs.is_active():
                continue
            try:
                if eval_one(ctx, obs.cond):
                    result = process_fn(ctx, obs.then)
                    mutations.extend(result)
            except Exception:
                # Observer evaluation failures should not crash the battle
                continue

        return mutations

    def fire_and_collect(self, trigger: str, ctx: Ctx) -> list[Observer]:
        """Return matching observers without executing them.

        Useful when the engine wants to inspect matches before processing.
        """
        return [obs for obs in self._observers
                if obs.is_active() and eval_one(ctx, obs.cond)]

    # ── Lifecycle ──

    def clear_by_scope(self, scope: str) -> int:
        """Remove all observers with the given scope. Returns count removed."""
        before = len(self._observers)
        self._observers = [o for o in self._observers if o.scope != scope]
        return before - len(self._observers)

    def clear_by_source(self, source: str) -> int:
        """Remove all observers from a given source (trait name). Returns count removed."""
        before = len(self._observers)
        self._observers = [o for o in self._observers if o.source != source]
        return before - len(self._observers)

    def clear_all(self) -> None:
        self._observers.clear()

    def __len__(self) -> int:
        return len(self._observers)

    def __iter__(self):
        return iter(self._observers)
