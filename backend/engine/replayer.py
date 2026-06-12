"""JournalReplayer — apply VM Mutations to mutable battle state.

Pure counterpart to the VM: takes a Journal and replays each Mutation
against the mutable Sprite/GlobalEffects objects, producing side effects
and observer-triggering events.
"""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING

from backend.vm.journal import (
    AbnormalChange,
    Borrow,
    BurstGrant,
    Charge,
    CounterRegister,
    Damage,
    Dispel,
    Double,
    EffectDelta,
    EnergyChange,
    Escape,
    Exchange,
    GainSkillsMutation,
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

# Ratio stats where the modifier stores the TOTAL (1.0 + bonus), not the bonus.
# When creating display effects, we convert: display_mult = delta - 1.0.
_TOTAL_BASED_RATIO_STATS: frozenset[str] = frozenset({
    "power_mult", "damage_mult",
})

# Modifier stats that should also create a visible StatBuffEffect.
# These are integer-count stats that players expect to see as buff/debuff icons.
# (power is excluded: values are direct power amounts with per-skill scope,
# not global sprite buffs.)
_VISIBLE_MOD_STATS: frozenset[str] = frozenset({
    "combo", "priority", "life_drain", "power_mod",
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
    "combo_set": "连击固定",
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
    "drive": "传动",
    "power_mod": "威力",
    "swift": "迅捷",
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


def _apply_to_all_skills(sprite, m, replayer=None) -> str:
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
        # Permanent scope: persist to sprite._modifiers so load_permanent_mods()
        # can restore after _SKILL_PER_TURN_KEYS cleanup each turn.
        if m.scope == "permanent" and bs.name:
            key = f"skill.{bs.name}.{m.stat}"
            if m.mode == "add":
                sprite._modifiers[key] = bs_mods[m.stat]
            else:
                sprite._modifiers[key] = bs_mods[m.stat]
    # Create display-only effect for trait tooltip (energy_cost, combo, priority, etc.)
    source = m.source or ""
    if source and replayer is not None:
        replayer._sync_mult_display_effect(
            sprite, m.stat, 0.0, m.scope, source,
            display_value=float(delta))
    if m.stat == "energy_cost":
        return f"{sprite.name} 全技能能耗{delta:+.0f}"
    return f"{sprite.name} 全技能{label}{delta:+.0f}"


def _apply_to_matching_skills(sprite, m, mark_energy_mod: int = 0, replayer=None) -> str:
    """Apply a modifier to BattleSkills matching skill_where.

    Also registers the effect in sprite._trait_direct_effects so
    reapply_all_direct_mods() can restore it after _PER_TURN_KEYS cleanup.

    mark_energy_mod: team-level mark energy reduction (not in bs.energy_cost property).
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
            "energy_cost": max(0, getattr(bs, 'energy_cost', 0) - mark_energy_mod),
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
    if applied and m.scope != "turn" and m.mode in ("add", "set"):
        effect_dict = {
            "op": "power_mod",
            "attr": m.stat,
            "delta": m.value,
            "mode": m.mode,
            "skill_where": m.skill_where,
            "skill_filter": m.skill_filter,
            "source": m.source,
        }
        if not m.skill_filter:
            del effect_dict["skill_filter"]
        if not m.source:
            del effect_dict["source"]
        if m.ttl > 0:
            effect_dict["ttl"] = m.ttl
        direct_effects = getattr(sprite, '_trait_direct_effects', None)
        if direct_effects is None:
            sprite._trait_direct_effects = []
        if effect_dict not in sprite._trait_direct_effects:
            sprite._trait_direct_effects.append(effect_dict)
        if replayer is not None and replayer._battle is not None:
            replayer._battle._vm_engine.trait_loader._direct_mod_sprite_ids.add(id(sprite))

    if applied:
        source = m.source or ""
        # Create display-only StatBuffEffect for trait tooltip
        if source:
            from backend.vm.effect import _STAT_LABELS as _EFF_LABELS
            from backend.vm.effect import StatBuffEffect
            eff_name = _EFF_LABELS.get(m.stat, m.stat)
            existing = next(
                (e for e in getattr(sprite, 'active_effects', [])
                 if isinstance(e, StatBuffEffect) and e.stat_key == m.stat
                 and e.source == source and e.steps == 0),
                None,
            )
            if m.stat == "energy_cost":
                # Absolute value: show cost reduction (negative delta = reduction)
                display_val = float(delta)
                if existing is not None:
                    existing.display_value = display_val
                    existing.scope = m.scope or "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source,
                            scope=m.scope or "battlefield",
                            stat_key=m.stat, steps=0,
                            display_value=display_val,
                            ttl=m.ttl,
                        ))
                return ""  # energy bar shows cost visually
            elif m.stat == "power_mod":
                if existing is not None:
                    existing.display_value = delta * 10
                    existing.scope = "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source, scope="battlefield",
                            stat_key=m.stat, steps=0, display_value=delta * 10,
                            ttl=m.ttl,
                        ))
                element = (m.skill_where or {}).get("element", "")
                prefix = f"{element}" if element else ""
                return f"{sprite.name} {prefix}{label}{delta * 10:+.0f}"
            elif m.stat in _RATIO_STATS:
                display_bonus = delta - 1.0 if m.stat in _TOTAL_BASED_RATIO_STATS else delta
                if existing is not None:
                    existing.display_mult = display_bonus
                    existing.scope = m.scope or "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source,
                            scope=m.scope or "battlefield",
                            stat_key=m.stat, steps=0, display_mult=display_bonus,
                            ttl=m.ttl,
                        ))
                return f"{sprite.name} {label}={delta:.0%}"
            else:
                # Non-ratio, non-stage stats: display as absolute delta
                if existing is not None:
                    existing.display_value = delta
                    existing.scope = m.scope or "battlefield"
                    if m.ttl > 0:
                        existing.ttl = max(existing.ttl, m.ttl)
                else:
                    active = getattr(sprite, 'active_effects', None)
                    if active is not None:
                        active.append(StatBuffEffect(
                            name=eff_name, source=source,
                            scope=m.scope or "battlefield",
                            stat_key=m.stat, steps=0, display_value=delta,
                            ttl=m.ttl,
                        ))
                return f"{sprite.name} {label}{delta:+.0f}"
        # No source → just log, no display effect
        if m.stat == "energy_cost":
            return ""
        if m.stat == "_burst_extended":
            return ""
        if m.stat in _RATIO_STATS:
            return f"{sprite.name} {label}={delta:.0%}"
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
        leaving_sprite: Sprite | None = None,
    ):
        self.self = self_sprite
        self.opp = opp_sprite
        self.globals = globals_
        self.registry = registry
        self.team = team  # "A" or "B"
        self._species_lookup = species_lookup  # callable(number) -> SpeciesStats | None
        self._self_skill = self_skill
        self._battle = battle  # optional ref for trait-level ops
        self._leaving = leaving_sprite  # for post_enemy_leave: the sprite that left
        self._trait_sourcing: bool = False  # True during trait observer then-effect replay
        self._cleared_position_stats: set[str] = set()  # per-replay batch cleanup tracking
        self._energy_deltas: dict[int, int] = {}  # id(m) -> actual delta (capped by max_energy/floor 0)
        # MCTS 仿真模式下跳过所有 UI 显示逻辑（字符串格式化、_sync_mult_display_effect）
        self.is_headless: bool = getattr(battle, '_mcts_sim', False) if battle else False

    def _invalidate_battle_ctx_cache(self) -> None:
        if self._battle is not None and hasattr(self._battle, "_invalidate_ctx_team_cache"):
            self._battle._invalidate_ctx_team_cache()

    # ── Main entry ──

    def replay(self, journal: Journal) -> list[str]:
        """Replay all mutations. Returns event strings for logging."""
        self._cleared_position_stats.clear()
        self._energy_deltas.clear()
        if self.is_headless:
            dispatch = self._DISPATCH
            for mutation in journal:
                handler = dispatch.get(type(mutation))
                if handler is not None:
                    handler(self, mutation)
            return []
        events: list[str] = []
        for mutation in journal:
            ev = self._apply(mutation)
            if ev:
                events.append(ev)
        return events

    # ── Dispatch ──

    # O(1) type dispatch dict — replaces 31-branch cls.__name__ string chain
    _DISPATCH: dict[type, callable] = {}

    def _apply(self, m: Mutation) -> str:
        handler = self._DISPATCH.get(type(m))
        if handler is not None:
            if self.is_headless:
                handler(self, m)
                return ""
            return handler(self, m)
        return f"Unknown mutation: {type(m).__name__}"

    # ── Handlers ──

    def _apply_stat_change(self, m: StatChange) -> str:
        sprite = self._target_sprite(m.target)
        # Immunity gate: block stat debuffs (steps < 0) on stage stats
        if m.steps < 0 and m.stat in _STAGE_STATS:
            if self._check_immune(sprite, "immune_stat_down", m.stat):
                return "" if self.is_headless else f"{sprite.name} 免疫{_STAT_LABELS.get(m.stat, m.stat)}降低"
        # 同步战斗逻辑所需数据到 active_effects（影响编码器输入）
        self._sync_stat_buff_effect(sprite, m.stat, m.steps, m.scope,
                                    m.source or "skill",
                                    is_inherent=self._trait_sourcing)
        # ── 以下为纯 UI 显示逻辑，MCTS 仿真模式跳过 ──
        if self.is_headless:
            return ""
        label = _STAT_LABELS.get(m.stat, m.stat)
        unit = _STEP_UNIT.get(m.stat, 10)
        if m.stat in ('priority', 'energy_cost', 'combo'):
            display = f'{label}{m.steps * unit:+d}' if m.steps != 0 else f'{label}{m.steps:+d}'
        elif m.stat in ('speed', 'power'):
            display = f'{label}{m.steps * unit:+d}'
        else:
            display = f'{label}{m.steps * unit:+d}%'
        # Stage stats from traits: create display-only effect for trait tooltip
        if m.stat in _STAGE_STATS:
            source = m.source or ""
            if m.stat == "speed":
                self._sync_mult_display_effect(sprite, m.stat, 0.0, m.scope, source,
                                                display_value=float(m.steps * _SPEED_STEP),
                                                additive=True)
            else:
                mult_value = m.steps * (_STEP_PCT / 100)
                self._sync_mult_display_effect(sprite, m.stat, mult_value, m.scope, source,
                                                additive=True)
        # Non-stage stats (power, energy_cost, priority, combo): display as absolute values
        elif m.source and m.stat in ('power', 'energy_cost', 'priority', 'combo') and m.steps != 0:
            self._sync_mult_display_effect(sprite, m.stat, 0.0, m.scope, m.source,
                                           display_value=float(m.steps * unit))
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
            if not devotion_name or devotion_name == "random":
                import random

                from backend.engine.devotion_config import DEVOTION_TYPES
                if not DEVOTION_TYPES:
                    return ""
                if m.mode == "add":
                    total = int(m.value)
                    for _ in range(total):
                        pick = random.choice(list(DEVOTION_TYPES.keys()))
                        player.devotion[pick] = player.devotion.get(pick, 0) + 1
                    return f"{t}队 获得{total}次随机奉献"
                devotion_name = random.choice(list(DEVOTION_TYPES.keys()))
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
            # Track scope for cleanup when consumed (e.g. 野性感官 priority+1 → turn scope)
            skill_scoped_on = m.target.startswith("skill_") if m.target else False
            if not skill_scoped_on and m.scope in ("turn", "battlefield", "persistent"):
                sprite._mod_scopes[m.stat] = m.scope
            if m.stat == 'energy_cost':
                return f"{sprite.name} 获得待机效果: 能耗{m.value:+}"
            label = _STAT_LABELS.get(m.stat, m.stat)
            return f"{sprite.name} 获得待机效果: {label}{m.value:+}"

        skill_scoped = m.target.startswith("skill_") if m.target else False

        # ── skill_filter "all" on sprite target: distribute to every BattleSkill ──
        if not skill_scoped and m.skill_filter == "all" and m.stat in _SKILL_DISTRIBUTE_STATS:
            return _apply_to_all_skills(sprite, m, replayer=self)

        # ── skill_where or skill_filter (attack/defense/status/...) on sprite target ──
        if not skill_scoped and (m.skill_where is not None or
                                (m.skill_filter and m.skill_filter != "all")):
            mark_mod = 0
            if self._battle is not None and self.team:
                mark_mod = self._battle.globals.mark_energy_mod(self.team)
            return _apply_to_matching_skills(sprite, m, mark_energy_mod=mark_mod, replayer=self)

        if skill_scoped:
            if m.target.startswith("skill_at_"):
                # Route to specific skill position (1-indexed: skill_at_1 → skills[0])
                # Before first occurrence of each stat per replay batch, clear old
                # position-based modifiers from all skills (prevents stacking across turns).
                if m.stat not in self._cleared_position_stats:
                    self._cleared_position_stats.add(m.stat)
                    for bs in sprite.skills:
                        bs._modifiers.pop(m.stat, None)
                        if m.stat == "drive":
                            bs._transmission = bs.base.transmission
                        elif m.stat == "sealed":
                            bs.sealed = False
                try:
                    pos = int(m.target.rsplit("_", 1)[-1]) - 1
                    if 0 <= pos < len(sprite.skills):
                        target_mods = sprite.skills[pos]._modifiers
                        # drive flag → _transmission on the target skill
                        if m.stat == "drive":
                            sprite.skills[pos]._transmission = int(m.value) if m.value else 0
                        elif m.stat == "sealed":
                            sprite.skills[pos].sealed = bool(m.value)
                    else:
                        target_mods = sprite._modifiers
                except (ValueError, IndexError):
                    target_mods = sprite._modifiers
            elif self._self_skill is not None:
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
            if cur is None:
                # damage_reduction and life_drain base is 0.0 (0%=none),
                # unlike multiplier ratio stats whose base is 1.0 (1.0×=no change).
                cur = 0.0 if m.stat in ("damage_reduction", "life_drain") else 1.0 if m.stat in _RATIO_STATS else 0.0
            target_mods[m.stat] = cur + delta
        elif m.mode == "multiply":
            target_mods[m.stat] = (cur or 1.0) * m.value if cur is not None else m.value
        else:
            target_mods[m.stat] = m.value
        final = target_mods[m.stat]

        # 失效属性缓存（当修改四维属性修正时）
        if m.stat in ("atk", "def", "sp_atk", "sp_def"):
            sprite._invalidate_stat_cache()

        # Sync sprite-level attrs (max_energy, starfall_consume_ratio) to active_effects.
        # Observer-triggered power_mod writes to _modifiers but property methods read
        # from active_effects → ModifierEffect (see Sprite.max_energy).
        from backend.engine.trait_loader import TraitLoader
        from backend.vm.effect import ModifierEffect
        if not skill_scoped and m.stat in TraitLoader._SPRITE_LEVEL_ATTRS:
            existing = None
            for e in sprite.active_effects:
                if isinstance(e, ModifierEffect) and e.attr == m.stat:
                    existing = e
                    break
            if existing is not None:
                existing.value = m.value
                # Update name for immunity attrs (may change from blanket → specific)
                if m.stat.startswith("immune_") and m.name:
                    existing.name = m.name
            else:
                # Immunity attrs: use raw name from JSON (empty = blanket immunity)
                effect_name = (m.name or "") if m.stat.startswith("immune_") else f"{m.source or 'trait'}-{m.stat}"
                sprite.active_effects.append(ModifierEffect(
                    name=effect_name,
                    source=m.source or "trait",
                    attr=m.stat,
                    value=m.value,
                    mode=m.mode,
                    target=m.target,
                    scope=m.scope,
                ))

        # Track invisible modifiers for scope cleanup
        if not skill_scoped and m.scope in ("turn", "battlefield", "persistent"):
            sprite._mod_scopes[m.stat] = m.scope

        # ── 以下为纯 UI 显示逻辑（创建 StatBuffEffect 用于界面展示），
        #     MCTS 仿真模式跳过全部。战斗逻辑数据已通过 _modifiers /
        #     ModifierEffect 完成同步。 ──
        if self.is_headless:
            return ""

        label = _STAT_LABELS.get(m.stat, m.stat)

        if not skill_scoped and m.value != 0:
            create_visible = False
            steps = 0
            if m.stat in _VISIBLE_MOD_STATS:
                steps = int(m.value * _STEP_UNIT.get(m.stat, 10)) if m.stat in _RATIO_STATS else int(m.value)
                create_visible = True
            # _STAGE_STATS (atk/def/sp_atk/sp_def/speed) are already applied
            # through _modifiers → build_ctx → atk_self/def_self/etc.
            # Creating a visible effect here would cause double-counting:
            # once via _modifiers and once via _extract_stat_stages.
            if create_visible and steps != 0:
                self._sync_stat_buff_effect(sprite, m.stat, steps, m.scope,
                                            m.source or "skill", mode=m.mode)
        # Create display-only StatBuffEffect for trait tooltip
        # (steps=0 so _extract_stat_stages ignores it, no double-counting).
        # Only created when m.source is explicitly set (trait-injected),
        # never fall back to species.ability — that would misattribute
        # skill effects as trait effects.
        source = m.source or ""
        if source:
            if m.stat in _STAGE_STATS:
                # Percentage stats: display_mult = ratio value (e.g., 0.5 = +50%)
                self._sync_mult_display_effect(sprite, m.stat, m.value, m.scope, source,
                                                additive=(m.mode == "add"))
            elif m.stat in _VISIBLE_MOD_STATS:
                if m.stat in _RATIO_STATS:
                    self._sync_mult_display_effect(sprite, m.stat, m.value, m.scope, source)
                elif m.stat == "power_mod":
                    # power_mod is step-based (1步=10威力), use display_value for flat power display
                    self._sync_mult_display_effect(
                        sprite, m.stat, 0.0, m.scope, source,
                        display_value=float(m.value) * 10)
                else:
                    self._sync_mult_display_effect(sprite, m.stat, 0, m.scope, source,
                                                   display_value=float(m.value))

        if m.stat in _RATIO_STATS:
            return f"{sprite.name} {label}={final:.0%}"
        if m.stat == "power_mod":
            return f"{sprite.name} {label}{final * 10:+.0f}"
        if m.stat == "sealed":
            if m.target.startswith("skill_at_"):
                try:
                    pos = int(m.target.rsplit("_", 1)[-1])
                except ValueError:
                    pos = "?"
                return f"{sprite.name} {pos}号位封印"
            return f"{sprite.name} 技能封印"
        if m.stat in _STAGE_STATS:
            return f"{sprite.name} {label}{final:+.0%}"
        if m.stat == "energy_cost":
            return ""
        if m.stat == "_burst_extended":
            return ""
        if m.stat == "combo_set":
            return f"{sprite.name} 连击固定为{final:.0f}"
        if m.stat in ("swift", "drive"):
            skill_name = ""
            label = _STAT_LABELS.get(m.stat, m.stat)
            if m.target == "skill_off_0" and self._self_skill is not None:
                skill_name = getattr(self._self_skill.base, 'name', '') if hasattr(self._self_skill, 'base') else ''
            elif m.target.startswith("skill_at_"):
                try:
                    pos = int(m.target.rsplit("_", 1)[-1]) - 1
                    if 0 <= pos < len(sprite.skills):
                        skill_name = sprite.skills[pos].base.name
                except (ValueError, IndexError):
                    pass
            if skill_name:
                return f"{skill_name} 获得{label}"
        return f"{sprite.name} {label}{final:+.0f}"

    def _apply_damage(self, m: Damage) -> str:
        sprite = self._target_sprite(m.target)
        actual = sprite.take_damage(m.amount)

        # Life drain: attacker heals by a percentage of damage dealt.
        healed = 0
        if m.target != "sprite_self":
            drain_pct = self.self._modifiers.get("life_drain", 0.0)
            if self._self_skill is not None:
                drain_pct = max(drain_pct, self._self_skill._modifiers.get("life_drain", 0.0))
            if drain_pct > 0:
                healed = self.self.heal(round(actual * drain_pct))

        if self.is_headless:
            return ""
        result = f"{sprite.name} -{actual}HP"
        if sprite.is_fainted:
            result += " (fainted)"
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
            self._energy_deltas[id(m)] = actual
            return f"{sprite.name} +{actual}E"
        else:
            actual = sprite.lose_energy(-m.delta)
            self._energy_deltas[id(m)] = -actual
            return f"{sprite.name} -{actual}E"

    def _apply_mark_change(self, m: MarkChange) -> str:
        """Apply, dispel, steal, or convert marks."""
        if m.action == "apply":
            team = self.team if m.target_team == "own" else ("B" if self.team == "A" else "A")
            category = self.globals.classify_mark(m.name)
            coexist = bool(self.self._modifiers.get("mark_coexist", False))
            self.globals.apply_mark(team, m.name, category, m.delta, coexist=coexist)
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
                    if mark.stacks <= 0:
                        self.globals.mark_effects.get(team, []).remove(mark)
                    return f"{self.self.name} 驱散{team}方{m.name}×{removed}"
            return ""

        if m.action == "steal":
            opp_team = "B" if self.team == "A" else "A"
            team = self.team if m.target_team == "own" else opp_team
            from_team = opp_team if team == self.team else self.team
            pos, neg = self.globals.get_marks(from_team)
            all_marks = pos + neg
            count = m.delta or 1
            if m.name:
                for mark in all_marks:
                    if mark.name == m.name and mark.stacks > 0:
                        removed = min(mark.stacks, count)
                        mark.stacks -= removed
                        if mark.stacks <= 0:
                            self.globals.mark_effects.get(from_team, []).remove(mark)
                        category = self.globals.classify_mark(m.name)
                        coexist = bool(self.self._modifiers.get("mark_coexist", False))
                        self.globals.apply_mark(team, m.name, category, removed, coexist=coexist)
                        return f"{self.self.name} 偷取{m.name}×{removed}"
            else:
                import random
                available = [mk for mk in all_marks if mk.stacks > 0]
                if available:
                    mark = random.choice(available)
                    removed = min(mark.stacks, count)
                    mark.stacks -= removed
                    if mark.stacks <= 0:
                        self.globals.mark_effects.get(from_team, []).remove(mark)
                    category = self.globals.classify_mark(mark.name)
                    coexist = bool(self.self._modifiers.get("mark_coexist", False))
                    self.globals.apply_mark(team, mark.name, category, removed, coexist=coexist)
                    return f"{self.self.name} 偷取{mark.name}×{removed}"
            return ""

        if m.action == "convert":
            from backend.vm.effect import AbnormalEffect
            source_name = m.source_abnormal
            if not source_name:
                return ""
            effects_list = [e for e in self.self.active_effects
                            if isinstance(e, AbnormalEffect)
                            and e.name == source_name]
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
            coexist = bool(self.self._modifiers.get("mark_coexist", False))
            self.globals.apply_mark(team, m.name, category, marks, coexist=coexist)
            return f"{self.self.name} {source_name}→{m.name}×{marks}"

        return ""

    @staticmethod
    def _check_immune(sprite, immune_type: str, target_name: str = "") -> bool:
        """Check if sprite has immunity to a specific abnormal or stat debuff.

        immune_type: "immune_abnormal" or "immune_stat_down"
        target_name: specific name ("灼烧", "atk") — empty name on effect = blanket immunity
        """
        from backend.vm.effect import ModifierEffect
        for e in getattr(sprite, 'active_effects', []):
            if isinstance(e, ModifierEffect) and e.attr == immune_type:
                if not e.name or e.name == target_name:
                    return True
        return False

    def _apply_abnormal_change(self, m: AbnormalChange) -> str:
        sprite = self._target_sprite(m.target)
        # 萌化: trigger form devolution via apply_moe (needs species lookup)
        if m.name == '萌化' and self._species_lookup is not None and m.delta > 0:
            return self._apply_moe_via_replayer(sprite, m)
        # Immunity gate: only block application (delta > 0), never block removal
        if m.delta > 0 and self._check_immune(sprite, "immune_abnormal", m.name):
            return f"{sprite.name} 免疫{m.name}"
        self._sync_abnormal_effect(sprite, m.name, m.delta, m.scope)
        if m.name == '萌化':
            self._invalidate_battle_ctx_cache()
        return f"{sprite.name} {m.name} +{m.delta}层"

    @staticmethod
    def _sync_abnormal_effect(sprite, name: str, delta: int, scope: str) -> None:
        """Create or update AbnormalEffect on sprite.active_effects (dual-write).

        Incrementally updates sprite._cached_abnormals to avoid O(N) rebuild.
        """
        from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
        from backend.vm.effect import AbnormalEffect

        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        existing = next(
            (e for e in active if isinstance(e, AbnormalEffect) and e.name == name), None
        )
        if existing is not None:
            existing.stacks += delta
            # 增量更新缓存（MCTS 热路径）
            if not getattr(sprite, '_effects_dirty', True):
                sprite._cached_abnormals[name] = sprite._cached_abnormals.get(name, 0) + delta
            if existing.stacks <= 0:
                active.remove(existing)
                sprite._invalidate_effects_cache()
            return

        if delta <= 0:
            return

        template = ABNORMAL_TEMPLATES.get(name)
        if template is not None:
            new_effect = AbnormalEffect(
                name=template.name,
                source=template.source,
                scope=scope or template.scope,
                ttl=template.ttl,
                stacks=delta,
                tick_damage_pct=template.tick_damage_pct,
                tick_element=template.tick_element,
                decay_on_tick=template.decay_on_tick,
                max_stacks=template.max_stacks,
            )
        else:
            new_effect = AbnormalEffect(
                name=name, source="skill", scope=scope, stacks=delta,
            )
        active.append(new_effect)
        # 增量更新缓存
        if not getattr(sprite, '_effects_dirty', True):
            sprite._cached_abnormals[name] = sprite._cached_abnormals.get(name, 0) + delta

    def _apply_moe_via_replayer(self, sprite: Sprite, m: AbnormalChange) -> str:
        """Apply 萌化 form devolution through the replayer path.

        Creates a minimal battle adapter so apply_moe() can look up species.
        """
        class _MoeBattle:
            def lookup_species_by_number(_self, number):
                return self._species_lookup(number)
        events = sprite.apply_moe(m.delta, _MoeBattle())
        self._invalidate_battle_ctx_cache()
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
                self._invalidate_battle_ctx_cache()
                return f"{old_name} 萌化解除 → 变为{sprite.name}(-{removed}层)"
            if m.source:
                n = self._remove_by_source(sprite, m.source, "abnormal")
                self._remove_abnormal_effect(sprite, source=m.source)
                return f"{sprite.name} 驱散异常(source={m.source}) x{n}"
            sprite.remove_effect(m.name, "abnormal")
            self._remove_abnormal_effect(sprite, name=m.name)
            if m.name == '萌化':
                self._invalidate_battle_ctx_cache()
            return f"{sprite.name} 驱散异常 {m.name}"
        elif m.what == "mark":
            team = self.team if m.target == "team_own" else ("B" if self.team == "A" else "A")
            pos, neg = self.globals.get_marks(team)
            all_marks = pos + neg
            count = m.limit or 1
            if m.name:
                for mark in all_marks:
                    if mark.name == m.name and mark.stacks > 0:
                        removed = min(mark.stacks, count)
                        mark.stacks -= removed
                        if mark.stacks <= 0:
                            self.globals.mark_effects.get(team, []).remove(mark)
                        return f"{team}队 驱散印记 {m.name}×{removed}"
                return f"驱散印记失败：{team}队无{m.name}"
            import random
            available = [mk for mk in all_marks if mk.stacks > 0]
            if not available:
                return "驱散印记失败：无印记"
            mark = random.choice(available)
            removed = min(mark.stacks, count)
            mark.stacks -= removed
            if mark.stacks <= 0:
                self.globals.mark_effects.get(team, []).remove(mark)
            return f"{team}队 驱散印记 {mark.name}×{removed}"
        return ""

    @staticmethod
    def _dispel_by_source(sprite, source: str, positive_only: bool = True) -> int:
        """Remove effects from sprite matching the given source. Returns count removed."""
        from backend.vm.effect import StatBuffEffect
        removed = 0
        for e in list(getattr(sprite, 'active_effects', [])):
            if isinstance(e, StatBuffEffect) and getattr(e, 'source', '') == source:
                if positive_only and e.steps <= 0:
                    continue
                if not positive_only and e.steps >= 0:
                    continue
                sprite.active_effects.remove(e)
                removed += 1
        return removed

    @staticmethod
    def _remove_by_source(sprite, source: str, category: str = '') -> int:
        """Remove effects matching source and optional category. Returns count removed."""
        from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect
        type_map = {'stat': StatBuffEffect, 'abnormal': AbnormalEffect, 'state': StateEffect}
        target_type = type_map.get(category)
        removed = 0
        for e in list(getattr(sprite, 'active_effects', [])):
            if getattr(e, 'source', '') != source:
                continue
            if target_type is not None and not isinstance(e, target_type):
                continue
            sprite.active_effects.remove(e)
            removed += 1
        return removed

    @staticmethod
    def _remove_abnormal_effect(sprite, name: str = '', source: str = '') -> None:
        """Remove AbnormalEffect from sprite.active_effects by name or source."""
        from backend.vm.effect import AbnormalEffect
        active = getattr(sprite, 'active_effects', None)
        if not active:
            return
        to_remove = []
        for e in active:
            if not isinstance(e, AbnormalEffect):
                continue
            if name and e.name == name or source and e.source == source:
                to_remove.append(e)
        for e in to_remove:
            active.remove(e)

    @staticmethod
    def _sync_stat_buff_effect(sprite, stat_key: str, steps: int, scope: str,
                               source: str, mode: str = "add",
                               is_inherent: bool = False) -> None:
        """Create or update StatBuffEffect on sprite.active_effects (dual-write).

        When mode="set", existing steps are replaced instead of accumulated.
        is_inherent=True marks effects from traits that should not be inherited.
        Incrementally updates sprite._cached_stages / _cached_positive to avoid
        O(N) _extract_sprite_effects rebuild on every Ctx snapshot.
        """
        from backend.vm.effect import StatBuffEffect
        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        existing = next(
            (e for e in active
             if isinstance(e, StatBuffEffect) and e.stat_key == stat_key and e.scope == scope),
            None,
        )
        if existing is not None:
            old_steps = existing.steps
            if mode == "set":
                existing.steps = steps
            else:
                existing.steps += steps
            # 增量更新缓存（MCTS 热路径）
            if not getattr(sprite, '_effects_dirty', True):
                if mode == "set":
                    sprite._cached_stages[stat_key] = sprite._cached_stages.get(stat_key, 0) - old_steps + steps
                else:
                    sprite._cached_stages[stat_key] = sprite._cached_stages.get(stat_key, 0) + steps
                # positive 计数调整
                new_steps = existing.steps
                if old_steps <= 0 and new_steps > 0:
                    sprite._cached_positive += 1
                elif old_steps > 0 and new_steps <= 0:
                    sprite._cached_positive -= 1
            # Propagate is_inherent to existing effect if not already set
            if is_inherent and not getattr(existing, 'is_inherent', False):
                existing.is_inherent = True
            return

        active.append(StatBuffEffect(
            name=f'{stat_key}', source=source, scope=scope,
            stat_key=stat_key, steps=steps, is_inherent=is_inherent,
        ))
        # 增量更新缓存
        if not getattr(sprite, '_effects_dirty', True):
            sprite._cached_stages[stat_key] = sprite._cached_stages.get(stat_key, 0) + steps
            if steps > 0:
                sprite._cached_positive += 1

    @staticmethod
    def _sync_mult_display_effect(sprite, stat_key: str, mult_value: float,
                                   scope: str, source: str,
                                   display_value: float | None = None,
                                   additive: bool = False) -> None:
        """Create or update display-only StatBuffEffect for mult_mod values.

        Sets steps=0 so _extract_stat_stages ignores it (no double-counting).
        The display_mult field carries the ratio for UI display only.
        The display_value field carries absolute values (combo, priority, etc.).

        When additive=True, mult_value and display_value are added to existing
        values instead of replacing them (used for cumulative stat_stage triggers).
        """
        if not source:
            return
        from backend.vm.effect import StatBuffEffect
        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        existing = next(
            (e for e in active
             if isinstance(e, StatBuffEffect) and e.stat_key == stat_key
             and e.source == source and e.steps == 0),
            None,
        )
        if existing is not None:
            if additive:
                existing.display_mult += mult_value
                if display_value is not None:
                    existing.display_value = (existing.display_value or 0) + display_value
            else:
                existing.display_mult = mult_value
                if display_value is not None:
                    existing.display_value = display_value
            existing.scope = scope
            return

        from backend.vm.effect import _STAT_LABELS
        active.append(StatBuffEffect(
            name=_STAT_LABELS.get(stat_key, stat_key),
            source=source, scope=scope,
            stat_key=stat_key, steps=0, display_mult=mult_value,
            display_value=display_value,
        ))

    @staticmethod
    def _sync_state_effect(sprite, state_type: str, params: dict | None = None) -> None:
        """Create or update StateEffect on sprite.active_effects (dual-write)."""
        from backend.vm.effect import StateEffect
        active = getattr(sprite, 'active_effects', None)
        if active is None:
            return

        # Replace existing state of same type
        active[:] = [e for e in active
                     if not (isinstance(e, StateEffect) and e.state_type == state_type)]

        active.append(StateEffect(
            name=state_type, source="skill", scope="turn",
            state_type=state_type, params=params or {},
        ))
        # 增量更新缓存
        if not getattr(sprite, '_effects_dirty', True):
            if state_type == "charging":
                sprite._cached_charging = True
            elif state_type == "charged":
                sprite._cached_charged = True

    def _apply_steal(self, m: Steal) -> str:
        # Steal effects/energy/marks from target to self
        if m.what == "positive":
            from copy import copy

            from backend.vm.effect import StatBuffEffect
            target = self._target_sprite(m.from_target)
            positives = [e for e in target.active_effects
                         if isinstance(e, StatBuffEffect) and e.steps > 0]
            if m.action == "copy":
                for e in positives:
                    self.self.add_effect(copy(e))
                return f"{self.self.name} 复制 {len(positives)} 增益 from {target.name}"
            for e in positives:
                target.active_effects.remove(e)
                self.self.add_effect(e)
            return f"{self.self.name} 偷取 {len(positives)} 增益 from {target.name}"
        elif m.what == "energy":
            amount = m.amount or 0
            if m.from_target == "team_opp" and self._battle is not None:
                opp_player = self._battle.get_opponent(self.team)
                total_stolen = 0
                names: list[str] = []
                for s in opp_player.team:
                    if s.energy <= 0:
                        continue
                    s_stolen = min(s.energy, amount)
                    s.lose_energy(s_stolen)
                    total_stolen += s_stolen
                    names.append(f"{s.name}({s_stolen})")
                self.self.gain_energy(total_stolen)
                return f"{self.self.name} 偷取 {total_stolen}E from {', '.join(names)}"
            target = self._target_sprite(m.from_target)
            stolen = min(target.energy, amount)
            target.lose_energy(stolen)
            self.self.gain_energy(stolen)
            return f"{self.self.name} 偷取 {stolen}E from {target.name}"
        elif m.what == "mark":
            from_team_key = "A" if m.from_target == "team_own" else "B"
            to_team_key = self.team
            name = m.name
            if name:
                mark = self.globals.get_mark_by_name(from_team_key, name)
                if mark and mark.stacks > 0:
                    stacks = mark.stacks
                    category = mark.category
                    self.globals.mark_effects.get(from_team_key, []).remove(mark)
                    coexist = bool(self.self._modifiers.get("mark_coexist", False))
                    self.globals.apply_mark(to_team_key, name, category, stacks, coexist=coexist)
                    return f"{self.self.name} 偷取 {name} x{stacks}"
            return ""
        return ""

    def _apply_tick(self, m: Tick) -> str:
        # Trigger abnormal tick damage — matches turn_end() formula
        sprite = self._target_sprite(m.target)
        stacks = sprite.get_stacks(m.abnormal_name)
        if stacks <= 0:
            return ""

        from backend.vm.effect import AbnormalEffect
        active = getattr(sprite, 'active_effects', None)
        dmg_pct = 0.03
        tick_element = ""
        tick_per_stack = True
        if active:
            ae = next(
                (e for e in active if isinstance(e, AbnormalEffect) and e.name == m.abnormal_name),
                None,
            )
            if ae is not None:
                dmg_pct = ae.tick_damage_pct or dmg_pct
                tick_element = ae.tick_element
                tick_per_stack = ae.tick_per_stack

        raw = max(1, round(sprite.max_hp * dmg_pct * stacks)) if tick_per_stack else max(1, round(sprite.max_hp * dmg_pct))
        mult = self._tick_element_mult(sprite, tick_element)
        dmg = max(1, round(raw * mult))
        sprite.take_damage(dmg)
        return f"{sprite.name} {m.abnormal_name} tick -{dmg}HP"

    @staticmethod
    def _tick_element_mult(sprite, element: str) -> float:
        """元素克制乘数，与 SkillResolver._tick_multiplier 一致。"""
        if not element:
            return 1.0
        from backend.sim.resolver import _TYPE_CHART
        attrs = getattr(sprite.species, 'attributes', '')
        mult = 1.0
        for attr in (attrs.split(',') if attrs else []):
            mult *= _TYPE_CHART.get(element, {}).get(attr, 1.0)
        return mult

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

    def _apply_effect_delta(self, m: EffectDelta) -> str:
        from backend.vm.effect import AbnormalEffect, StatBuffEffect

        sprite = self._target_sprite(m.target)
        n = 0
        for e in list(getattr(sprite, 'active_effects', [])):
            if isinstance(e, AbnormalEffect):
                if m.what == "negative":
                    new_stacks = e.stacks + m.delta
                    if e.max_stacks and new_stacks > e.max_stacks:
                        new_stacks = e.max_stacks
                    e.stacks = new_stacks
                    n += 1
            elif isinstance(e, StatBuffEffect):
                direction = self._match_stat_effect(e, m.what)
                if direction:
                    e.steps += direction * m.delta
                    n += 1
        # Also handle sprite._modifiers for increment-style positive stats
        _INC_STATS = frozenset({"combo", "power", "priority"})
        _DEC_STATS = frozenset({"energy_cost"})
        if m.what == "positive":
            for key in _INC_STATS:
                val = sprite._modifiers.get(key, 0)
                if val > 0:
                    sprite._modifiers[key] = val + m.delta
                    n += 1
            for key in _DEC_STATS:
                val = sprite._modifiers.get(key, 0)
                if val < 0:
                    sprite._modifiers[key] = val - m.delta
                    n += 1
        tag = "增益" if m.what == "positive" else "减益"
        if n:
            sprite._invalidate_effects_cache()
        return f"{sprite.name} {tag} +{m.delta}层 ({n})"

    @staticmethod
    def _match_stat_effect(e, what: str) -> int:
        """返回累加方向: +1 表示 steps+=delta, -1 表示 steps-=delta, 0 不匹配."""

        if e.stat_key == 'energy_cost':
            if what == "negative" and e.steps > 0:
                return 1
            if what == "positive" and e.steps < 0:
                return -1
        else:
            if what == "positive" and e.steps > 0:
                return 1
            if what == "negative" and e.steps < 0:
                return -1
        return 0

    def _apply_charge(self, m: Charge) -> str:
        sprite = self._target_sprite(m.target)
        self._sync_state_effect(sprite, "charging")
        return f"{sprite.name} 开始蓄力"

    def _apply_escape(self, m: Escape) -> str:
        sprite = self._target_sprite(m.target)
        name = sprite.name if sprite else m.target
        if self._battle:
            self._battle.pending_escape = {
                "team": self.team,
                "inherit": m.inherit,
                "urgent": m.urgent,
                "user_name": name,
            }
        return f"{name} 脱离 (inherit={m.inherit}, urgent={m.urgent})"

    def _apply_return(self, m: Return) -> str:
        sprite = self._target_sprite(m.target)
        sprite.pending_return = True
        return f"{sprite.name} 准备返场"

    def _apply_lock(self, m: Lock) -> str:
        sprite = self._target_sprite(m.target)
        sprite.locked_turns = m.turns
        self._sync_state_effect(sprite, "locked", {"turns": m.turns})
        return f"{sprite.name} 锁定 {m.turns}t"

    def _apply_interrupt(self, m: Interrupt) -> str:
        sprite = self._target_sprite(m.target)
        sprite.interrupted = True
        self._sync_state_effect(sprite, "interrupted")
        return f"{sprite.name} 被打断"

    def _apply_exchange(self, m: Exchange) -> str:
        if m.what == "hp_ratio":
            self.self.current_hp, self.opp.current_hp = \
                round(self.opp.current_hp / self.opp.max_hp * self.self.max_hp) if self.opp.max_hp else 0, \
                round(self.self.current_hp / self.self.max_hp * self.opp.max_hp) if self.self.max_hp else 0
            return "交换HP比例"
        elif m.what == "effects":
            self.self.active_effects, self.opp.active_effects = self.opp.active_effects, self.self.active_effects
            return "交换增益减益"
        elif m.what == "skills":
            self.self.skills, self.opp.skills = self.opp.skills, self.self.skills
            return "交换技能"
        elif m.what == "adjacent_skills":
            pre_pos = {id(bs): i for i, bs in enumerate(self.self.skills or [])}
            self._swap_adjacent_skills(self.self)
            position_events = []
            if self._battle is not None:
                for i, bs in enumerate(self.self.skills or []):
                    if pre_pos.get(id(bs), -1) != i:
                        position_events += self._battle._fire_skill_position_changed(
                            self.team, self.self, bs
                        )
            if position_events:
                return " | ".join(["交换相邻技能位置", *position_events])
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

    def _apply_burst_grant(self, m: BurstGrant) -> str:
        """Write burst effects to matching BattleSkills on the target sprite."""
        from backend.engine.modifiers import eval_skill_where

        sprite = self._target_sprite(m.target)
        applied = 0
        for bs in (sprite.skills or []):
            if m.skill_where:
                skill_info = {
                    "name": getattr(bs, 'name', ''),
                    "energy_cost": getattr(bs, 'energy_cost', 0),
                    "element": getattr(getattr(bs, 'base', None), 'element', ''),
                    "skill_type": getattr(getattr(bs, 'base', None), 'skill_type', ''),
                }
                if not eval_skill_where(m.skill_where, skill_info):
                    continue
            if m.skill_filter and m.skill_filter != "all":
                st = getattr(getattr(bs, 'base', None), 'skill_type', '')
                if not _matches_skill_type(m.skill_filter, st):
                    continue
            bs._burst_effects.extend(list(m.effects))
            bs._modifiers["burst"] = float(len(bs._burst_effects) > 0)
            applied += 1
        if applied:
            return f"{sprite.name} {m.source} 迸发赋予 {applied} 技能"
        return ""

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
        return f"{self.self.name} {label}→{player.lives}" if self.self else ""

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

    def _resolve_source(self, source_key: str):
        """Resolve a source key to a sprite reference.

        "self" → replayer.self (attacker / trait bearer / leaving sprite)
        "sprite_opp" → replayer._leaving in post_enemy_leave context,
                       otherwise replayer.opp
        anything else → replayer.opp
        """
        if source_key == "self":
            return self.self
        if source_key == "sprite_opp" and self._leaving is not None:
            return self._leaving
        return self.opp

    def _apply_inherit_effects_mutation(self, m: InheritEffectsMutation) -> str:
        """Transfer effects between sprites. Requires battle reference.

        When inherit_stat_effects=True: copy all StatBuffEffect objects
        (六维/连击/威力/吸血等) regardless of scope.
        Otherwise: filter by scope (legacy behavior).
        """
        if self._battle is None:
            return ""
        source_sprite = self._resolve_source(m.source_key)
        if source_sprite is None:
            return ""
        from copy import copy

        from backend.vm.effect import StatBuffEffect
        if m.inherit_stat_effects:
            inherited = [copy(e) for e in getattr(source_sprite, 'active_effects', [])
                         if isinstance(e, StatBuffEffect)
                         and not getattr(e, 'is_inherent', False)]
        else:
            inherited = [copy(e) for e in getattr(source_sprite, 'active_effects', [])
                         if getattr(e, 'scope', '') == m.scope]
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
        self._invalidate_battle_ctx_cache()
        if isinstance(result, list):
            return ' | '.join(str(x) for x in result)
        return result

    def _apply_trait_interaction_mutation(self, m: TraitInteractionMutation) -> str:
        """Suppress, remove, or copy a trait on a sprite."""
        target = self.self if m.target in ("sprite_self", "self") else self.opp
        if target is None:
            return ""

        def replace_ability(sprite, ability: str, ability_id: int = 0) -> None:
            species = copy(sprite.species)
            species.ability = ability
            species.ability_id = ability_id
            sprite.species = species
            sprite._trait_handler = None

        if m.action == 'suppress':
            target._trait_suppressed = True
            target._trait_handler = None
            return f'{target.name} 特性被压制'
        if m.action == 'remove':
            target._trait_suppressed = True
            target._trait_handler = None
            if m.new_ability:
                replace_ability(target, m.new_ability)
                target._trait_suppressed = False
                return f'{target.name} 特性变为 {m.new_ability}'
            return f'{target.name} 特性被移除'
        if m.action == 'copy':
            source = self.opp if m.copy_from == "sprite_opp" else self.self
            if source is None or target is source:
                return ""
            source_ability = source.species.ability
            source_ability_id = getattr(source.species, 'ability_id', 0)
            if not source_ability and not source_ability_id:
                return ""
            replace_ability(target, source_ability, source_ability_id)
            target._trait_suppressed = False
            return f'{target.name} 复制特性 → {source_ability or source_ability_id}'
        return ""

    def _apply_gain_skills(self, m: GainSkillsMutation) -> str:
        """Grant temporary skills to a sprite from a skill pool.

        Picks count random skills not already carried by the sprite,
        builds BattleSkill instances, and appends them to the sprite's
        skill bar as temporary skills (cleared after battle).
        """
        import random
        sprite = self._target_sprite(m.target)
        if sprite is None:
            return ""

        # Build candidate pool
        if self._battle is not None and hasattr(self._battle, 'list_all_skill_names'):
            all_names = self._battle.list_all_skill_names()
        else:
            return f"{sprite.name} gain_skills: no skill pool available"

        if not all_names:
            return f"{sprite.name} gain_skills: empty skill pool"

        carried = {getattr(bs.base, 'name', '') for bs in (sprite.skills or []) if bs.base}

        candidates = all_names
        if m.exclude_carried:
            candidates = [n for n in all_names if n not in carried]

        if m.source == "learnset":
            species_elements = set(getattr(sprite.species, 'elements', []))
            if species_elements and self._battle is not None and hasattr(self._battle, 'skill_element_map'):
                elem_map = self._battle.skill_element_map()
                candidates = [n for n in candidates
                              if set(elem_map.get(n, [])) & species_elements]

        if not candidates:
            return f"{sprite.name} gain_skills: no candidates"

        count = min(m.count, len(candidates))
        chosen = random.sample(candidates, count)

        if self._battle is not None and self._battle.skill_loader is not None:
            new_skills = self._battle.skill_loader(chosen)
        else:
            return f"{sprite.name} gain_skills: no skill loader"

        for bs in new_skills:
            bs.is_temporary = True
            sprite.skills.append(bs)

        names = ', '.join(getattr(bs, 'name', str(bs)) for bs in new_skills)
        return f"{sprite.name} 获得临时技能: {names}"

    def _apply_counter_register(self, m: CounterRegister) -> str:
        # Counter registration is handled by _register_counters_from_journal
        # in battle.py → register_counter(). The replayer's job here is just
        # to produce the verbose log entry (named counters only).
        if self.registry and m.name and m.name.strip():
            return f"注册计次器: {m.name}"
        return ""

    # ── Helpers ──

    def _target_sprite(self, target: str) -> Sprite:
        if target.startswith("skill_at_"):
            return self.self
        if target in ("sprite_self", "self", "team_own", "skill_off_0"):
            return self.self
        if target == "ally_new" and self._battle is not None:
            return self._battle.get_player(self.team).active
        if target == "enemy_new" and self._battle is not None:
            return self._battle.get_opponent(self.team).active
        if target == "sprite_bench" and self._battle is not None:
            player = self._battle.get_player(self.team)
            bench = [s for i, s in enumerate(player.team) if i != player.active_index and not s.is_fainted]
            if bench:
                import random
                return random.choice(bench)
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


# ── O(1) dispatch dict for JournalReplayer._apply ──
# Built once at import time; replaces the 31-branch cls.__name__ if-elif chain.
JournalReplayer._DISPATCH = {
    AbnormalChange: JournalReplayer._apply_abnormal_change,
    Borrow: JournalReplayer._apply_borrow,
    BurstGrant: JournalReplayer._apply_burst_grant,
    Charge: JournalReplayer._apply_charge,
    CounterRegister: JournalReplayer._apply_counter_register,
    Damage: JournalReplayer._apply_damage,
    Dispel: JournalReplayer._apply_dispel,
    Double: JournalReplayer._apply_double,
    EffectDelta: JournalReplayer._apply_effect_delta,
    EnergyChange: JournalReplayer._apply_energy_change,
    Escape: JournalReplayer._apply_escape,
    Exchange: JournalReplayer._apply_exchange,
    GainSkillsMutation: JournalReplayer._apply_gain_skills,
    Heal: JournalReplayer._apply_heal,
    InheritEffectsMutation: JournalReplayer._apply_inherit_effects_mutation,
    Interrupt: JournalReplayer._apply_interrupt,
    LivesDelta: JournalReplayer._apply_lives_delta,
    Lock: JournalReplayer._apply_lock,
    MarkChange: JournalReplayer._apply_mark_change,
    ModifierInjection: JournalReplayer._apply_modifier,
    Redirect: JournalReplayer._apply_redirect,
    Replay: JournalReplayer._apply_replay,
    Reset: JournalReplayer._apply_reset,
    Return: JournalReplayer._apply_return,
    ScheduleEntry: JournalReplayer._apply_schedule_entry,
    StatChange: JournalReplayer._apply_stat_change,
    Steal: JournalReplayer._apply_steal,
    TeamCounterDelta: JournalReplayer._apply_team_counter_delta,
    Tick: JournalReplayer._apply_tick,
    TraitInteractionMutation: JournalReplayer._apply_trait_interaction_mutation,
    TransformMutation: JournalReplayer._apply_transform_mutation,
    WeatherSet: JournalReplayer._apply_weather_set,
}
