"""Observer system — passive effects that fire when conditions are met.

Observers are registered by battle/trait setup and evaluated after each
relevant event (skill use, damage, KO, switch, etc.). Each observer has
a condition, a then-block of effects, and a scope that controls lifetime.

Trigger filtering: listen triggers are inferred from the condition type
via infer_triggers(). Only observers whose listen set contains the current
trigger are evaluated. An empty listen set means "fire on all triggers"
(backward compat / unknown condition types).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from backend.vm.cond import eval_one, infer_triggers
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
    "post_heal",           # after HP heal
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
    listen: frozenset = field(default_factory=frozenset)  # Trigger points to evaluate on
    threshold: int = 1        # fire then every N condition hits (1 = every time)
    reset_on_fire: bool = True  # reset internal counter after then executes
    owner_sprite_id: int | None = None  # id() of the sprite that owns this observer
    _hit_count: int = field(default=0, repr=False)  # internal counter

    def is_active(self) -> bool:
        """Permanent observers never deactivate; others may be cleared."""
        return True  # engine manages lifecycle via scope

    def should_clear(self, reason: str) -> bool:
        """Whether this observer should be cleared for the given reason.

        reload: always clear (prevents duplicate registration)
        battlefield: clear on leave or faint
        persistent: clear only on faint
        permanent: never clear (except on reload)
        """
        if reason == "reload":
            return True  # always clear to prevent duplicates
        if self.scope == "battlefield":
            return True  # clear on any removal
        if self.scope == "persistent":
            return reason == "faint"
        return False  # permanent — never clear

    def hit(self) -> bool:
        """Increment hit counter; return True if threshold reached."""
        self._hit_count += 1
        if self._hit_count >= self.threshold:
            if self.reset_on_fire:
                self._hit_count = 0
            return True
        return False


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

    def unregister_by_owner(self, sprite_id: int, reason: str = "leave") -> int:
        """Remove observers owned by a sprite. Returns count removed."""
        before = len(self._observers)
        self._observers = [
            obs for obs in self._observers
            if obs.owner_sprite_id != sprite_id or not obs.should_clear(reason)
        ]
        return before - len(self._observers)

    def register_from_counter(self, counter) -> None:
        """Register an observer from a CounterRegister mutation.

        Auto-infers listen triggers from the condition type.
        """
        explicit_listen = getattr(counter, 'listen', None)
        self._observers.append(Observer(
            cond=counter.cond,
            then=counter.then,
            scope=counter.scope,
            name=counter.name or "",
            listen=explicit_listen if explicit_listen is not None else infer_triggers(counter.cond),
            threshold=getattr(counter, 'threshold', 1),
            reset_on_fire=getattr(counter, 'reset_on_fire', True),
        ))

    # ── Firing ──

    def fire(self, trigger: str, ctx: Ctx,
             process_fn: Callable = None) -> list[Mutation]:
        """Evaluate all observers for a given trigger point.

        Only observers whose listen set is empty (backward compat) or contains
        the current trigger are evaluated. Those whose condition evaluates True
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
            # Skip if observer has explicit listen triggers and this trigger
            # isn't one of them (empty listen = fire on all, backward compat)
            if obs.listen and trigger not in obs.listen:
                continue
            try:
                if eval_one(ctx, obs.cond):
                    if obs.hit():  # threshold gate — only execute when threshold reached
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
                if obs.is_active()
                and (not obs.listen or trigger in obs.listen)
                and eval_one(ctx, obs.cond)]

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
