"""BattleVMEngine — VM-powered skill execution for turn-based battles.

Integrates the pure-function VM with mutable battle state:
    1. Build Ctx snapshot from battle state
    2. Fire pre-calc observers → collect modifier injections
    3. VM.execute(ctx, effects) → Journal
    4. Replay Journal against mutable state
    5. Fire post-event observers

Can be used standalone or as a drop-in for Battle.execute_skill().
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from scripts.vm.ctx import Ctx
from scripts.vm.executor import execute as vm_execute, process_effects
from scripts.vm.journal import Journal, Mutation, ModifierInjection, CounterRegister
from scripts.vm.cond import eval_one

from .snapshot import build_ctx
from .observer import ObserverRegistry
from .replayer import JournalReplayer

if TYPE_CHECKING:
    from sim.sprite import Sprite
    from sim.globals import GlobalEffects
    from .skill_loader import SkillRecord


class SkillExecutionResult:
    """Result of executing one skill through the VM pipeline."""
    __slots__ = ("ctx", "journal", "events", "fainted_target")

    def __init__(self, ctx: Ctx, journal: Journal, events: list[str]):
        self.ctx = ctx
        self.journal = journal
        self.events = events
        self.fainted_target = any(
            "fainted" in e.lower() for e in events
        )


class BattleVMEngine:
    """VM-powered skill execution engine.

    Can be embedded in the existing Battle class or used standalone.
    """

    def __init__(self, registry: ObserverRegistry | None = None):
        self.registry = registry or ObserverRegistry()

    # ── Main execution flow ──

    def execute_skill(
        self,
        self_sprite: Sprite,
        opp_sprite: Sprite,
        self_skill,
        opp_skill = None,
        globals_: GlobalEffects | None = None,
        *,
        turn: int = 0,
        is_first: bool = False,
        effects: list[dict] | None = None,
        **kwargs,
    ) -> SkillExecutionResult:
        """Execute one skill through the full VM pipeline.

        Args:
            self_sprite: The skill user
            opp_sprite: The opponent
            self_skill: The skill being executed (SkillRecord, BattleSkill, or duck-typed)
            opp_skill: Opponent's current skill (for counter context)
            globals_: Global battle effects (weather, marks)
            effects: Explicit RISC IR effects (if None, read from self_skill.effects)
            **kwargs: Additional Ctx parameters (opp_switched, counter_succeeded, etc.)

        Returns:
            SkillExecutionResult with Ctx, Journal, and event strings
        """
        # 1. Build Ctx snapshot
        ctx = build_ctx(
            self_sprite, opp_sprite,
            self_skill, opp_skill, globals_,
            turn=turn, is_first=is_first, **kwargs,
        )

        # 2. Fire pre-calc observers → collect modifier injections
        pre_mods = self._fire_pre_calc(ctx)

        # 3. Execute VM on the skill's effects
        vm_effects = effects if effects is not None else self._get_effects(self_skill)
        journal = vm_execute(ctx, vm_effects)

        # 4. Merge pre-calc modifiers into journal
        if pre_mods:
            journal = pre_mods + journal

        # 5. Replay journal against mutable state
        replayer = JournalReplayer(
            self_sprite, opp_sprite, globals_, self.registry,
        )
        events = replayer.replay(journal)

        # 6. Fire post-skill observers
        post_ev = self._fire_post_event("post_skill", ctx, replayer)
        events.extend(post_ev)

        return SkillExecutionResult(ctx, journal, events)

    def execute_effects(
        self,
        ctx: Ctx,
        effects: list[dict],
    ) -> Journal:
        """Execute raw effects through the VM (used for observer then-blocks)."""
        return vm_execute(ctx, effects)

    # ── Observer integration ──

    def _fire_pre_calc(self, ctx: Ctx) -> Journal:
        """Fire pre-calculation observers and collect their mutations.

        Pre-calc observers (traits like "first action power bonus") run
        BEFORE the VM and inject modifier effects into the pipeline.
        """
        mutations: Journal = []
        for obs in self.registry._observers:
            try:
                if eval_one(ctx, obs.cond):
                    result = process_effects(ctx, obs.then)
                    mutations.extend(result)
            except Exception:
                continue
        return mutations

    def _fire_post_event(self, trigger: str, ctx: Ctx, replayer: JournalReplayer) -> list[str]:
        """Fire post-event observers and replay their mutations."""
        events: list[str] = []
        for obs in self.registry._observers:
            try:
                if eval_one(ctx, obs.cond):
                    journal = process_effects(ctx, obs.then)
                    ev = replayer.replay(journal)
                    events.extend(ev)
            except Exception:
                continue
        return events

    # ── Helpers ──

    @staticmethod
    def _get_effects(skill) -> list[dict]:
        """Extract RISC IR effects from a skill object.

        SkillRecord.effects are already list[dict] in op/when format.
        BattleSkill/Skill.effects are prototype Effect objects — unsupported
        (data/skills are now RISC IR format).
        """
        if hasattr(skill, 'effects'):
            effs = skill.effects
            if callable(effs):
                effs = effs()
            result = list(effs)
            if result and isinstance(result[0], dict):
                return result
        return []

    def register_counter(self, mutation: CounterRegister) -> None:
        """Register a persistent counter from a CounterRegister mutation."""
        from .observer import Observer
        self.registry.register(Observer(
            cond=mutation.cond,
            then=mutation.then,
            scope=mutation.scope,
            name=mutation.name or "",
            source="counter",
        ))
