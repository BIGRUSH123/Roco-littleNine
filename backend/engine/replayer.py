"""JournalReplayer — apply VM Mutations to mutable battle state.

Pure counterpart to the VM: takes a Journal and replays each Mutation
against the mutable Sprite/GlobalEffects objects, producing side effects
and observer-triggering events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.vm.journal import (
    AbnormalChange,
    Borrow,
    Charge,
    CounterRegister,
    Damage,
    Dispel,
    Double,
    EnergyChange,
    Escape,
    Exchange,
    Heal,
    InheritEffectsMutation,
    Interrupt,
    Journal,
    LivesDelta,
    Lock,
    MarkChange,
    ModifierInjection,
    Mutation,
    Redirect,
    Replay,
    Reset,
    Return,
    ScheduleEntry,
    StatChange,
    Steal,
    TeamCounterDelta,
    Tick,
    TraitInteractionMutation,
    TransformMutation,
    WeatherSet,
)

if TYPE_CHECKING:
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite

    from .observer import ObserverRegistry


# Stats whose values are ratios (display as percentage)
_RATIO_STATS: frozenset[str] = frozenset({
    "power_mult", "damage_mult", "damage_reduction",
    "energy_cost_mult",
    "heal_reverse", "life_drain",
    "ignore_resistance", "ignore_mods", "survive",
})

# Modifier stats that should also create a visible StatusEffect.
# These are integer-count stats that players expect to see as buff/debuff icons.
# (power is excluded: values are direct power amounts with per-skill scope,
# not global sprite buffs.)
_VISIBLE_MOD_STATS: frozenset[str] = frozenset({
    "combo", "priority",
})

_STEP_PCT = 10        # 非速度六维：1步=10%
_SPEED_STEP = 10       # 速度：1步=10点

# Stage stats that can also appear as value-based ModifierInjections.
# Value → steps: non-speed: steps = int(value * 10); speed: steps = int(value / 10).
_STAGE_STATS: frozenset[str] = frozenset({
    "atk", "def", "sp_atk", "sp_def", "speed",
})

# Chinese labels for stat keys (modifiers + stage stats)
_STAT_LABELS: dict[str, str] = {
    # Stage stats (1步=10%, speed=10点)
    "atk": "物攻", "sp_atk": "魔攻", "def": "物防", "sp_def": "魔防",
    "speed": "速度",
    # Modifier stats
    "energy_cost": "能耗",
    "power": "威力",
    "combo": "连击",
    "priority": "先手",
    "power_mult": "威力倍率",
    "damage_mult": "伤害倍率",
    "damage_reduction": "减伤",
    "energy_cost_mult": "能耗倍率",
    "heal_reverse": "回复反转",
    "life_drain": "吸血",
    "ignore_resistance": "无视抗性",
    "ignore_mods": "无视修正",
    "survive": "不屈",
}

# Step unit for display conversion: steps → display value
_STEP_UNIT: dict[str, int] = {
    "power": 10, "speed": 10, "life_drain": 10,
    "priority": 1, "energy_cost": 1, "combo": 1,
}


# Stats that distribute to all BattleSkills when skill_filter="all" is used
# on a sprite-scoped target (e.g. power_mod {target: "sprite_self", skill_filter: "all"})
_SKILL_DISTRIBUTE_STATS = frozenset({"energy_cost", "power", "combo", "priority"})


_ATTACK_TYPES: frozenset[str] = frozenset({"物攻", "魔攻", "动态攻击"})


def _matches_skill_type(skill_filter: str | None, skill_type: str) -> bool:
    """Check if a skill's type matches the skill_filter."""
    if not skill_filter or skill_filter == "all":
        return True
    if skill_filter == "attack":
        return skill_type in _ATTACK_TYPES
    if skill_filter == "defense":
        return skill_type == "防御"
    if skill_filter == "status":
        return skill_type == "状态"
    return True  # unknown filters pass through


def _apply_to_all_skills(sprite, m) -> str:
    """Distribute a modifier to all BattleSkills on the sprite."""
    label = _STAT_LABELS.get(m.stat, m.stat)
    delta = m.value
    if m.stat == "energy_cost":
        delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)
    for bs in (sprite.skills or []):
        bs_mods = getattr(bs, '_modifiers', None)
        if bs_mods is None:
            continue
        cur = bs_mods.get(m.stat, 0.0)
        if m.mode == "add":
            bs_mods[m.stat] = cur + delta
        elif m.mode == "set":
            bs_mods[m.stat] = delta
        elif m.mode == "multiply":
            bs_mods[m.stat] = cur * delta if cur else delta
    if m.stat == "energy_cost":
        return ""  # energy bar shows cost visually
    return f"{sprite.name} 全技能{label}{delta:+.0f}"


def _apply_to_matching_skills(sprite, m) -> str:
    """Apply a modifier to BattleSkills matching skill_where.

    Also registers the effect in sprite._trait_direct_effects so
    reapply_all_direct_mods() can restore it after _PER_TURN_KEYS cleanup.
    """
    from backend.engine.modifiers import eval_skill_where

    label = _STAT_LABELS.get(m.stat, m.stat)
    delta = m.value
    if m.stat == "energy_cost":
        delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)
    applied = False
    for bs in (sprite.skills or []):
        bs_mods = getattr(bs, '_modifiers', None)
        if bs_mods is None:
            continue
        skill_info = {
            "name": getattr(bs, 'name', ''),
            "energy_cost": getattr(bs, 'energy_cost', 0),
            "element": getattr(getattr(bs, 'base', None), 'element', ''),
            "skill_type": getattr(getattr(bs, 'base', None), 'skill_type', ''),
        }
        if not eval_skill_where(m.skill_where, skill_info):
            continue
        st = skill_info.get("skill_type", "")
        if m.skill_filter and not _matches_skill_type(m.skill_filter, st):
            continue
        cur = bs_mods.get(m.stat, 0.0)
        if m.mode == "add":
            bs_mods[m.stat] = cur + delta
        elif m.mode == "set":
            bs_mods[m.stat] = delta
        elif m.mode == "multiply":
            bs_mods[m.stat] = cur * delta if cur else delta
        applied = True

    # Register for turn-to-turn persistence (survives _PER_TURN_KEYS cleanup)
    if applied and m.scope != "turn" and m.mode == "add":
        effect_dict = {
            "op": "power_mod",
            "attr": m.stat,
            "delta": m.value,
            "skill_where": m.skill_where,
        }
        direct_effects = getattr(sprite, '_trait_direct_effects', None)
        if direct_effects is None:
            sprite._trait_direct_effects = []
        if effect_dict not in sprite._trait_direct_effects:
            sprite._trait_direct_effects.append(effect_dict)

    if applied:
        if m.stat == "energy_cost":
            return ""
        return f"{sprite.name} {label}{delta:+.0f}"
    return ""


class JournalReplayer:
    """Replays a VM Journal against mutable battle state.

    Usage:
        replayer = JournalReplayer(self_sprite, opp_sprite, globals_, registry)
        events = replayer.replay(journal)
    """

    def __init__(
        self,
        self_sprite: Sprite,
        opp_sprite: Sprite,
        globals_: GlobalEffects,
        registry: ObserverRegistry | None = None,
        team: str = "A",
        species_lookup = None,
        self_skill = None,
        battle = None,
    ):
        self.self = self_sprite
        self.opp = opp_sprite
        self.globals = globals_
        self.registry = registry
        self.team = team  # "A" or "B"
        self._species_lookup = species_lookup  # callable(number) -> SpeciesStats | None
        self._self_skill = self_skill
        self._battle = battle  # optional ref for trait-level ops

    # ── Main entry ──

    def replay(self, journal: Journal) -> list[str]:
        """Replay all mutations. Returns event strings for logging."""
        events: list[str] = []
        for mutation in journal:
            ev = self._apply(mutation)
            if ev:
                events.append(ev)
        return events

    # ── Dispatch ──

    def _apply(self, m: Mutation) -> str:
        """Dispatch a single mutation to the appropriate handler."""
        cls = type(m).__name__

        if cls == "StatChange":
            return self._apply_stat_change(m)
        elif cls == "ModifierInjection":
            return self._apply_modifier(m)
        elif cls == "Damage":
            return self._apply_damage(m)
        elif cls == "Heal":
            return self._apply_heal(m)
        elif cls == "EnergyChange":
            return self._apply_energy_change(m)
        elif cls == "MarkChange":
            return self._apply_mark_change(m)
        elif cls == "AbnormalChange":
            return self._apply_abnormal_change(m)
        elif cls == "WeatherSet":
            return self._apply_weather_set(m)
        elif cls == "Dispel":
            return self._apply_dispel(m)
        elif cls == "Steal":
            return self._apply_steal(m)
        elif cls == "Tick":
            return self._apply_tick(m)
        elif cls == "Double":
            return self._apply_double(m)
        elif cls == "Charge":
            return self._apply_charge(m)
        elif cls == "Escape":
            return self._apply_escape(m)
        elif cls == "Return":
            return self._apply_return(m)
        elif cls == "Lock":
            return self._apply_lock(m)
        elif cls == "Interrupt":
            return self._apply_interrupt(m)
        elif cls == "Exchange":
            return self._apply_exchange(m)
        elif cls == "Reset":
            return self._apply_reset(m)
        elif cls == "Redirect":
            return self._apply_redirect(m)
        elif cls == "Replay":
            return self._apply_replay(m)
        elif cls == "Borrow":
            return self._apply_borrow(m)
        elif cls == "CounterRegister":
            return self._apply_counter_register(m)
        elif cls == "TeamCounterDelta":
            return self._apply_team_counter_delta(m)
        elif cls == "LivesDelta":
            return self._apply_lives_delta(m)
        elif cls == "ScheduleEntry":
            return self._apply_schedule_entry(m)
        elif cls == "InheritEffectsMutation":
            return self._apply_inherit_effects_mutation(m)
        elif cls == "TransformMutation":
            return self._apply_transform_mutation(m)
        elif cls == "TraitInteractionMutation":
            return self._apply_trait_interaction_mutation(m)

        return f"Unknown mutation: {cls}"

    # ── Handlers ──

    def _apply_stat_change(self, m: StatChange) -> str:
        sprite = self._target_sprite(m.target)
        from backend.sim.sprite import StatusEffect
        label = _STAT_LABELS.get(m.stat, m.stat)
        unit = _STEP_UNIT.get(m.stat, 10)
        if m.stat in ('priority', 'energy_cost', 'combo'):
            display = f'{label}{m.steps * unit:+d}' if m.steps != 0 else f'{label}{m.steps:+d}'
        elif m.stat == 'speed':
            display = f'{label}{m.steps * unit:+d}'
        else:
            display = f'{label}{m.steps * unit:+d}%'
        effect = StatusEffect(
            name=display,
            category="stat",
            stat_key=m.stat,
            steps=m.steps,
            scope=m.scope,
            source=m.source or m.name or "skill",
        )
        sprite.add_effect(effect)
        return f"{sprite.name} {display}"

    def _apply_modifier(self, m: ModifierInjection) -> str:
        """Store modifier on target sprite for later snapshot consumption.

        ModifierInjections carry values like damage_reduction, power_mult,
        combo, etc. They are stored on the sprite's _modifiers dict and
        read by build_ctx when constructing Ctx for subsequent skills.

        If on_next=True, the modifier is deferred to _pending_modifiers
        and will be consumed on the next matching skill use.
        """
        # Devotion writes to team-level player.devotion, not sprite._modifiers
        if m.stat == "devotion":
            if self._battle is None:
                return ""
            t = ("B" if self.team == "A" else "A") if m.target == "opp" else self.team
            player = self._battle.get_player(t)
            if player is None:
                return ""
            devotion_name = m.name or ""
            cur = player.devotion.get(devotion_name, 0)
            if m.mode == "set":
                player.devotion[devotion_name] = int(m.value)
            elif m.mode == "add":
                player.devotion[devotion_name] = cur + int(m.value)
            else:
                player.devotion[devotion_name] = int(m.value)
            delta = player.devotion[devotion_name] - cur
            return f"{t}队 奉献{devotion_name} {delta:+d}层"

        sprite = self._target_sprite(m.target)

        if m.on_next:
            sprite._pending_modifiers.append(m)
            return ""  # suppress verbose pending modifier log

        skill_scoped = m.target.startswith("skill_") if m.target else False

        # ── skill_filter "all" on sprite target: distribute to every BattleSkill ──
        if not skill_scoped and m.skill_filter == "all" and m.stat in _SKILL_DISTRIBUTE_STATS:
            return _apply_to_all_skills(sprite, m)

        # ── skill_where or skill_filter (attack/defense/status/...) on sprite target ──
        if not skill_scoped and (m.skill_where is not None or
                                (m.skill_filter and m.skill_filter != "all")):
            return _apply_to_matching_skills(sprite, m)

        if skill_scoped:
            if self._self_skill is not None:
                target_mods = self._self_skill._modifiers
                if m.scope == "permanent" and self._self_skill.skill:
                    skill_name = getattr(self._self_skill.skill, 'name', '')
                    if skill_name:
                        key = f"skill.{skill_name}.{m.stat}"
                        cur = sprite._modifiers.get(key, 0.0)
                        if m.mode == "set":
                            sprite._modifiers[key] = m.value
                        elif m.mode == "add":
                            sprite._modifiers[key] = cur + m.value
                        elif m.mode == "multiply":
                            sprite._modifiers[key] = (cur or 1.0) * m.value
            else:
                target_mods = sprite._modifiers
        else:
            target_mods = sprite._modifiers

        if target_mods is None:
            final = m.value
            label = _STAT_LABELS.get(m.stat, m.stat)
            if m.stat in _RATIO_STATS:
                return f"{sprite.name} {label}={final:.0%}"
            if m.stat == "energy_cost":
                return ""
            return f"{sprite.name} {label}{final:+.0f}"

        cur = target_mods.get(m.stat)
        if m.mode == "set":
            target_mods[m.stat] = m.value
        elif m.mode == "add":
            delta = m.value
            if m.stat == "energy_cost":
                delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)
            target_mods[m.stat] = (cur or 0.0) + delta
        elif m.mode == "multiply":
            target_mods[m.stat] = (cur or 1.0) * m.value if cur is not None else m.value
        else:
            target_mods[m.stat] = m.value
        final = target_mods[m.stat]

        # Track invisible modifiers for scope cleanup
        if not skill_scoped and m.scope in ("turn", "battlefield", "persistent"):
            sprite._mod_scopes[m.stat] = m.scope
        label = _STAT_LABELS.get(m.stat, m.stat)

        if not skill_scoped and m.value != 0:
            from backend.sim.sprite import StatusEffect
            create_visible = False
            steps = 0
            if m.stat in _VISIBLE_MOD_STATS:
                steps = int(m.value)
                create_visible = True
            # _STAGE_STATS (atk/def/sp_atk/sp_def/speed) are already applied
            # through _modifiers → build_ctx → atk_self/def_self/etc.
            # Creating a StatusEffect here would cause double-counting:
            # once via _modifiers and once via _extract_stat_stages.
            if create_visible and steps != 0:
                effect = StatusEffect(
                    name=f'{label}{steps:+.0f}',
                    category="stat",
                    stat_key=m.stat,
                    steps=steps,
                    scope=m.scope,
                    source=m.source or m.name or "skill",
                )
                sprite.add_effect(effect)

        if m.stat in _RATIO_STATS:
            return f"{sprite.name} {label}={final:.0%}"
        if m.stat == "energy_cost":
            return ""
        return f"{sprite.name} {label}{final:+.0f}"

    def _apply_damage(self, m: Damage) -> str:
        sprite = self._target_sprite(m.target)
        actual = sprite.take_damage(m.amount)
        result = f"{sprite.name} -{actual}HP"
        if sprite.is_fainted:
            result += " (fainted)"

        # Life drain: attacker heals by a percentage of damage dealt.
        # Check skill._modifiers first (same-execution injection), then
        # sprite._modifiers for backward compat.
        if m.target != "sprite_self":
            drain_pct = self.self._modifiers.get("life_drain", 0.0)
            if self._self_skill is not None:
                drain_pct = max(drain_pct, self._self_skill._modifiers.get("life_drain", 0.0))
            if drain_pct > 0:
                healed = self.self.heal(round(actual * drain_pct))
                if healed:
                    result += f" [吸血+{healed}HP]"
        return result

    def _apply_heal(self, m: Heal) -> str:
        sprite = self._target_sprite(m.target)
        actual = sprite.heal(m.amount)
        return f"{sprite.name} +{actual}HP"

    def _apply_energy_change(self, m: EnergyChange) -> str:
        sprite = self._target_sprite(m.target)
        if m.delta > 0:
            actual = sprite.gain_energy(m.delta)
            return f"{sprite.name} +{actual}E"
        else:
            actual = sprite.lose_energy(-m.delta)
            return f"{sprite.name} -{actual}E"

    def _apply_mark_change(self, m: MarkChange) -> str:
        """Apply, dispel, steal, or convert marks."""
        if m.action == "apply":
            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            category = self.globals.classify_mark(m.name)
            self.globals.apply_mark(team, m.name, category, m.delta)
            return f"{team}队 {m.name} {m.delta:+d}层"

        if m.action == "dispel":
            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            pos, neg = self.globals.get_marks(team)
            all_marks = pos + neg
            count = m.delta or 1
            for mark in all_marks:
                if mark.name == m.name and mark.stacks > 0:
                    removed = min(mark.stacks, count)
                    mark.stacks -= removed
                    return f"{self.self.name} 驱散{team}方{m.name}×{removed}"
            return ""

        if m.action == "steal":
            opp_team = "B" if self.team == "A" else "A"
            team = self.team if m.target_team == "own" else opp_team
            from_team = opp_team if team == self.team else self.team
            pos, neg = self.globals.get_marks(from_team)
            all_marks = pos + neg
            count = m.delta or 1
            for mark in all_marks:
                if mark.name == m.name and mark.stacks > 0:
                    removed = min(mark.stacks, count)
                    mark.stacks -= removed
                    category = self.globals.classify_mark(m.name)
                    self.globals.apply_mark(team, m.name, category, removed)
                    return f"{self.self.name} 偷取{m.name}×{removed}"
            return ""

        if m.action == "convert":
            source_name = m.source_abnormal
            if not source_name:
                return ""
            effects_list = [e for e in self.self.effects
                            if getattr(e, 'category', '') == 'abnormal'
                            and getattr(e, 'name', '') == source_name]
            total_stacks = sum(getattr(e, 'stacks', 0) for e in effects_list)
            if total_stacks <= 0:
                return ""
            marks = max(1, int(total_stacks * m.ratio))
            consumed = int(marks / m.ratio) if m.ratio > 0 else total_stacks
            for e in effects_list:
                remove_stacks = min(getattr(e, 'stacks', 0), consumed)
                e.stacks -= remove_stacks
                consumed -= remove_stacks
                if consumed <= 0:
                    break
            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            category = self.globals.classify_mark(m.name)
            self.globals.apply_mark(team, m.name, category, marks)
            return f"{self.self.name} {source_name}→{m.name}×{marks}"

        return ""

    def _apply_abnormal_change(self, m: AbnormalChange) -> str:
        sprite = self._target_sprite(m.target)
        # 萌化: trigger form devolution via apply_moe (needs species lookup)
        if m.name == '萌化' and self._species_lookup is not None and m.delta > 0:
            return self._apply_moe_via_replayer(sprite, m)
        from backend.sim.sprite import StatusEffect
        effect = StatusEffect(
            name=m.name,
            category="abnormal",
            stacks=m.delta,
            scope=m.scope,
            source="skill",
        )
        sprite.add_effect(effect)
        return f"{sprite.name} {m.name} +{m.delta}层"

    def _apply_moe_via_replayer(self, sprite: Sprite, m: AbnormalChange) -> str:
        """Apply 萌化 form devolution through the replayer path.

        Creates a minimal battle adapter so apply_moe() can look up species.
        """
        class _MoeBattle:
            def lookup_species_by_number(_self, number):
                return self._species_lookup(number)
        events = sprite.apply_moe(m.delta, _MoeBattle())
        return ' | '.join(events) if events else f"{sprite.name} {m.name} +{m.delta}层"

    def _apply_weather_set(self, m: WeatherSet) -> str:
        self.globals.set_weather(m.weather, m.turns)
        return f"天气 → {m.weather} ({m.turns}t)"

    def _apply_dispel(self, m: Dispel) -> str:
        sprite = self._target_sprite(m.target)
        if m.what == "positive":
            n = self._dispel_by_source(sprite, m.source, positive_only=True) if m.source else sprite.dispel_positive(m.limit if m.limit else -1)
            return f"{sprite.name} 驱散 {n} 增益"
        elif m.what == "negative":
            n = self._dispel_by_source(sprite, m.source, positive_only=False) if m.source else sprite.dispel_negative(m.limit if m.limit else -1)
            return f"{sprite.name} 驱散 {n} 减益"
        elif m.what == "abnormal":
            if m.name == '萌化' and self._species_lookup is not None and sprite._moe_position > 0:
                class _MoeBattle:
                    def lookup_species_by_number(_self, number):
                        return self._species_lookup(number)
                old_name = sprite.name
                removed = sprite.remove_moe(sprite._moe_position, _MoeBattle())
                return f"{old_name} 萌化解除 → 变为{sprite.name}(-{removed}层)"
            if m.source:
                n = self._remove_by_source(sprite, m.source, "abnormal")
                return f"{sprite.name} 驱散异常(source={m.source}) x{n}"
            sprite.remove_effect(m.name, "abnormal")
            return f"{sprite.name} 驱散异常 {m.name}"
        elif m.what == "mark":
            self.globals.remove_mark(
                self.team if m.target == "team_own" else ("B" if self.team == "A" else "A"),
                "positive" if self.globals.classify_mark(m.name or "") == "positive" else "negative"
            )
            return f"驱散印记 {m.name}"
        return ""

    @staticmethod
    def _dispel_by_source(sprite, source: str, positive_only: bool = True) -> int:
        """Remove effects from sprite matching the given source. Returns count removed."""
        removed = 0
        for e in list(sprite.effects):
            if getattr(e, 'category', '') == 'stat' and getattr(e, 'source', '') == source:
                if positive_only and e.steps <= 0:
                    continue
                if not positive_only and e.steps >= 0:
                    continue
                sprite.effects.remove(e)
                removed += 1
        return removed

    @staticmethod
    def _remove_by_source(sprite, source: str, category: str = '') -> int:
        """Remove effects matching source and optional category. Returns count removed."""
        removed = 0
        for e in list(sprite.effects):
            if getattr(e, 'source', '') != source:
                continue
            if category and getattr(e, 'category', '') != category:
                continue
            sprite.effects.remove(e)
            removed += 1
        return removed

    def _apply_steal(self, m: Steal) -> str:
        # Steal effects/energy/marks from target to self
        if m.what == "positive":
            target = self._target_sprite(m.from_target)
            positives = [e for e in target.effects
                         if getattr(e, 'category', '') == 'stat' and e.steps > 0]
            for e in positives:
                target.effects.remove(e)
                self.self.add_effect(e)
            return f"{self.self.name} 偷取 {len(positives)} 增益 from {target.name}"
        elif m.what == "energy":
            target = self._target_sprite(m.from_target)
            amount = m.amount or 0
            stolen = min(target.energy, amount)
            target.lose_energy(stolen)
            self.self.gain_energy(stolen)
            return f"{self.self.name} 偷取 {stolen}E from {target.name}"
        elif m.what == "mark":
            # Determine team for mark stealing
            from_team_key = "A" if m.from_target == "team_own" else "B"
            to_team_key = self.team
            name = m.name  # specific mark name, or None = all
            if name:
                # Steal specific mark: find it, remove it, apply to own team
                mark = self.globals.get_mark_by_name(from_team_key, name)
                if mark and mark.stacks > 0:
                    stacks = mark.stacks
                    category = mark.category
                    # Remove from source team's list
                    if from_team_key == "A":
                        src_list = self.globals.pos_marks_a if category == "positive" else self.globals.neg_marks_a
                    else:
                        src_list = self.globals.pos_marks_b if category == "positive" else self.globals.neg_marks_b
                    if mark in src_list:
                        src_list.remove(mark)
                    # Apply to own team
                    self.globals.apply_mark(to_team_key, name, category, stacks)
                    return f"{self.self.name} 偷取 {name} x{stacks}"
            return ""
        return ""

    def _apply_tick(self, m: Tick) -> str:
        # Trigger abnormal tick damage
        sprite = self._target_sprite(m.target)
        stacks = sprite.get_stacks(m.abnormal_name)
        if stacks > 0:
            # Simple tick: deal damage based on abnormal type
            dmg = max(1, round(sprite.max_hp * 0.03 * stacks))
            sprite.take_damage(dmg)
            return f"{sprite.name} {m.abnormal_name} tick -{dmg}HP"
        return ""

    def _apply_double(self, m: Double) -> str:
        sprite = self._target_sprite(m.target)
        if m.what == "positive":
            n = sprite.double_positive()
            return f"{sprite.name} 增益 ×2 ({n})"
        elif m.what == "negative":
            n = sprite.double_negative()
            return f"{sprite.name} 减益 ×2 ({n})"
        elif m.what == "abnormal":
            stacks = sprite.get_stacks(m.name or "")
            if stacks > 0:
                sprite.update_stacks(m.name or "", stacks * 2)
                return f"{sprite.name} {m.name} ×2"
        return ""

    def _apply_charge(self, m: Charge) -> str:
        sprite = self._target_sprite(m.target)
        from backend.sim.sprite import StatusEffect
        sprite.add_effect(StatusEffect(
            name="charging", category="state", scope="persistent", source="skill",
        ))
        return f"{sprite.name} 开始蓄力"

    def _apply_escape(self, m: Escape) -> str:
        # Engine handles escape via battle turn logic
        return f"{m.target} 脱离 (inherit={m.inherit}, urgent={m.urgent})"

    def _apply_return(self, m: Return) -> str:
        sprite = self._target_sprite(m.target)
        sprite.pending_return = True
        return f"{sprite.name} 准备返场"

    def _apply_lock(self, m: Lock) -> str:
        sprite = self._target_sprite(m.target)
        sprite.locked_turns = m.turns
        return f"{sprite.name} 锁定 {m.turns}t"

    def _apply_interrupt(self, m: Interrupt) -> str:
        sprite = self._target_sprite(m.target)
        sprite.interrupted = True
        return f"{sprite.name} 被打断"

    def _apply_exchange(self, m: Exchange) -> str:
        if m.what == "hp_ratio":
            self.self.current_hp, self.opp.current_hp = \
                round(self.opp.current_hp / self.opp.max_hp * self.self.max_hp) if self.opp.max_hp else 0, \
                round(self.self.current_hp / self.self.max_hp * self.opp.max_hp) if self.self.max_hp else 0
            return "交换HP比例"
        elif m.what == "effects":
            self.self.effects, self.opp.effects = self.opp.effects, self.self.effects
            return "交换增益减益"
        elif m.what == "skills":
            self.self.skills, self.opp.skills = self.opp.skills, self.self.skills
            return "交换技能"
        elif m.what == "adjacent_skills":
            self._swap_adjacent_skills(self.self)
            return "交换相邻技能位置"
        return ""

    def _apply_reset(self, m: Reset) -> str:
        # Reset a stat mod to base — engine handles this for skill slots
        return f"重置 {m.stat}"

    def _apply_redirect(self, m: Redirect) -> str:
        # Set redirect flag on self — engine reads this in _handle_redirect
        self.self._redirect_target = m.target
        return f"伤害重定向 → {m.target}"

    def _apply_replay(self, m: Replay) -> str:
        # Engine needs to find historical skills and replay them
        return f"重放技能 from={m.from_}"

    def _apply_borrow(self, m: Borrow) -> str:
        return f"借用技能 from={m.from_skill}"

    def _apply_team_counter_delta(self, m: TeamCounterDelta) -> str:
        """Write to a team-level counter. Requires battle reference."""
        if self._battle is None:
            return ""
        t = ("B" if self.team == "A" else "A") if m.target == "opp" else self.team
        self._battle.inc_team_counter(t, m.key, m.delta)
        return ""

    def _apply_lives_delta(self, m: LivesDelta) -> str:
        """Modify player lives. Requires battle reference."""
        if self._battle is None:
            return ""
        t = ("B" if self.team == "A" else "A") if m.target_team == "opp" else self.team
        player = self._battle.get_player(t)
        if player is None:
            return ""
        if m.delta < 0 and player.lives <= 0:
            return ""
        player.lives += m.delta
        label = f"奉献{m.delta}" if m.delta > 0 else f"魔力{m.delta}"
        return f"{self.self.name} {label}" if self.self else ""

    def _apply_schedule_entry(self, m: ScheduleEntry) -> str:
        """Register delayed effects. Requires battle reference."""
        if self._battle is None:
            return ""
        self._battle.scheduled_effects.append({
            'turn': self._battle.turn + m.turns,
            'phase': m.at,
            'effects': m.then,
            'source': self.self,
            'ctx_snapshot': {'team': self.team, 'target': 'self'},
        })
        return f"{self.self.name}: 延时效果({m.turns}回合后)" if self.self else ""

    def _apply_inherit_effects_mutation(self, m: InheritEffectsMutation) -> str:
        """Transfer effects between sprites. Requires battle reference."""
        if self._battle is None:
            return ""
        source_sprite = self.self if m.source_key == "self" else self.opp
        if source_sprite is None:
            return ""
        inherited = [e for e in source_sprite.effects if getattr(e, 'scope', '') == m.scope]
        if not inherited:
            return ""
        if m.via_pending:
            self._battle.pending_effects.setdefault(self.team, [])
            self._battle.pending_effects[self.team].extend(inherited)
            return f"{source_sprite.name}→next({self.team}) 继承{len(inherited)}个效果"
        else:
            target_sprite = self.opp if m.target_key == "enemy_new" else self.self
            if target_sprite is None:
                return ""
            for e in inherited:
                target_sprite.add_effect(e)
            return f"{source_sprite.name}→{target_sprite.name} 继承{len(inherited)}个效果"

    def _apply_transform_mutation(self, m: TransformMutation) -> str:
        """Transform a sprite's species. Requires battle reference for species lookup."""
        if self._battle is None:
            return ""
        from backend.common.models import SpeciesStats
        sprite = self.self
        new_species = self._battle.lookup_species(m.species)
        if new_species is None:
            s = sprite.species
            new_species = SpeciesStats(
                name=m.species, form='',
                hp=s.hp, atk=s.atk, sp_atk=s.sp_atk,
                def_=s.def_, sp_def=s.sp_def, speed=s.speed,
                attributes=s.attributes, ability=s.ability,
            )
        skill_names = list(m.skills) if m.skills else []
        new_skills = self._battle.build_skills(skill_names) if skill_names else []
        if m.reset_hp:
            sprite.current_hp = sprite.max_hp
        if m.reset_energy:
            sprite.energy = getattr(sprite, 'max_energy', 10)
        result = sprite.transform(new_species, new_skills) if hasattr(sprite, 'transform') else f"{sprite.name} → {m.species}"
        if isinstance(result, list):
            return ' | '.join(str(x) for x in result)
        return result

    def _apply_trait_interaction_mutation(self, m: TraitInteractionMutation) -> str:
        """Suppress, remove, or copy a trait on a sprite."""
        target = self.self if m.target in ("sprite_self", "self") else self.opp
        if target is None:
            return ""
        if m.action == 'suppress':
            target._trait_suppressed = True
            target._trait_handler = None
            return f'{target.name} 特性被压制'
        if m.action == 'remove':
            target._trait_suppressed = True
            target._trait_handler = None
            if m.new_ability:
                target.species.ability = m.new_ability
                target._trait_suppressed = False
                target._trait_handler = None
                return f'{target.name} 特性变为 {m.new_ability}'
            return f'{target.name} 特性被移除'
        if m.action == 'copy':
            source = self.opp if m.copy_from == "sprite_opp" else self.self
            if source is None or target is source:
                return ""
            source_ability = source.species.ability
            if not source_ability:
                return ""
            target.species.ability = source_ability
            target._trait_handler = None
            target._trait_suppressed = False
            return f'{target.name} 复制特性 → {source_ability}'
        return ""

    def _apply_counter_register(self, m: CounterRegister) -> str:
        # Counter registration is handled by _register_counters_from_journal
        # in battle.py → register_counter(). The replayer's job here is just
        # to produce the verbose log entry (named counters only).
        if self.registry and m.name and m.name.strip():
            return f"注册计次器: {m.name}"
        return ""

    # ── Helpers ──

    def _target_sprite(self, target: str) -> Sprite:
        if target in ("sprite_self", "self", "team_own", "skill_off_0"):
            return self.self
        return self.opp

    @staticmethod
    def _swap_adjacent_skills(sprite: Sprite) -> None:
        """Swap the current skill with its adjacent neighbors (left and right).

        For skills at positions 0 and 3 (edge cases), only swap the available side.
        """
        skills = sprite.skills
        if not skills or len(skills) < 2:
            return
        n = len(skills)
        # Find current skill position (the one being used)
        # In a typical 4-skill layout, swap positions 1<->2 for symmetry
        # Simplified: swap all adjacent pairs (0<->1, 2<->3)
        for i in range(0, n - 1, 2):
            skills[i], skills[i + 1] = skills[i + 1], skills[i]
