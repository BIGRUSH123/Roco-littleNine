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

from backend.vm.cond import eval_one
from backend.vm.ctx import Ctx
from backend.vm.executor import execute as vm_execute
from backend.vm.executor import process_effects
from backend.vm.journal import CounterRegister, Journal, Replay

from .modifiers import apply_modifiers_to_journal
from .observer import ObserverRegistry
from .replayer import JournalReplayer
from .snapshot import build_ctx

if TYPE_CHECKING:
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite



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
        self.registry = registry if registry is not None else ObserverRegistry()
        # Burst tracking: team → list of (skill_name, effects)
        self._burst_effects: dict[str, list[tuple[str, list[dict]]]] = {"A": [], "B": []}
        # Distinct burst skill names per team (for burst_triggered_count)
        self._burst_names: dict[str, set[str]] = {"A": set(), "B": set()}
        # Counter values: name → count (for counter_value queries)
        self._counter_values: dict[str, int] = {}
        # Skill history: sprite_id → [(skill_name, effects, tags_dict)]
        self._skill_history: dict[str, list[tuple[str, list[dict], dict]]] = {}
        # Skill tag lookup: sprite_id → {skill_name: tag}
        self._skill_tags: dict[str, dict[str, str]] = {}

    def burst_triggered_count(self, team: str) -> int:
        """Return the number of distinct burst skills triggered by this team."""
        return len(self._burst_names.get(team, set()))

    def _increment_counter(self, name: str | None) -> None:
        """Increment a named counter value. None = unnamed, no-op."""
        if name is not None:
            self._counter_values[name] = self._counter_values.get(name, 0) + 1

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
        team: str = "A",
        effects: list[dict] | None = None,
        species_lookup = None,
        battle_skill = None,
        **kwargs,
    ) -> SkillExecutionResult:
        """Execute one skill through the full VM pipeline.

        Args:
            self_sprite: The skill user
            opp_sprite: The opponent
            self_skill: The skill being executed (CompiledSkill, BattleSkill, or duck-typed)
            opp_skill: Opponent's current skill (for counter context)
            globals_: Global battle effects (weather, marks)
            team: "A" or "B" — which team the sprite belongs to
            effects: Explicit RISC IR effects (if None, read from self_skill.effects)
            **kwargs: Additional Ctx parameters (opp_switched, counter_succeeded, etc.)

        Returns:
            SkillExecutionResult with Ctx, Journal, and event strings
        """
        # 1. Build Ctx snapshot
        ctx = build_ctx(
            self_sprite, opp_sprite,
            self_skill, opp_skill, globals_,
            team=team, turn=turn, is_first=is_first,
            burst_triggered_count_own=self.burst_triggered_count(team),
            counter_values=dict(self._counter_values),
            **kwargs,
        )

        # 2. Fire pre-calc observers → collect modifier injections
        pre_mods = self._fire_pre_calc(ctx)

        # 3. Execute VM on the skill's effects
        vm_effects = effects if effects is not None else self._get_effects(self_skill)
        journal = vm_execute(ctx, vm_effects)

        # 4. Merge pre-calc modifiers into journal
        if pre_mods:
            journal = pre_mods + journal

        # 4.5 Register burst effects (first action = burst)
        if is_first and vm_effects:
            skill_name = getattr(self_skill, 'name', '')
            self._burst_effects[team].append((skill_name, vm_effects))
            self._burst_names[team].add(skill_name)

        # 4.6 Handle Replay mutations
        journal = self._handle_replay(journal, team, ctx)

        # 4.7 Handle Borrow mutations (skill property substitution)
        journal = self._handle_borrow(journal, ctx)

        # 4.8 Handle Redirect mutations (change damage target)
        journal = self._handle_redirect(journal)

        # 5. Apply same-skill modifiers to Damage amounts
        journal = apply_modifiers_to_journal(journal, ctx)

        # 6. Replay journal against mutable state
        replayer = JournalReplayer(
            self_sprite, opp_sprite, globals_, self.registry, team=team,
            species_lookup=species_lookup,
            self_skill=battle_skill,
        )
        events = replayer.replay(journal)

        # 6.5 Register counters from journal
        self._register_counters_from_journal(journal)

        # 6.6 Track skill history for sprite_self replay
        skill_name = getattr(self_skill, 'name', '')
        if skill_name:
            sprite_id = id(self_sprite)
            self._skill_history.setdefault(sprite_id, []).append(
                (skill_name, list(vm_effects), {"tag": getattr(self_skill, 'tag', '')})
            )

        # 7. Fire post-skill observers
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
    def _get_effects(skill) -> list:
        """Extract effects from a skill object.

        CompiledSkill.effects are tuple[SkillIROp, ...] (typed IR nodes).
        SkillRecord/dict-based effects are list[dict] in op/when format.
        The executor handles both formats.
        """
        if hasattr(skill, 'effects'):
            effs = skill.effects
            if callable(effs):
                effs = effs()
            result = list(effs)
            if result:
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

    def _register_counters_from_journal(self, journal: Journal) -> None:
        """Scan journal for CounterRegister mutations and register them."""
        for m in journal:
            if isinstance(m, CounterRegister):
                self.register_counter(m)
                # Initialize counter value for named counters
                if m.name:
                    self._counter_values.setdefault(m.name, 0)

    def _handle_borrow(self, journal: Journal, ctx: Ctx) -> Journal:
        """Handle Borrow mutations by substituting borrowed skill properties.

        When a Borrow mutation is present, replaces the current skill's
        power, type, and element with the opponent's skill properties.
        Then injects an implicit hit effect based on the borrowed properties.

        The Borrow mutation itself is removed from the journal.
        """
        from backend.vm.damage import calc_damage
        from backend.vm.journal import Borrow, Damage

        borrow_muts = [m for m in journal if isinstance(m, Borrow)]
        if not borrow_muts:
            return journal

        # Remove Borrow mutations from journal
        journal = [m for m in journal if not isinstance(m, Borrow)]

        for borrow in borrow_muts:
            if borrow.from_skill != "skill_opp_current":
                continue

            # Substitute borrowed skill properties
            borrowed_power = ctx.power_opp
            borrowed_type = ctx.skill_type_opp
            borrowed_element = ctx.element_opp

            # Only deal damage if the borrowed skill is an attack type
            if borrowed_type in ("物攻", "魔攻", "动态攻击") and borrowed_power > 0:
                # Determine atk/def based on damage type
                if borrowed_type == "物攻":
                    atk_base = ctx.atk_self
                    def_base = ctx.def_opp
                    atk_stage = ctx.stat_stages_self.get("atk", 0) * 0.1
                    def_stage = ctx.stat_stages_opp.get("def", 0) * 0.1
                else:
                    atk_base = ctx.sp_atk_self
                    def_base = ctx.sp_def_opp
                    atk_stage = ctx.stat_stages_self.get("sp_atk", 0) * 0.1
                    def_stage = ctx.stat_stages_opp.get("sp_def", 0) * 0.1

                amount = calc_damage(
                    borrowed_power, atk_base, def_base,
                    atk_stage=atk_stage,
                    def_stage=def_stage,
                    damage_reduction=ctx.damage_reduction_opp,
                    combo_count=ctx.combo_self,
                )
                journal.append(Damage(
                    target="sprite_opp",
                    amount=amount,
                    element=borrowed_element,
                    type=borrowed_type,
                ))

        return journal

    _apply_borrow = _handle_borrow  # alias for test

    def _handle_redirect(self, journal: Journal) -> Journal:
        """Handle Redirect mutations by changing Damage target.

        When a Redirect(target="sprite_self") is present, all Damage
        targeting sprite_opp is redirected to sprite_self (or vice versa).
        """
        from backend.vm.journal import Damage, Redirect

        redirect_muts = [m for m in journal if isinstance(m, Redirect)]
        if not redirect_muts:
            return journal

        target = redirect_muts[0].target  # use first redirect
        result: Journal = []
        for m in journal:
            if isinstance(m, Damage) and m.target != target:
                result.append(Damage(
                    target=target,
                    amount=m.amount,
                    element=m.element,
                    type=m.type,
                ))
            elif isinstance(m, Redirect):
                continue  # consume redirect
            else:
                result.append(m)
        return result

    def _handle_replay(self, journal: Journal, team: str, ctx: Ctx) -> Journal:
        """Handle Replay mutations by executing burst/self skill effects.

        Scans journal for Replay mutations. For team_burst replays, finds
        all registered burst effects and executes them through the VM.
        For sprite_self replays, finds matching skills in sprite history.
        The resulting mutations are prepended to the journal.
        """

        replay_muts = [m for m in journal if isinstance(m, Replay)]
        if not replay_muts:
            return journal

        extra: Journal = []
        for r in replay_muts:
            if r.from_ == "team_burst":
                for skill_name, effects in self._burst_effects.get(team, []):
                    extra.extend(vm_execute(ctx, effects))
            elif r.from_ == "sprite_self":
                extra.extend(self._collect_sprite_self_replay(ctx, r.skill_filter))

        if extra:
            journal = [m for m in journal if not isinstance(m, Replay)]
            journal = extra + journal
        return journal

    def _handle_replay_sprite_self(self, journal: Journal, sprite_id: str, ctx: Ctx) -> Journal:
        """Handle Replay from sprite_self (used by tests with explicit sprite_id)."""

        replay_muts = [m for m in journal if isinstance(m, Replay)]
        if not replay_muts:
            return journal

        extra: Journal = []
        for r in replay_muts:
            if r.from_ == "sprite_self":
                extra.extend(self._collect_sprite_self_replay(ctx, r.skill_filter))

        if extra:
            journal = [m for m in journal if not isinstance(m, Replay)]
            journal = extra + journal
        return journal

    def _collect_sprite_self_replay(self, ctx: Ctx, skill_filter: dict | None) -> Journal:
        """Collect effects from sprite skill history matching the filter.

        Filters can include: tag, skill_type, element.
        """
        accumulated: Journal = []
        # Try all sprite histories (most battles have 2 sprites)
        for sprite_id, history in self._skill_history.items():
            for skill_name, effects, tags in history:
                if skill_filter and not self._matches_skill_filter(
                    skill_name, effects, tags, skill_filter
                ):
                    continue
                accumulated.extend(vm_execute(ctx, effects))
        return accumulated

    @staticmethod
    def _matches_skill_filter(
        skill_name: str, effects: list[dict], tags: dict, skill_filter: dict
    ) -> bool:
        """Check if a historical skill matches the replay filter."""
        # tag filter
        if "tag" in skill_filter:
            if tags.get("tag", "") != skill_filter["tag"]:
                return False
        # skill_type filter
        if "skill_type" in skill_filter:
            if tags.get("skill_type", "") != skill_filter["skill_type"]:
                return False
        # element filter
        if "element" in skill_filter:
            if tags.get("element", "") != skill_filter["element"]:
                return False
        return True
