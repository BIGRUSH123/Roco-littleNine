"""JournalReplayer — apply VM Mutations to mutable battle state.

Pure counterpart to the VM: takes a Journal and replays each Mutation
against the mutable Sprite/GlobalEffects objects, producing side effects
and observer-triggering events.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from backend.vm.journal import (
    StatChange, ModifierInjection, Damage, Heal, EnergyChange,
    MarkChange, AbnormalChange, WeatherSet, Dispel, Steal, Tick,
    Double, Charge, Escape, Return, Lock, Interrupt, Exchange,
    Reset, Redirect, Replay, Borrow, CounterRegister,
    Mutation, Journal,
)

if TYPE_CHECKING:
    from sim.sprite import Sprite, StatusEffect
    from sim.globals import GlobalEffects
    from .observer import ObserverRegistry


# Stats whose values are ratios (display as percentage)
_RATIO_STATS: frozenset[str] = frozenset({
    "power_mult", "damage_mult", "damage_reduction",
    "energy_cost_mult",
    "heal_reverse", "life_drain",
    "ignore_resistance", "ignore_mods", "survive",
})


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
    ):
        self.self = self_sprite
        self.opp = opp_sprite
        self.globals = globals_
        self.registry = registry
        self.team = team  # "A" or "B"

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

        return f"Unknown mutation: {cls}"

    # ── Handlers ──

    def _apply_stat_change(self, m: StatChange) -> str:
        sprite = self._target_sprite(m.target)
        from sim.sprite import StatusEffect
        effect = StatusEffect(
            name=f"{m.stat}+{m.steps}",
            category="stat",
            stat_key=m.stat,
            steps=m.steps,
            scope=m.scope,
            source=m.name or "skill",
        )
        sprite.add_effect(effect)
        return f"{sprite.name} {m.stat} {m.steps:+d}步"

    def _apply_modifier(self, m: ModifierInjection) -> str:
        """Store modifier on target sprite for later snapshot consumption.

        ModifierInjections carry values like damage_reduction, power_mult,
        combo, etc. They are stored on the sprite's _modifiers dict and
        read by build_ctx when constructing Ctx for subsequent skills.

        If on_next=True, the modifier is deferred to _pending_modifiers
        and will be consumed on the next matching skill use.
        """
        sprite = self._target_sprite(m.target)

        if m.on_next:
            sprite._pending_modifiers.append(m)
            return f"{sprite.name} {m.stat} pending (on_next, if_type={m.if_type})"

        cur = sprite._modifiers.get(m.stat)  # None if never set (distinct from 0.0)
        if m.mode == "set":
            sprite._modifiers[m.stat] = m.value
        elif m.mode == "add":
            sprite._modifiers[m.stat] = (cur or 0.0) + m.value
        elif m.mode == "multiply":
            sprite._modifiers[m.stat] = (cur or 1.0) * m.value if cur is not None else m.value
        else:
            sprite._modifiers[m.stat] = m.value
        final = sprite._modifiers[m.stat]
        if m.stat in _RATIO_STATS:
            return f"{sprite.name} {m.stat}={final:.0%}"
        return f"{sprite.name} {m.stat}={final:+.0f}"

    def _apply_damage(self, m: Damage) -> str:
        sprite = self._target_sprite(m.target)
        actual = sprite.take_damage(m.amount)
        result = f"{sprite.name} -{actual}HP"
        if sprite.is_fainted:
            result += " (fainted)"

        # Life drain: attacker heals by a percentage of damage dealt
        if m.target != "sprite_self":
            drain_pct = self.self._modifiers.get("life_drain", 0.0)
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
        team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
        category = self.globals.classify_mark(m.name)
        self.globals.apply_mark(team, m.name, category, m.delta)
        return f"{team}队 {m.name} {m.delta:+d}层"

    def _apply_abnormal_change(self, m: AbnormalChange) -> str:
        sprite = self._target_sprite(m.target)
        from sim.sprite import StatusEffect
        effect = StatusEffect(
            name=m.name,
            category="abnormal",
            stacks=m.delta,
            scope="battlefield",
            source="skill",
        )
        sprite.add_effect(effect)
        return f"{sprite.name} {m.name} +{m.delta}层"

    def _apply_weather_set(self, m: WeatherSet) -> str:
        self.globals.set_weather(m.weather, m.turns)
        return f"天气 → {m.weather} ({m.turns}t)"

    def _apply_dispel(self, m: Dispel) -> str:
        sprite = self._target_sprite(m.target)
        if m.what == "positive":
            n = sprite.dispel_positive(m.limit if m.limit else -1)
            return f"{sprite.name} 驱散 {n} 增益"
        elif m.what == "negative":
            n = sprite.dispel_negative(m.limit if m.limit else -1)
            return f"{sprite.name} 驱散 {n} 减益"
        elif m.what == "abnormal":
            # Remove abnormal by name
            sprite.remove_effect(m.name, "abnormal")
            return f"{sprite.name} 驱散异常 {m.name}"
        elif m.what == "mark":
            self.globals.remove_mark(
                self.team if m.target == "team_own" else ("B" if self.team == "A" else "A"),
                "positive" if self.globals.classify_mark(m.name or "") == "positive" else "negative"
            )
            return f"驱散印记 {m.name}"
        return ""

    def _apply_steal(self, m: Steal) -> str:
        # Steal effects/energy/marks from target to self
        if m.what == "positive":
            target = self._target_sprite(m.from_target)
            from sim.sprite import StatusEffect
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
        from sim.sprite import StatusEffect
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

    def _apply_counter_register(self, m: CounterRegister) -> str:
        if self.registry:
            from .observer import Observer
            self.registry.register(Observer(
                cond=m.cond,
                then=m.then,
                scope=m.scope,
                name=m.name or "",
                source="counter",
            ))
            return f"注册计次器 {m.name or '(匿名)'}"
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
