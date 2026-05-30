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
from .trait_loader import TraitLoader

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



def _is_positive_modifier(m) -> bool:
    """Check if a ModifierInjection represents a beneficial change."""
    if m.mode == "add":
        if m.stat == "energy_cost":
            return m.value < 0
        return m.value > 0
    if m.mode == "set":
        return False
    if m.mode == "multiply":
        return m.value > 1.0
    return False


class BattleVMEngine:
    """VM-powered skill execution engine.

    Can be embedded in the existing Battle class or used standalone.
    """

    def __init__(self, registry: ObserverRegistry | None = None):
        self.registry = registry if registry is not None else ObserverRegistry()
        self.trait_loader = TraitLoader(self.registry)  # IR_RISC trait pipeline
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
        # 0. Extract engine-only kwargs (not for build_ctx)
        battle = kwargs.pop('battle', None)

        # 1. Build Ctx snapshot
        ctx = build_ctx(
            self_sprite, opp_sprite,
            self_skill, opp_skill, globals_,
            team=team, turn=turn, is_first=is_first,
            burst_triggered_count_own=self.burst_triggered_count(team),
            counter_values=dict(self._counter_values),
            battle_skill=battle_skill,
            **kwargs,
        )

        # 2. Fire pre-calc observers → apply to sprite → rebuild ctx
        # so trait buffs like专注力's atk+100% are visible in the snapshot
        pre_calc_mods = self._fire_pre_calc(ctx, id(self_sprite))
        # Also fire defender's pre_calc observers (e.g. 完全偏振) —
        # uses attacker's ctx so conditions referencing sprite_opp resolve
        # to the defending sprite correctly.
        pre_calc_mods.extend(self._fire_pre_calc(ctx, id(opp_sprite)))
        pre_calc_events: list[str] = []
        if pre_calc_mods:
            from .replayer import JournalReplayer as _JR
            _pre_r = _JR(self_sprite, opp_sprite, globals_, self.registry, team=team, battle=battle)
            pre_calc_events = _pre_r.replay(pre_calc_mods)
            ctx = build_ctx(
                self_sprite, opp_sprite, self_skill, opp_skill, globals_,
                team=team, turn=turn, is_first=is_first,
                burst_triggered_count_own=self.burst_triggered_count(team),
                counter_values=dict(self._counter_values),
                battle_skill=battle_skill,
                **kwargs,
            )
        # pre_calc_mods already applied to sprite via _pre_r.replay above
        pre_mods = self._fire_pre_event("pre_modifier", ctx, id(self_sprite))

        # 3. Execute VM on the skill's effects
        vm_effects = effects if effects is not None else self._get_effects(self_skill)
        journal = vm_execute(ctx, vm_effects)

        # 4. Merge pre-calc modifiers into journal
        if pre_mods:
            journal = pre_mods + journal

        # 4.1 Fire pre_defend observers (L1→L2: defender's trait)
        pre_defend_mods = self._fire_pre_event("pre_defend", ctx, id(opp_sprite))
        if pre_defend_mods:
            journal = pre_defend_mods + journal

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
            battle=battle,
        )
        events = pre_calc_events + replayer.replay(journal)

        # 6.5 Register counters from journal
        self._register_counters_from_journal(journal, self_sprite)

        # 6.6 Track skill history for sprite_self replay
        skill_name = getattr(self_skill, 'name', '')
        if skill_name:
            sprite_id = id(self_sprite)
            self._skill_history.setdefault(sprite_id, []).append(
                (skill_name, list(vm_effects), {"tag": getattr(self_skill, 'tag', '')})
            )

        # 6.7 Trigger starfall mark: non-幻系 attack → consume marks + deal 幻系 damage
        skill_type = getattr(self_skill, 'skill_type', '')
        skill_element = getattr(self_skill, 'element', '')
        if skill_type in ('物攻', '魔攻') and skill_element != '幻' and globals_:
            opp_team = 'B' if team == 'A' else 'A'
            sf_dmg = globals_.trigger_starfall(opp_team, self_sprite, opp_sprite)
            if sf_dmg > 0:
                events.append(f'星陨印记引爆: {opp_sprite.name} -{sf_dmg}HP')

        # 7. Fire post-skill observers
        ctx.just_acted_self = True  # for sprite_acted condition
        post_ev = self._fire_post_event("post_skill", ctx, replayer)
        events.extend(post_ev)

        # 8. Fire mutation-driven observers (damage, KO, energy, abnormal, etc.)
        post_mut_ev = self._fire_mutation_events(journal, ctx, replayer)
        events.extend(post_mut_ev)

        return SkillExecutionResult(ctx, journal, events)

    def execute_effects(
        self,
        ctx: Ctx,
        effects: list[dict],
    ) -> Journal:
        """Execute raw effects through the VM (used for observer then-blocks)."""
        return vm_execute(ctx, effects)

    # ── Observer integration ──

    def _fire_pre_calc(self, ctx: Ctx, sprite_id: int = 0) -> Journal:
        """Fire pre-calculation observers and collect their mutations."""
        return self._fire_pre_event("pre_calc", ctx, sprite_id)

    def _fire_pre_event(self, trigger: str, ctx: Ctx, sprite_id: int = 0) -> Journal:
        """Fire pre-execution observers and collect their mutations.

        Pre-execution observers run BEFORE the VM and inject modifier
        effects into the pipeline. Unlike _fire_post_event, this does
        not replay mutations — it only collects them for later replay.

        Observers are filtered by listen set and owner sprite.
        """
        mutations: Journal = []
        for obs in self.registry._observers:
            if obs.listen and trigger not in obs.listen:
                continue
            # Owner filter: observers with an owner only fire for their sprite
            if obs.owner_sprite_id is not None and sprite_id != 0 and obs.owner_sprite_id != sprite_id:
                continue
            try:
                if eval_one(ctx, obs.cond):
                    then = self._inject_source(obs.then, obs.source) if obs.source else obs.then
                    result = process_effects(ctx, then)
                    mutations.extend(result)
            except Exception:
                continue
        return mutations

    def _inject_source(self, effects: list[dict], source: str) -> list[dict]:
        """Inject source into effects that don't have their own.

        Recursively handles when/then/else nesting so observer child effects
        carry the observer's source for trait tooltip display.

        Handles both raw dict effects and already-compiled typed IR objects.
        """
        import copy

        from backend.vm.ir_skill import WhenBlock
        result = []
        for eff in effects:
            if isinstance(eff, dict):
                eff = copy.copy(eff)
                if "op" in eff and "source" not in eff:
                    eff["source"] = source
                if isinstance(eff.get("then"), list):
                    eff["then"] = self._inject_source(eff["then"], source)
                if isinstance(eff.get("else"), list):
                    eff["else"] = self._inject_source(eff["else"], source)
            elif isinstance(eff, WhenBlock):
                eff = copy.copy(eff)
                eff.then = tuple(self._inject_source(list(eff.then), source))
                if eff.else_:
                    eff.else_ = tuple(self._inject_source(list(eff.else_), source))
            result.append(eff)
        return result

    def _inject_default_scope(self, effects: list[dict], scope: str) -> list[dict]:
        """Inject a default scope into effects that don't have their own.

        Recursively handles when/then/else nesting so observer child effects
        inherit the observer's scope (e.g. persistent → mult_mod survives
        _PER_TURN_KEYS cleanup).

        Handles both raw dict effects and already-compiled typed IR objects.
        """
        import copy

        from backend.vm.ir_skill import WhenBlock
        result = []
        for eff in effects:
            if isinstance(eff, dict):
                eff = copy.copy(eff)
                if "op" in eff and "scope" not in eff:
                    eff["scope"] = scope
                if isinstance(eff.get("then"), list):
                    eff["then"] = self._inject_default_scope(eff["then"], scope)
                if isinstance(eff.get("else"), list):
                    eff["else"] = self._inject_default_scope(eff["else"], scope)
            elif isinstance(eff, WhenBlock):
                eff = copy.copy(eff)
                eff.then = tuple(self._inject_default_scope(list(eff.then), scope))
                if eff.else_:
                    eff.else_ = tuple(self._inject_default_scope(list(eff.else_), scope))
            result.append(eff)
        return result

    def _fire_post_event(self, trigger: str, ctx: Ctx, replayer: JournalReplayer) -> list[str]:
        """Fire post-event observers and replay their mutations.

        Observers are filtered by listen set. For per-sprite triggers
        (entry/leave/turn_end/post_abnormal_tick), owner filtering ensures
        each sprite's observers only fire for their own events.
        """
        events: list[str] = []
        owner_id = id(replayer.self) if replayer.self else None
        for obs in self.registry._observers:
            if obs.listen and trigger not in obs.listen:
                continue
            # Owner filter: only for triggers where "which sprite" matters.
            # turn_end and post_abnormal_tick are fired per-sprite in a loop,
            # so sprite-owned observers must only fire for their owner.
            # post_ko: allow observers owned by EITHER the fainted sprite (self)
            # or the killer (opp). For killer-owned observers, perspective swap
            # below re-orients replayer.self → killer before condition eval.
            if trigger == "post_ko":
                opp_id = id(replayer.opp) if replayer.opp else None
                if obs.owner_sprite_id is not None:
                    if obs.owner_sprite_id != owner_id and obs.owner_sprite_id != opp_id:
                        continue
            elif trigger in ("post_entry", "post_leave", "post_skill",
                           "turn_end", "post_abnormal_tick", "turn_start",
                           "post_energy_change", "post_counter",
                           "post_enemy_leave", "post_charge",
                           "post_heal"):
                if obs.owner_sprite_id is not None and owner_id is not None:
                    if obs.owner_sprite_id != owner_id:
                        continue
            # post_damage / post_ko: if owner is the defender / killer (not the
            # replayer.self), swap replayer AND ctx BEFORE condition evaluation
            # so conds like on_damage_taken / on_ko resolve from owner's perspective
            # and stat reads (atk_self, def_opp) use correct values.
            saved_perspective = None
            if trigger in ("post_damage", "post_ko") and obs.owner_sprite_id is not None:
                opp_id = id(replayer.opp) if replayer.opp else None
                if obs.owner_sprite_id == opp_id:
                    saved_perspective = (
                        replayer.self, replayer.opp,
                        ctx,
                    )
                    replayer.self, replayer.opp = saved_perspective[1], saved_perspective[0]
                    ctx = ctx.swapped_view()
                    # flip damage_taken_of on the swapped ctx too
                    if ctx.event.damage_taken_of == "sprite_opp":
                        ctx.event.damage_taken_of = "sprite_self"
                    elif ctx.event.damage_taken_of == "sprite_self":
                        ctx.event.damage_taken_of = "sprite_opp"
            try:
                if eval_one(ctx, obs.cond):
                    then = self._inject_default_scope(obs.then, obs.scope)
                    if obs.source:
                        then = self._inject_source(then, obs.source)
                    journal = process_effects(ctx, then)
                    replayer._trait_sourcing = True
                    ev = replayer.replay(journal)
                    replayer._trait_sourcing = False
                    events.extend(ev)
                    # Escape triggered — stop processing more observers
                    # for this sprite (battle state changed)
                    if replayer._battle and replayer._battle.pending_escape:
                        break
            except Exception:
                continue
            finally:
                if saved_perspective is not None:
                    replayer.self, replayer.opp = saved_perspective[0], saved_perspective[1]
                    ctx = saved_perspective[2]
        return events

    def _fire_mutation_events(self, journal: Journal, ctx: Ctx, replayer: JournalReplayer) -> list[str]:
        """Fire observers for mutation-driven trigger points.

        Scans the journal for specific mutation types and fires the
        corresponding observer trigger points. Handles:
          - Damage → post_damage
          - KO (target_fainted) → post_ko
          - EnergyChange → post_energy_change
          - AbnormalChange → post_abnormal_change / post_abnormal_apply
          - ModifierInjection(positive) → post_positive_change
        """
        from backend.vm.journal import (
            AbnormalChange,
            Damage,
            EnergyChange,
            Heal,
            ModifierInjection,
            StatChange,
        )

        events: list[str] = []
        fired: set[str] = set()  # deduplicate triggers per execution

        for m in journal:
            trigger = None
            if isinstance(m, Damage):
                trigger = "post_damage"
                ctx.damage_taken_this_turn = m.amount
                ctx.event.damage_taken_of = "sprite_self" if m.target in ("sprite_self",) else "sprite_opp"
            elif isinstance(m, EnergyChange):
                target_of = "sprite_self" if m.target in ("sprite_self",) else "sprite_opp"
                ctx.event.energy_changed_of = target_of
                ctx.event.skills_energy_changed_of = target_of
                if target_of == "sprite_self":
                    ctx.energy_delta_self = getattr(replayer, '_energy_deltas', {}).get(id(m), m.delta)
                else:
                    ctx.energy_delta_self = 0
                trigger = "post_energy_change"
            elif isinstance(m, Heal):
                target_of = "sprite_self" if m.target in ("sprite_self",) else "sprite_opp"
                ctx.event.heal_of = target_of
                if target_of == "sprite_self":
                    ctx.heal_delta_self = m.amount
                else:
                    ctx.heal_delta_opp = m.amount
                trigger = "post_heal"
            elif isinstance(m, AbnormalChange):
                trigger = "post_abnormal_change"
                # Set event context for condition matching
                ctx.event.abnormal_changed_name = m.name
                ctx.event.abnormal_changed_target = "sprite_self" if m.target == "sprite_self" else "sprite_opp"
                # Also fire post_abnormal_apply if stacks increased
                if getattr(m, 'delta', 0) > 0:
                    apply_trigger = "post_abnormal_apply"
                    ctx.event.abnormal_applied_name = m.name
                    ctx.event.abnormal_applied_target = ctx.event.abnormal_changed_target
                    if apply_trigger not in fired:
                        fired.add(apply_trigger)
                        apply_ev = self._fire_post_event(apply_trigger, ctx, replayer)
                        events.extend(apply_ev)
            elif isinstance(m, StatChange):
                if getattr(m, 'is_positive', False):
                    trigger = "post_positive_change"
                    ctx.event.positive_changed_of = "sprite_self" if m.target in ("sprite_self",) else "sprite_opp"
                    ctx.event.positive_changed_stat = m.stat
                    ctx.event.positive_changed_steps = m.steps
            elif isinstance(m, ModifierInjection):
                if _is_positive_modifier(m):
                    trigger = "post_positive_change"
                    ctx.event.positive_changed_of = "sprite_self" if m.target in ("sprite_self",) else "sprite_opp"

            if trigger and trigger not in fired:
                fired.add(trigger)
                ev = self._fire_post_event(trigger, ctx, replayer)
                events.extend(ev)

        # post_ko: check if the target fainted from damage
        for m in journal:
            if isinstance(m, Damage):
                target_sprite = replayer.opp if m.target == "sprite_opp" else replayer.self
                if target_sprite and target_sprite.is_fainted:
                    if "post_ko" not in fired:
                        fired.add("post_ko")
                        ctx.event.target_fainted = True
                        ev = self._fire_post_event("post_ko", ctx, replayer)
                        events.extend(ev)
                    break

        return events

    def fire_trigger(
        self,
        trigger: str,
        ctx: Ctx,
        self_sprite: Sprite,
        opp_sprite: Sprite,
        globals_,
        *,
        team: str = "A",
        species_lookup = None,
        self_skill = None,
        battle = None,
        leaving_sprite: Sprite | None = None,
    ) -> list[str]:
        """Public hook: fire a trigger point and replay results as events.

        Callable from sim/battle.py at dispatch points (entry, leave,
        turn_end, counter_success, etc.) where the engine's internal
        execute_skill() pipeline is not active.
        """
        replayer = JournalReplayer(
            self_sprite, opp_sprite, globals_, self.registry, team=team,
            species_lookup=species_lookup,
            self_skill=self_skill,
            battle=battle,
            leaving_sprite=leaving_sprite,
        )
        return self._fire_post_event(trigger, ctx, replayer)

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

    def register_counter(self, mutation: CounterRegister, owner_sprite=None) -> None:
        """Register a persistent counter from a CounterRegister mutation.

        Deduplicates: if an observer with the same cond+then+owner already
        exists, skips registration.
        """
        from backend.vm.cond import infer_triggers

        from .observer import Observer
        owner_id = id(owner_sprite) if owner_sprite else None
        # Check for duplicate
        for obs in self.registry._observers:
            if (obs.cond == mutation.cond
                    and obs.then == mutation.then
                    and obs.owner_sprite_id == owner_id):
                return  # already registered
        self.registry.register(Observer(
            cond=mutation.cond,
            then=mutation.then,
            scope=mutation.scope,
            name=mutation.name or "",
            source="counter",
            listen=infer_triggers(mutation.cond),
            threshold=mutation.threshold,
            reset_on_fire=mutation.reset_on_fire,
            owner_sprite_id=owner_id,
        ))

    def _register_counters_from_journal(self, journal: Journal, owner_sprite=None) -> None:
        """Scan journal for CounterRegister mutations and register them."""
        for m in journal:
            if isinstance(m, CounterRegister):
                self.register_counter(m, owner_sprite)
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
                for _skill_name, effects in self._burst_effects.get(team, []):
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
        for _sprite_id, history in self._skill_history.items():
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
        if "tag" in skill_filter and tags.get("tag", "") != skill_filter["tag"]:
            return False
        # skill_type filter
        if "skill_type" in skill_filter and tags.get("skill_type", "") != skill_filter["skill_type"]:
            return False
        # element filter
        return not ("element" in skill_filter and tags.get("element", "") != skill_filter["element"])
