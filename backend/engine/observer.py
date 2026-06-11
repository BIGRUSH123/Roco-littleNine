"""Observer system — passive effects that fire when conditions are met.

Observers are registered by battle/trait setup and evaluated after each
relevant event (skill use, damage, KO, switch, etc.). Each observer has
a condition, a then-block of effects, and a scope that controls lifetime.

Trigger filtering: listen triggers are inferred from the condition type
via infer_triggers(). Only observers whose listen set contains the current
trigger are evaluated. An empty listen set means "fire on all triggers"
(backward compat / unknown condition types).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from backend.vm.cond import infer_triggers
from backend.vm.ctx import Ctx
from backend.vm.executor import process_effects
from backend.vm.journal import Mutation


# ═══════════════════════════════════════════════════════════════════
# Pre-bake helpers — 注册时一次性注入 source/scope，消除运行时 copy.copy
# ═══════════════════════════════════════════════════════════════════

def _bake_inject_source(effects: list[dict], source: str) -> None:
    """在 effects 树中注入 source（原地修改，无拷贝）。

    递归处理 when/then/else 嵌套。仅对缺失 "source" 的效果赋值，
    已有 source 的效果保持原值不变。

    跳过非 dict 类型的元素（编译后的 IR 对象如 PowerModOp，
    其 source 已在编译时设置且不可原地修改）。
    """
    for eff in effects:
        if not isinstance(eff, dict):
            continue
        if "op" in eff and "source" not in eff:
            eff["source"] = source
        if isinstance(eff.get("then"), list):
            _bake_inject_source(eff["then"], source)
        if isinstance(eff.get("else"), list):
            _bake_inject_source(eff["else"], source)


def _bake_inject_scope(effects: list[dict], scope: str) -> None:
    """在 effects 树中注入 scope（原地修改，无拷贝，幂等）。

    仅对缺失 "scope" 的效果赋值。首次调用后所有效果已带 scope，
    后续调用均为 no-op。供 _fire_post_event 使用（_fire_pre_event 不注入 scope）。

    跳过非 dict 类型的元素（编译后的 IR 对象如 PowerModOp），
    其 scope 已在编译时设置且不可原地修改。
    """
    for eff in effects:
        if not isinstance(eff, dict):
            continue
        if "op" in eff and "scope" not in eff:
            eff["scope"] = scope
        if isinstance(eff.get("then"), list):
            _bake_inject_scope(eff["then"], scope)
        if isinstance(eff.get("else"), list):
            _bake_inject_scope(eff["else"], scope)

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
    owner_skill_id: int | None = None   # id() of the BattleSkill that owns this observer
    _hit_count: int = field(default=0, repr=False)  # internal counter
    # Lazily-compiled condition callable (ctx) -> bool. Memoizes eval_one's
    # dispatch so repeated firings skip the isinstance chain + dict lookup.
    # cond is immutable after registration, so the cache stays valid.
    _compiled_cond: object = field(default=None, compare=False, repr=False)
    # Global registration order, assigned by the registry. Used to merge the
    # owner sub-index back into exact registration order on owner-filtered
    # fires. compare=False so it never affects counter-dedup equality.
    _reg_seq: int = field(default=0, compare=False, repr=False)

    def eval_cond(self, ctx) -> bool:
        """Evaluate this observer's condition, compiling+caching on first use."""
        fn = self._compiled_cond
        if fn is None:
            from backend.vm.cond import compile_cond
            fn = self._compiled_cond = compile_cond(self.cond)
        return fn(ctx)

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
        # 按 trigger 分桶：fire(trigger) 只遍历匹配桶 + fallback，避免全量扫描
        self._by_trigger: dict[str, list[Observer]] = {}
        self._fallback: list[Observer] = []  # listen 为空 → 全部 trigger 触发
        # Owner sub-index over the SAME population as _by_trigger (listen-matched
        # observers only). Lets owner-filtered fires iterate just
        # {ownerless ∪ owned-by-actor} instead of both teams' observers.
        # trigger → owner_sprite_id → [observers]
        self._owned_by_trigger: dict[str, dict[int, list[Observer]]] = {}
        # trigger → [observers with owner_sprite_id is None]
        self._ownerless_by_trigger: dict[str, list[Observer]] = {}
        self._owner_candidate_cache: dict[tuple[str, int], tuple[Observer, ...]] = {}
        self._seq_counter: int = 0  # monotonic registration sequence

    # ── Registration ──

    def _index(self, obs: Observer) -> None:
        # 注册时一次性注入 source 到 then 效果树（原地修改，无拷贝），
        # 消除 _fire_pre_event / _fire_post_event 运行时的 copy.copy 开销。
        if obs.source and obs.then:
            _bake_inject_source(obs.then, obs.source)
        obs._reg_seq = self._seq_counter
        self._seq_counter += 1
        self._observers.append(obs)
        self._owner_candidate_cache.clear()
        if obs.listen:
            owner = obs.owner_sprite_id
            for t in obs.listen:
                self._by_trigger.setdefault(t, []).append(obs)
                if owner is None:
                    self._ownerless_by_trigger.setdefault(t, []).append(obs)
                else:
                    self._owned_by_trigger.setdefault(t, {}).setdefault(owner, []).append(obs)
        else:
            self._fallback.append(obs)

    def _rebuild_index(self) -> None:
        self._by_trigger.clear()
        self._fallback.clear()
        self._owned_by_trigger.clear()
        self._ownerless_by_trigger.clear()
        self._owner_candidate_cache.clear()
        # Re-derive _reg_seq from list position (registration order) so the
        # owner sub-index merge stays correct even after snapshot restore,
        # where observers are copied in and may carry stale seq values.
        for i, obs in enumerate(self._observers):
            obs._reg_seq = i
            if obs.listen:
                owner = obs.owner_sprite_id
                for t in obs.listen:
                    self._by_trigger.setdefault(t, []).append(obs)
                    if owner is None:
                        self._ownerless_by_trigger.setdefault(t, []).append(obs)
                    else:
                        self._owned_by_trigger.setdefault(t, {}).setdefault(owner, []).append(obs)
            else:
                self._fallback.append(obs)
        self._seq_counter = len(self._observers)

    def register(self, observer: Observer) -> None:
        self._index(observer)

    def register_many(self, observers: list[Observer]) -> None:
        for obs in observers:
            self._index(obs)

    def unregister_by_owner(self, sprite_id: int, reason: str = "leave") -> int:
        before = len(self._observers)
        self._observers = [
            obs for obs in self._observers
            if obs.owner_sprite_id != sprite_id or not obs.should_clear(reason)
        ]
        self._rebuild_index()
        return before - len(self._observers)

    def register_from_counter(self, counter) -> None:
        explicit_listen = getattr(counter, 'listen', None)
        self._index(Observer(
            cond=counter.cond,
            then=counter.then,
            scope=counter.scope,
            name=counter.name or "",
            listen=explicit_listen if explicit_listen is not None else infer_triggers(counter.cond),
            threshold=getattr(counter, 'threshold', 1),
            reset_on_fire=getattr(counter, 'reset_on_fire', True),
        ))

    # ── Firing ──

    def candidates_for(self, trigger: str):
        """Return observers that can fire for trigger."""
        candidates = self._by_trigger.get(trigger, ())
        if not self._fallback:
            return candidates
        if not candidates:
            return self._fallback
        return (obs for obs in self._observers if not obs.listen or trigger in obs.listen)

    def candidates_for_owner(self, trigger: str, owner_id: int | None):
        """Return observers for trigger that pass a single-owner filter.

        Single-owner semantics: an observer fires if it is ownerless OR owned
        by owner_id. This is the filter used by per-sprite triggers
        (post_entry/post_skill/turn_end/…). Returning only the relevant subset
        avoids iterating the other sprite's owned observers on every fire.

        Falls back to the full trigger bucket when owner_id is None or when
        listen-empty fallback observers exist (rare), so the caller's in-loop
        filter remains authoritative and registration order is preserved.
        """
        if owner_id is None or self._fallback:
            return self.candidates_for(trigger)
        cache_key = (trigger, owner_id)
        cached = self._owner_candidate_cache.get(cache_key)
        if cached is not None:
            return cached
        owned = self._owned_by_trigger.get(trigger, {}).get(owner_id)
        ownerless = self._ownerless_by_trigger.get(trigger, ())
        if not owned:
            result = tuple(ownerless)
            self._owner_candidate_cache[cache_key] = result
            return result
        if not ownerless:
            result = tuple(owned)
            self._owner_candidate_cache[cache_key] = result
            return result
        # Merge two registration-ordered lists back into global order.
        result = tuple(sorted((*ownerless, *owned), key=lambda o: o._reg_seq))
        self._owner_candidate_cache[cache_key] = result
        return result

    def has_candidates(self, trigger: str, owner_sprite_id: int | None = None) -> bool:
        """Fast pre-check before building expensive Ctx snapshots."""
        if owner_sprite_id is None:
            return bool(self._by_trigger.get(trigger) or self._fallback)
        if (
            self._ownerless_by_trigger.get(trigger)
            or self._owned_by_trigger.get(trigger, {}).get(owner_sprite_id)
        ):
            return True
        for obs in self._fallback:
            owner = obs.owner_sprite_id
            if owner is None or owner == owner_sprite_id:
                return True
        return False

    def has_any_candidates(self, triggers) -> bool:
        """Fast pre-check for multiple ownerless trigger points."""
        if self._fallback:
            return True
        return any(self._by_trigger.get(trigger) for trigger in triggers)

    def fire(self, trigger: str, ctx: Ctx,
             process_fn: Callable = None) -> list[Mutation]:
        if process_fn is None:
            process_fn = process_effects

        mutations: list[Mutation] = []
        # 只遍历匹配 trigger 的桶 + fallback，跳过硬过滤
        candidates = self._by_trigger.get(trigger, ())
        for obs in candidates:
            if not obs.is_active():
                continue
            try:
                if obs.eval_cond(ctx) and obs.hit():
                    mutations.extend(process_fn(ctx, obs.then))
            except Exception:
                continue
        for obs in self._fallback:
            if not obs.is_active():
                continue
            try:
                if obs.eval_cond(ctx) and obs.hit():
                    mutations.extend(process_fn(ctx, obs.then))
            except Exception:
                continue
        return mutations

    def fire_and_collect(self, trigger: str, ctx: Ctx) -> list[Observer]:
        result = []
        for obs in self._by_trigger.get(trigger, ()):
            if obs.is_active() and obs.eval_cond(ctx):
                result.append(obs)
        for obs in self._fallback:
            if obs.is_active() and obs.eval_cond(ctx):
                result.append(obs)
        return result

    # ── Lifecycle ──

    def clear_by_scope(self, scope: str) -> int:
        before = len(self._observers)
        self._observers = [o for o in self._observers if o.scope != scope]
        self._rebuild_index()
        return before - len(self._observers)

    def clear_by_source(self, source: str) -> int:
        before = len(self._observers)
        self._observers = [o for o in self._observers if o.source != source]
        self._rebuild_index()
        return before - len(self._observers)

    def clear_all(self) -> None:
        self._observers.clear()
        self._by_trigger.clear()
        self._fallback.clear()

    def __len__(self) -> int:
        return len(self._observers)

    def __iter__(self):
        return iter(self._observers)
