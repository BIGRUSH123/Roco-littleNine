"""Snapshot — build Ctx from mutable battle state.

The ONLY module in engine/ that depends on sim/ (prototype data structures).
When the prototype is replaced, only this module needs updating.

Accepts any object with the required attributes for skill parameters
(CompiledSkill, BattleSkill, Skill, or plain dict-like).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.sim.battleskill import BattleSkill
from backend.sim.resolver import _TYPE_CHART
from backend.vm.effect import MarkEffect
from backend.vm.ctx import Ctx, EventContext

if TYPE_CHECKING:
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite

_SPEED_STEP = 10


def _get_element_advantage(atk_element: str, def_elements: list[str]) -> float:
    """Calculate type effectiveness: product of attacker element vs each defender element.

    Returns 0.5 (resist), 1.0 (neutral), 2.0 (super effective), etc.
    """
    if not atk_element or not def_elements:
        return 1.0
    chart = _TYPE_CHART.get(atk_element, {})
    mult = 1.0
    for de in def_elements:
        mult *= chart.get(de, 1.0)
    return mult


def _compute_speed_self(ss: Sprite, stat_stages: dict[str, int]) -> int:
    """Compute ctx speed_self from sprite, applying _modifiers multiplier."""
    base = max(0, ss.initial_stats.get("speed", 100) + stat_stages.get("speed", 0) * _SPEED_STEP)
    speed_mod = ss._modifiers.get("speed", 0)
    if speed_mod:
        return max(1, round(base * (1.0 + speed_mod)))
    return base


def _compute_speed(stats: dict[str, int], stat_stages: dict[str, int]) -> int:
    return max(0, stats.get("speed", 100) + stat_stages.get("speed", 0) * _SPEED_STEP)


def _battle_skill_summary_key(skills) -> tuple | None:
    key = []
    for sk in skills:
        if type(sk) is not BattleSkill:
            return None
        base = sk.replaced_by or sk.base
        key.append((
            id(sk),
            id(base),
            sk.nullified,
            sk._element_override,
            sk._mech_energy_reduction,
            sk._modifiers.get("energy_cost", 0),
        ))
    return tuple(key)


def _collect_skill_summary(sprite: Sprite) -> tuple[frozenset, dict[str, int], int, int]:
    skills = getattr(sprite, 'skills', None) or ()
    if skills:
        cache_key = _battle_skill_summary_key(skills)
        if cache_key is not None:
            cached = getattr(sprite, '_skill_summary_cache', None)
            if cached is not None and cached[0] == cache_key:
                return cached[1]
    else:
        cache_key = None
    elements: set[str] = set()
    element_counts: dict[str, int] = {}
    energy_sum = 0
    zero_cost_count = 0
    if skills and type(skills[0]) is BattleSkill:
        for sk in skills:
            if sk.nullified:
                el = sk._element_override
                cost = sk._mech_energy_reduction + int(sk._modifiers.get("energy_cost", 0))
            else:
                base = sk.replaced_by or sk.base
                el = sk._element_override or base.element
                cost = base.energy_cost + int(sk._modifiers.get("energy_cost", 0)) + sk._mech_energy_reduction
            if el:
                elements.add(el)
                element_counts[el] = element_counts.get(el, 0) + 1
            energy_sum += cost
            if cost == 0:
                zero_cost_count += 1
        result = (frozenset(elements), element_counts, energy_sum, zero_cost_count)
        if cache_key is not None:
            sprite._skill_summary_cache = (cache_key, result)
        return result

    for sk in skills:
        if isinstance(sk, BattleSkill):
            if sk.nullified:
                el = sk._element_override
                base_cost = 0
            else:
                base = sk.replaced_by or sk.base
                el = sk._element_override or base.element
                base_cost = base.energy_cost
            cost = base_cost + int(sk._modifiers.get("energy_cost", 0)) + sk._mech_energy_reduction
        else:
            try:
                el = sk.element
                cost = sk.energy_cost
            except AttributeError:
                el = getattr(sk, 'element', None)
                cost = getattr(sk, 'energy_cost', 0)
        if el:
            elements.add(el)
            element_counts[el] = element_counts.get(el, 0) + 1
        energy_sum += cost
        if cost == 0:
            zero_cost_count += 1
    return frozenset(elements), element_counts, energy_sum, zero_cost_count


def build_ctx(
    self_sprite: Sprite,
    opp_sprite: Sprite,
    self_skill: Any,
    opp_skill: Any = None,
    globals_: GlobalEffects | None = None,
    *,
    team: str = "A",
    turn: int = 0,
    is_first: bool = False,
    opp_switched: bool = False,
    self_switched: bool = False,
    counter_succeeded: bool = False,
    was_countered: bool = False,
    prev_counter_succeeded: bool = False,
    damage_taken_this_turn: int = 0,
    target_fainted: bool = False,
    damage_reduced_self: int = 0,
    skill_index: int = 0,
    skill_position_changed: bool = False,
    devotion_triggered: bool = False,
    # Skill tracking overrides
    prev_damage_taken_self: bool = False,
    prev_damage_taken_opp: bool = False,
    prev_skill_type: str = "",
    # Event tracking
    last_tick_abnormal: str = "",
    last_tick_target: str = "",
    abnormal_changed_name: str = "",
    abnormal_changed_target: str = "",
    abnormal_applied_name: str = "",
    abnormal_applied_target: str = "",
    skills_energy_changed_of: str = "",
    positive_changed_of: str = "",
    energy_changed_of: str = "",
    damage_taken_of: str = "",
    last_tick_damage_self: int = 0,
    last_tick_damage_opp: int = 0,
    turn_end: bool = False,
    # Counter values
    counter_values: dict[str, int] | None = None,
    # Additional team accumulators
    skill_count_own: dict[str, int] | None = None,
    team_counters_own: dict[str, int] | None = None,
    team_counters_opp: dict[str, int] | None = None,
    energy_cost_sum_self: dict[str, int] | None = None,
    elements_used_count_self: int = 0,
    burst_triggered_count_own: int = 0,
    fainted_own: int = 0,
    fainted_opp: int = 0,
    lives_own: int = 5,
    lives_opp: int = 5,
    team_elements_own: frozenset = frozenset(),
    team_elements_opp: frozenset = frozenset(),
    devotion_own: dict[str, int] | None = None,
    devotion_opp: dict[str, int] | None = None,
    abnormal_stacks_battle: dict[str, int] | None = None,
    moe_team_stacks: int = 0,
    # BattleSkill reference (for _modifiers and synthesized power/energy/combo)
    battle_skill: Any = None,
) -> Ctx:
    """Build a VM Ctx snapshot from mutable battle objects.

    All non-sprite data is passed as keyword arguments so the caller
    (BattleEngine) controls what context to inject.
    """

    # ── Self sprite ──
    ss = self_sprite
    ss_mods = ss._modifiers
    ss_stats = ss.initial_stats
    ss_counters = ss.counters
    hp_self = ss.current_hp
    hp_self_max = ss.max_hp
    hp_self_ratio = hp_self / hp_self_max if hp_self_max > 0 else 0.0
    priority_self = int(ss_mods.get("priority", 0))
    species_self = getattr(ss, 'species', None)
    elements_self = tuple(species_self.elements) if species_self else ()

    # 单次遍历 active_effects，同时提取 stat_stages/abnormal_stacks/charging/charged/positive_count
    (
        stat_stages_self,
        abnormal_stacks_self,
        is_charging_self,
        charged_self,
        positive_count_self,
    ) = _extract_sprite_effects(ss)

    (
        skill_elements_self,
        skill_element_counts_self,
        skills_energy_sum_self,
        zero_cost_skill_count_self,
    ) = _collect_skill_summary(ss)

    # Energy cost sum by type/element/tag — accumulated by engine
    ecs = energy_cost_sum_self or {}

    # ── Opponent sprite ──
    os = opp_sprite
    os_mods = os._modifiers
    os_stats = os.initial_stats
    hp_opp = os.current_hp
    hp_opp_max = os.max_hp
    hp_opp_ratio = hp_opp / hp_opp_max if hp_opp_max > 0 else 0.0
    species_opp = getattr(os, 'species', None)
    elements_opp = tuple(species_opp.elements) if species_opp else ()

    (
        stat_stages_opp,
        abnormal_stacks_opp,
        is_charging_opp,
        charged_opp,
        positive_count_opp,
    ) = _extract_sprite_effects(os)
    (
        skill_elements_opp,
        skill_element_counts_opp,
        skills_energy_sum_opp,
        _zero_cost_skill_count_opp,
    ) = _collect_skill_summary(os)

    skill_element_count_self = len(skill_elements_self)
    skill_element_count_opp = len(skill_elements_opp)

    # ── Teams (from GlobalEffects) ──
    g = globals_
    own_team = team
    opp_team = "B" if team == "A" else "A"
    mark_stacks_own: dict[str, int] = {}
    mark_count_own = 0
    mark_stacks_opp: dict[str, int] = {}
    mark_count_opp = 0
    mark_bonus_own = 0.0
    if g:
        for m in g.mark_effects.get(own_team, ()):
            if not isinstance(m, MarkEffect):
                continue
            mark_stacks_own[m.name] = mark_stacks_own.get(m.name, 0) + m.stacks
            mark_count_own += m.stacks
            if m.damage_mult:
                cond = m.condition
                if cond == '' or (cond == 'is_first' and is_first) or (cond == 'not_first' and not is_first):
                    mark_bonus_own += m.damage_mult * m.stacks
        for m in g.mark_effects.get(opp_team, ()):
            if not isinstance(m, MarkEffect):
                continue
            mark_stacks_opp[m.name] = mark_stacks_opp.get(m.name, 0) + m.stacks
            mark_count_opp += m.stacks

    weather = g.weather if g else ""

    # ── Skill ──
    sk = self_skill
    bs = battle_skill
    # Read skill-level _modifiers from BattleSkill (unified dict);
    # fall back to duck-typed _modifiers on self_skill for tests.
    skill_mods = getattr(bs, '_modifiers', {}) if bs is not None else getattr(sk, '_modifiers', {}) if sk else {}
    if bs is not None:
        power_self = bs.power
        combo_base = bs.base.combo if hasattr(bs, 'base') else getattr(sk, 'combo', 1)
        energy_cost_self = bs.energy_cost
    else:
        power_self = sk.power if hasattr(sk, 'power') else 0
        combo_base = sk.combo if hasattr(sk, 'combo') else 1
        energy_cost_self = sk.energy_cost if hasattr(sk, 'energy_cost') else 0
    combo_mod = int(ss_mods.get("combo", 0))
    combo_set = int(ss_mods.get("combo_set", 0))
    # combo_mult 不在 snapshot 阶段乘入 — 留给 adjust_damage 在
    # 同技能 combo 修改（set/add）之后再乘，确保正确的执行顺序。
    combo_self = max(1, combo_set) if combo_set > 0 else max(1, combo_base + combo_mod)
    energy_cost_reduction_self = 0  # engine tracks this

    # ── Opp skill ──
    osk = opp_skill
    power_opp = osk.power if osk and hasattr(osk, 'power') else 0
    energy_cost_opp = osk.energy_cost if osk and hasattr(osk, 'energy_cost') else 0

    # ── Build EventContext ──
    event_ctx = EventContext(
        counter_succeeded=counter_succeeded,
        was_countered=was_countered,
        prev_counter_succeeded=prev_counter_succeeded,
        target_fainted=target_fainted,
        self_koed=ss.is_fainted,
        opp_switched=opp_switched,
        self_switched=self_switched,
        turn_end=turn_end,
        skill_position_changed=skill_position_changed,
        devotion_triggered=devotion_triggered,
        last_tick_abnormal=last_tick_abnormal,
        last_tick_target=last_tick_target,
        abnormal_changed_name=abnormal_changed_name,
        abnormal_changed_target=abnormal_changed_target,
        abnormal_applied_name=abnormal_applied_name,
        abnormal_applied_target=abnormal_applied_target,
        skills_energy_changed_of=skills_energy_changed_of,
        positive_changed_of=positive_changed_of,
        energy_changed_of=energy_changed_of,
        damage_taken_of=damage_taken_of,
    )

    # ── 批量提取字典值（减少重复查找，提升性能） ──
    # Self sprite 修正值（非四维属性）- 使用 Sprite 缓存
    damage_reduction_mod_self = ss.damage_reduction_modifier
    power_mult_mod_self = ss.power_mult_modifier
    damage_mult_mod_self = ss.damage_mult_modifier
    energy_cost_mult_mod_self = ss.energy_cost_mult_modifier
    combo_mult_mod_self = ss.combo_mult_modifier
    life_drain_mod_self = ss.life_drain_modifier

    # Self sprite counters
    times_entered_val = ss_counters.get("times_entered", 0)
    times_left_val = ss_counters.get("times_left", 0)

    # Opponent sprite 修正值 - 使用 Sprite 缓存
    damage_reduction_mod_opp = os.damage_reduction_modifier
    power_mult_mod_opp = os.power_mult_modifier
    damage_mult_mod_opp = os.damage_mult_modifier

    # ── Build Ctx ──
    return Ctx(
        event=event_ctx,
        # Bloodline / Elements
        bloodline_self=getattr(ss, 'bloodline', ''),
        bloodline_opp=getattr(os, 'bloodline', '') if os else '',
        elements_self=elements_self,
        elements_opp=elements_opp,
        # Self sprite
        hp_self=hp_self,
        hp_self_ratio=hp_self_ratio,
        hp_self_max=hp_self_max,
        energy_self=ss.energy,
        priority_self=priority_self,
        # Use initial_stats (base without stage multipliers) because
        # calc_damage applies stat_stages separately in the formula.
        # Apply _modifiers multipliers for mult_mod {attr: atk/def/etc.}
        atk_self=ss.atk_with_modifiers,
        def_self=ss.def_with_modifiers,
        sp_atk_self=ss.sp_atk_with_modifiers,
        sp_def_self=ss.sp_def_with_modifiers,
        speed_self=_compute_speed_self(ss, stat_stages_self),
        # Sprite + skill modifier delta 相加（同类型 buff 加性叠加，非相乘）
        damage_reduction_self=min(1.0,
            damage_reduction_mod_self
            + skill_mods.get("damage_reduction", 0.0)),
        power_mult_self=1.0
            + (power_mult_mod_self - 1.0)
            + (skill_mods.get("power_mult", 1.0) - 1.0),
        damage_mult_self=1.0
            + (damage_mult_mod_self - 1.0)
            + (skill_mods.get("damage_mult", 1.0) - 1.0),
        energy_cost_mult_self=energy_cost_mult_mod_self,
        combo_mult_self=combo_mult_mod_self,
        life_drain_self=life_drain_mod_self,
        abnormal_count_self=sum(abnormal_stacks_self.values()),
        abnormal_stacks_self=abnormal_stacks_self,
        positive_count_self=positive_count_self,
        first_action_self=getattr(ss, 'first_action', True),
        first_action_battle_self=getattr(ss, 'first_action_battle', True),
        charged_self=charged_self,
        is_charging_self=is_charging_self,
        is_charging_opp=is_charging_opp,
        times_entered_self=times_entered_val,
        times_left_self=times_left_val,
        elements_used_count_self=elements_used_count_self,
        skills_energy_sum_self=skills_energy_sum_self,
        just_entered=getattr(ss, 'entry_turn', -1) == turn and turn >= 0,
        skill_elements_self=skill_elements_self,
        skill_element_counts_self=skill_element_counts_self,
        skill_element_count_self=skill_element_count_self,
        stat_stages_self=stat_stages_self,
        energy_cost_sum_self=ecs,
        zero_cost_skill_count_self=zero_cost_skill_count_self,

        # Opp sprite
        hp_opp=hp_opp,
        hp_opp_ratio=hp_opp_ratio,
        hp_opp_max=hp_opp_max,
        energy_opp=os.energy,
        # Use initial_stats for non-speed stats (see self-sprite comment above)
        atk_opp=os.atk_with_modifiers,
        def_opp=os.def_with_modifiers,
        sp_atk_opp=os.sp_atk_with_modifiers,
        sp_def_opp=os.sp_def_with_modifiers,
        speed_opp=_compute_speed(os_stats, stat_stages_opp),
        damage_reduction_opp=damage_reduction_mod_opp,
        power_mult_opp=power_mult_mod_opp,
        damage_mult_opp=damage_mult_mod_opp,
        abnormal_count_opp=sum(abnormal_stacks_opp.values()),
        abnormal_stacks_opp=abnormal_stacks_opp,
        positive_count_opp=positive_count_opp,
        charged_opp=charged_opp,
        skill_elements_opp=skill_elements_opp,
        skill_element_counts_opp=skill_element_counts_opp,
        skill_element_count_opp=skill_element_count_opp,
        stat_stages_opp=stat_stages_opp,
        skills_energy_sum_opp=skills_energy_sum_opp,

        # Teams
        mark_count_own=mark_count_own,
        mark_stacks_own=mark_stacks_own,
        mark_count_opp=mark_count_opp,
        mark_stacks_opp=mark_stacks_opp,
        mark_bonus_own=mark_bonus_own,
        mark_count_both=mark_count_own + mark_count_opp,
        skill_count_own=skill_count_own or {},
        team_counters_own=team_counters_own or {},
        team_counters_opp=team_counters_opp or {},
        team_elements_own=team_elements_own,
        team_elements_opp=team_elements_opp,
        devotion_own=devotion_own or {},
        devotion_opp=devotion_opp or {},
        abnormal_stacks_battle=abnormal_stacks_battle or {},
        fainted_own=fainted_own,
        fainted_opp=fainted_opp,
        lives_own=lives_own,
        lives_opp=lives_opp,
        burst_triggered_count_own=burst_triggered_count_own,
        moe_team_stacks=moe_team_stacks,

        # Skill
        power_self=power_self,
        adjacent_power_sum=0,  # engine should compute this
        power_opp=power_opp,
        skill_type_self=getattr(sk, 'skill_type', ""),
        skill_type_opp=getattr(osk, 'skill_type', "") if osk else "",
        element_self=getattr(sk, 'element', ""),
        element_opp=getattr(osk, 'element', "") if osk else "",
        skill_tag_self=getattr(sk, 'tag', ""),
        combo_self=combo_self,
        element_advantage=_get_element_advantage(
            getattr(sk, 'element', ''),
            elements_opp,
        ) if sk else 1.0,
        energy_cost_self=energy_cost_self,
        energy_cost_reduction_self=energy_cost_reduction_self,
        energy_cost_opp=energy_cost_opp,
        damage_taken_this_turn=damage_taken_this_turn,
        damage_reduced_self=damage_reduced_self,
        prev_skill_type=prev_skill_type,
        prev_damage_taken_self=prev_damage_taken_self,
        prev_damage_taken_opp=prev_damage_taken_opp,

        # Skill tracking
        skill_index=skill_index,
        last_tick_damage_self=last_tick_damage_self,
        last_tick_damage_opp=last_tick_damage_opp,

        # Battlefield
        weather=weather,
        turn=turn,
        is_first=is_first,

        # Counters
        counter_values=counter_values or {},
    )


# ── Internal helpers ──

def _extract_sprite_effects(sprite: Sprite) -> tuple[dict[str, int], dict[str, int], bool, bool, int]:
    """返回 {stages, abnormals, charging, charged, positive}。

    优先使用 Sprite 增量维护的 O(1) 缓存；若 sprite 不含缓存接口
    （测试或其他调用方），回退到 O(N) 遍历。
    """
    # O(1) 路径：直接读取 Sprite 增量缓存，避免为每次 Ctx 构建分配临时 dict。
    if hasattr(sprite, '_cached_stages'):
        if getattr(sprite, '_effects_dirty', False):
            sprite._rebuild_effects_cache()
        return (
            sprite._cached_stages,
            sprite._cached_abnormals,
            sprite._cached_charging,
            sprite._cached_charged,
            sprite._cached_positive,
        )

    # 回退：O(N) 遍历 active_effects（测试/旧接口兼容）
    from backend.vm.effect import AbnormalEffect, StatBuffEffect, StateEffect

    stages: dict[str, int] = {}
    abnormals: dict[str, int] = {}
    charging = False
    charged = False
    positive = 0

    for e in getattr(sprite, 'active_effects', []):
        if isinstance(e, StatBuffEffect):
            stages[e.stat_key] = stages.get(e.stat_key, 0) + e.steps
            if e.steps > 0:
                positive += 1
        elif isinstance(e, AbnormalEffect):
            abnormals[e.name] = abnormals.get(e.name, 0) + e.stacks
        elif isinstance(e, StateEffect):
            if e.state_type == "charging":
                charging = True
            elif e.state_type == "charged":
                charged = True

    return stages, abnormals, charging, charged, positive
