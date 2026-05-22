"""Snapshot — build Ctx from mutable battle state.

The ONLY module in engine/ that depends on sim/ (prototype data structures).
When the prototype is replaced, only this module needs updating.

Accepts any object with the required attributes for skill parameters
(CompiledSkill, BattleSkill, Skill, or plain dict-like).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.vm.ctx import Ctx

if TYPE_CHECKING:
    from backend.sim.globals import GlobalEffects
    from backend.sim.sprite import Sprite


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
    devotion_own: dict[str, int] | None = None,
    devotion_opp: dict[str, int] | None = None,
    abnormal_stacks_battle: dict[str, int] | None = None,
    # BattleSkill reference (for _modifiers and synthesized power/energy/combo)
    battle_skill: Any = None,
) -> Ctx:
    """Build a VM Ctx snapshot from mutable battle objects.

    All non-sprite data is passed as keyword arguments so the caller
    (BattleEngine) controls what context to inject.
    """

    # ── Self sprite ──
    ss = self_sprite
    hp_self = ss.current_hp
    hp_self_max = ss.max_hp
    hp_self_ratio = hp_self / hp_self_max if hp_self_max > 0 else 0.0
    hp_self_missing_ratio = (hp_self_max - hp_self) / hp_self_max if hp_self_max > 0 else 0.0

    stat_stages_self = _extract_stat_stages(ss)
    abnormal_stacks_self = _extract_abnormal_stacks(ss)

    # Charging state
    is_charging_self = any(
        e.name == "charging" and e.category == "state" for e in ss.effects
    ) if hasattr(ss, 'effects') else False
    charged_self = any(
        e.name == "charged" and e.category == "state" for e in ss.effects
    ) if hasattr(ss, 'effects') else False

    # Skill elements carried
    skill_elements_self = frozenset(
        sk.element for sk in (ss.skills or []) if getattr(sk, 'element', None)
    ) if hasattr(ss, 'skills') else frozenset()

    # Energy cost sum by type/element/tag — accumulated by engine
    ecs = energy_cost_sum_self or {}

    # ── Opponent sprite ──
    os = opp_sprite
    hp_opp = os.current_hp
    hp_opp_max = os.max_hp
    hp_opp_ratio = hp_opp / hp_opp_max if hp_opp_max > 0 else 0.0
    hp_opp_missing_ratio = (hp_opp_max - hp_opp) / hp_opp_max if hp_opp_max > 0 else 0.0

    stat_stages_opp = _extract_stat_stages(os)
    abnormal_stacks_opp = _extract_abnormal_stacks(os)
    skill_elements_opp = frozenset(
        sk.element for sk in (os.skills or []) if getattr(sk, 'element', None)
    ) if hasattr(os, 'skills') else frozenset()

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
        pos_own, neg_own = g.get_marks(own_team)
        pos_opp, neg_opp = g.get_marks(opp_team)
        for m in pos_own + neg_own:
            mark_stacks_own[m.name] = mark_stacks_own.get(m.name, 0) + m.stacks
            mark_count_own += m.stacks
        for m in pos_opp + neg_opp:
            mark_stacks_opp[m.name] = mark_stacks_opp.get(m.name, 0) + m.stacks
            mark_count_opp += m.stacks
        mark_mult = g.mark_damage_mult(own_team, is_first)
        mark_bonus_own = mark_mult - 1.0

    weather = g.weather if g else ""

    # ── Self skill ──
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
    combo_mod = int(ss._modifiers.get("combo", 0))
    # combo_mult 不在 snapshot 阶段乘入 — 留给 adjust_damage 在
    # 同技能 combo 修改（set/add）之后再乘，确保正确的执行顺序。
    combo_self = max(1, combo_base + combo_mod)
    energy_cost_reduction_self = 0  # engine tracks this

    # ── Opp skill ──
    osk = opp_skill
    power_opp = osk.power if osk and hasattr(osk, 'power') else 0
    energy_cost_opp = osk.energy_cost if osk and hasattr(osk, 'energy_cost') else 0

    # ── Build Ctx ──
    return Ctx(
        # Self sprite
        hp_self=hp_self,
        hp_self_ratio=hp_self_ratio,
        hp_self_max=hp_self_max,
        hp_self_missing_ratio=hp_self_missing_ratio,
        energy_self=ss.energy,
        # Use initial_stats (base without stage multipliers) because
        # calc_damage applies stat_stages separately in the formula.
        # Using effective_stat would double-count stages and cause
        # division-by-zero when stages reduce a stat to 0.
        atk_self=ss.initial_stats.get("atk", 100),
        def_self=ss.initial_stats.get("def", 100),
        sp_atk_self=ss.initial_stats.get("sp_atk", 100),
        sp_def_self=ss.initial_stats.get("sp_def", 100),
        speed_self=ss.effective_stat("speed") if hasattr(ss, 'effective_stat') else ss.initial_stats.get("speed", 100),
        # Sprite + skill modifier delta 相加（同类型 buff 加性叠加，非相乘）
        damage_reduction_self=min(1.0,
            ss._modifiers.get("damage_reduction", 0.0)
            + skill_mods.get("damage_reduction", 0.0)),
        power_mult_self=1.0
            + (ss._modifiers.get("power_mult", 1.0) - 1.0)
            + (skill_mods.get("power_mult", 1.0) - 1.0),
        damage_mult_self=1.0
            + (ss._modifiers.get("damage_mult", 1.0) - 1.0)
            + (skill_mods.get("damage_mult", 1.0) - 1.0),
        energy_cost_mult_self=ss._modifiers.get("energy_cost_mult", 0.0),
        combo_mult_self=ss._modifiers.get("combo_mult", 0.0),
        life_drain_self=ss._modifiers.get("life_drain", 0.0),
        abnormal_count_self=sum(abnormal_stacks_self.values()),
        abnormal_stacks_self=abnormal_stacks_self,
        positive_count_self=_count_positive(ss),
        first_action_self=getattr(ss, 'first_action', True),
        charged_self=charged_self,
        is_charging_self=is_charging_self,
        self_koed=ss.is_fainted,
        times_entered_self=ss.counters.get("times_entered", 0),
        times_left_self=ss.counters.get("times_left", 0),
        elements_used_count_self=elements_used_count_self,
        skills_energy_sum_self=sum(
            getattr(s, 'energy_cost', 0) for s in (ss.skills or [])
        ),
        just_entered=getattr(ss, 'entry_turn', -1) == turn and turn > 0,
        skill_elements_self=skill_elements_self,
        stat_stages_self=stat_stages_self,
        energy_cost_sum_self=ecs,
        zero_cost_skill_count_self=sum(
            1 for s in (ss.skills or []) if getattr(s, 'energy_cost', 1) == 0
        ),

        # Opp sprite
        hp_opp=hp_opp,
        hp_opp_ratio=hp_opp_ratio,
        hp_opp_max=hp_opp_max,
        hp_opp_missing_ratio=hp_opp_missing_ratio,
        energy_opp=os.energy,
        # Use initial_stats for non-speed stats (see self-sprite comment above)
        atk_opp=os.initial_stats.get("atk", 100),
        def_opp=os.initial_stats.get("def", 100),
        sp_atk_opp=os.initial_stats.get("sp_atk", 100),
        sp_def_opp=os.initial_stats.get("sp_def", 100),
        speed_opp=os.effective_stat("speed") if hasattr(os, 'effective_stat') else os.initial_stats.get("speed", 100),
        damage_reduction_opp=os._modifiers.get("damage_reduction", 0.0),
        power_mult_opp=os._modifiers.get("power_mult", 1.0),
        damage_mult_opp=os._modifiers.get("damage_mult", 1.0),
        abnormal_count_opp=sum(abnormal_stacks_opp.values()),
        abnormal_stacks_opp=abnormal_stacks_opp,
        positive_count_opp=_count_positive(os),
        charged_opp=False,
        skill_elements_opp=skill_elements_opp,
        stat_stages_opp=stat_stages_opp,
        skills_energy_sum_opp=sum(
            getattr(s, 'energy_cost', 0) for s in (os.skills or [])
        ),

        # Teams
        mark_count_own=mark_count_own,
        mark_stacks_own=mark_stacks_own,
        mark_count_opp=mark_count_opp,
        mark_stacks_opp=mark_stacks_opp,
        mark_count_both=mark_count_own + mark_count_opp,
        mark_bonus_own=mark_bonus_own,
        skill_count_own=skill_count_own or {},
        team_counters_own=team_counters_own or {},
        team_counters_opp=team_counters_opp or {},
        devotion_own=devotion_own or {},
        devotion_opp=devotion_opp or {},
        abnormal_stacks_battle=abnormal_stacks_battle or {},
        fainted_own=fainted_own,
        fainted_opp=fainted_opp,
        burst_triggered_count_own=burst_triggered_count_own,

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
        energy_cost_self=energy_cost_self,
        energy_cost_reduction_self=energy_cost_reduction_self,
        energy_cost_opp=energy_cost_opp,
        counter_succeeded=counter_succeeded,
        was_countered=was_countered,
        prev_counter_succeeded=prev_counter_succeeded,
        damage_taken_this_turn=damage_taken_this_turn,
        damage_reduced_self=damage_reduced_self,
        devotion_triggered=devotion_triggered,
        prev_skill_type=prev_skill_type,
        target_fainted=target_fainted,
        prev_damage_taken_self=prev_damage_taken_self,
        prev_damage_taken_opp=prev_damage_taken_opp,

        # Skill tracking
        skill_index=skill_index,
        skill_position_changed=skill_position_changed,
        last_tick_damage_self=0,
        last_tick_damage_opp=0,

        # Battlefield
        weather=weather,
        last_tick_abnormal=last_tick_abnormal,
        last_tick_target=last_tick_target,
        abnormal_changed_name=abnormal_changed_name,
        abnormal_changed_target=abnormal_changed_target,
        abnormal_applied_name=abnormal_applied_name,
        abnormal_applied_target=abnormal_applied_target,
        skills_energy_changed_of=skills_energy_changed_of,
        positive_changed_of=positive_changed_of,
        energy_changed_of=energy_changed_of,
        turn_end=turn_end,
        turn=turn,
        is_first=is_first,
        opp_switched=opp_switched,
        self_switched=self_switched,

        # Counters
        counter_values=counter_values or {},
    )


# ── Internal helpers ──

def _extract_stat_stages(sprite: Sprite) -> dict[str, int]:
    """Extract stat stage changes from sprite effects.

    Returns {stat: total_steps} where steps are accumulated from stat effects.
    """
    stages: dict[str, int] = {}
    if not hasattr(sprite, 'effects'):
        return stages
    for e in sprite.effects:
        if getattr(e, 'category', '') == 'stat' and e.stat_key:
            stages[e.stat_key] = stages.get(e.stat_key, 0) + e.steps
    return stages


def _extract_abnormal_stacks(sprite: Sprite) -> dict[str, int]:
    """Extract abnormal stacks from sprite effects."""
    stacks: dict[str, int] = {}
    if not hasattr(sprite, 'effects'):
        return stacks
    for e in sprite.effects:
        if getattr(e, 'category', '') == 'abnormal':
            stacks[e.name] = stacks.get(e.name, 0) + e.stacks
    return stacks


def _count_positive(sprite: Sprite) -> int:
    """Count distinct positive stat effects on a sprite."""
    if not hasattr(sprite, 'effects'):
        return 0
    return sum(
        1 for e in sprite.effects
        if getattr(e, 'category', '') == 'stat' and e.steps > 0
    )
