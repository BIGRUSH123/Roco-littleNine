# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True

"""Cython 优化的 build_ctx 实现 - Week 1

这个文件包含 build_ctx 的 Cython 优化版本。
通过将 Python 对象数据提取到 C 类型变量，大幅减少对象访问开销。
"""

cimport cython
from libc.math cimport round as c_round
from cpython.dict cimport PyDict_GetItem, PyDict_Contains

# 导入 Python 类型
from backend.vm.ctx import Ctx, EventContext
from backend.sim.resolver import _TYPE_CHART


# ============================================================================
# C 数据结构定义
# ============================================================================

cdef int SPEED_STEP = 10


# Sprite 的所有必要数据（C 结构体）
# 这个结构体存储从 Sprite 对象提取的所有数据，
# 避免重复的 Python 对象属性访问。
cdef struct SpriteData:
    # 基础属性
    int current_hp
    int max_hp
    double hp_ratio
    int energy

    # 六维属性（已应用修正）
    int atk
    int def_stat
    int sp_atk
    int sp_def
    int speed

    # 修正值
    double damage_reduction
    double power_mult
    double damage_mult
    double energy_cost_mult
    double combo_mult
    double life_drain

    # 计数器
    int times_entered
    int times_left

    # 状态
    bint is_fainted
    bint first_action
    bint first_action_battle
    int entry_turn

    # 能力值等级
    int stat_stage_atk
    int stat_stage_def
    int stat_stage_sp_atk
    int stat_stage_sp_def
    int stat_stage_speed

    # 效果统计
    int abnormal_count
    int positive_count
    bint is_charging
    bint charged

    # 技能统计
    int skill_element_count
    int skills_energy_sum
    int zero_cost_skill_count


# 技能数据
cdef struct SkillData:
    int power
    int energy_cost
    int combo


# ============================================================================
# 数据提取函数
# ============================================================================

@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline double safe_hp_ratio(int current_hp, int max_hp) nogil:
    """安全计算 HP 比率（避免除零）"""
    if max_hp > 0:
        return <double>current_hp / <double>max_hp
    return 0.0


@cython.boundscheck(False)
@cython.wraparound(False)
cdef SpriteData extract_sprite_data(sprite, dict stat_stages) except *:
    """从 Sprite 对象提取所有需要的数据到 C 结构体

    这是性能优化的核心：一次性提取所有数据，
    后续计算只使用 C 变量，避免重复的 Python 对象访问。

    Args:
        sprite: Sprite 对象
        stat_stages: 能力值等级字典

    Returns:
        SpriteData: 包含所有必要数据的 C 结构体
    """
    cdef SpriteData data
    cdef dict counters

    # 基础属性
    data.current_hp = sprite.current_hp
    data.max_hp = sprite.max_hp
    data.hp_ratio = safe_hp_ratio(data.current_hp, data.max_hp)
    data.energy = sprite.energy

    # 六维属性（已应用修正）
    data.atk = sprite.atk_with_modifiers
    data.def_stat = sprite.def_with_modifiers
    data.sp_atk = sprite.sp_atk_with_modifiers
    data.sp_def = sprite.sp_def_with_modifiers
    # speed 需要特殊计算，暂时设为 0
    data.speed = 0

    # 修正值（使用 Sprite 缓存的 property）
    data.damage_reduction = sprite.damage_reduction_modifier
    data.power_mult = sprite.power_mult_modifier
    data.damage_mult = sprite.damage_mult_modifier
    data.energy_cost_mult = sprite.energy_cost_mult_modifier
    data.combo_mult = sprite.combo_mult_modifier
    data.life_drain = sprite.life_drain_modifier

    # 计数器
    counters = sprite.counters
    data.times_entered = counters.get("times_entered", 0)
    data.times_left = counters.get("times_left", 0)

    # 状态
    data.is_fainted = sprite.is_fainted
    data.first_action = sprite.first_action
    data.first_action_battle = sprite.first_action_battle
    data.entry_turn = sprite.entry_turn

    # 能力值等级
    data.stat_stage_atk = stat_stages.get("atk", 0)
    data.stat_stage_def = stat_stages.get("def", 0)
    data.stat_stage_sp_atk = stat_stages.get("sp_atk", 0)
    data.stat_stage_sp_def = stat_stages.get("sp_def", 0)
    data.stat_stage_speed = stat_stages.get("speed", 0)

    # 效果统计（由外部传入）
    data.abnormal_count = 0
    data.positive_count = 0
    data.is_charging = False
    data.charged = False

    # 技能统计（由外部传入）
    data.skill_element_count = 0
    data.skills_energy_sum = 0
    data.zero_cost_skill_count = 0

    return data


@cython.boundscheck(False)
@cython.wraparound(False)
cdef SkillData extract_skill_data(skill, battle_skill) except *:
    """提取技能数据"""
    cdef SkillData data

    if battle_skill is not None:
        data.power = battle_skill.power
        data.energy_cost = battle_skill.energy_cost
        if hasattr(battle_skill, 'base'):
            data.combo = battle_skill.base.combo if hasattr(battle_skill.base, 'combo') else 1
        else:
            data.combo = getattr(skill, 'combo', 1) if skill else 1
    elif skill is not None:
        data.power = skill.power if hasattr(skill, 'power') else 0
        data.energy_cost = skill.energy_cost if hasattr(skill, 'energy_cost') else 0
        data.combo = skill.combo if hasattr(skill, 'combo') else 1
    else:
        data.power = 0
        data.energy_cost = 0
        data.combo = 1

    return data


# ============================================================================
# 核心优化函数（已验证）
# ============================================================================

@cython.boundscheck(False)
@cython.wraparound(False)
cpdef int compute_speed_self_cy(
    dict initial_stats,
    dict modifiers,
    dict stat_stages
):
    """Cython 版本的 _compute_speed_self

    已验证：54x 加速
    """
    cdef int base_speed = initial_stats.get("speed", 100)
    cdef int speed_stage = stat_stages.get("speed", 0)
    cdef double speed_mod = modifiers.get("speed", 0.0)
    cdef int base

    base = base_speed + speed_stage * SPEED_STEP
    if base < 0:
        base = 0

    if speed_mod != 0.0:
        return max(1, <int>c_round(base * (1.0 + speed_mod)))

    return base


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef int compute_speed_cy(
    dict stats,
    dict stat_stages
):
    """Cython 版本的 _compute_speed

    已验证：3x 加速
    """
    cdef int speed = stats.get("speed", 100)
    cdef int stage = stat_stages.get("speed", 0)
    cdef int result = speed + stage * SPEED_STEP

    return max(0, result)


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef double get_element_advantage_cy(
    str atk_element,
    list def_elements,
    dict type_chart
):
    """Cython 版本的 _get_element_advantage

    已验证：1.8x 加速
    """
    cdef double mult = 1.0
    cdef dict chart
    cdef str def_elem

    if not atk_element or not def_elements:
        return 1.0

    chart = type_chart.get(atk_element, {})
    if not chart:
        return 1.0

    for def_elem in def_elements:
        mult *= chart.get(def_elem, 1.0)

    return mult


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef tuple collect_skill_summary_cy(
    list skills
):
    """Cython 版本的 _collect_skill_summary（简化版）"""
    cdef set elements = set()
    cdef dict element_counts = {}
    cdef int energy_sum = 0
    cdef int zero_cost_count = 0
    cdef int cost
    cdef str element

    for skill in skills:
        element = getattr(skill, 'element', '')
        if element:
            elements.add(element)
            if element in element_counts:
                element_counts[element] += 1
            else:
                element_counts[element] = 1

        cost = getattr(skill, 'energy_cost', 0)
        energy_sum += cost
        if cost == 0:
            zero_cost_count += 1

    return (frozenset(elements), element_counts, energy_sum, zero_cost_count)


# ============================================================================
# Week 1 Day 3-4 - build_ctx_cy 完整实现
# ============================================================================

cpdef build_ctx_cy(
    self_sprite,
    opp_sprite,
    self_skill,
    opp_skill=None,
    globals_=None,
    str team="A",
    int turn=0,
    bint is_first=False,
    bint opp_switched=False,
    bint self_switched=False,
    bint counter_succeeded=False,
    bint was_countered=False,
    bint prev_counter_succeeded=False,
    int damage_taken_this_turn=0,
    bint target_fainted=False,
    int damage_reduced_self=0,
    int skill_index=0,
    bint skill_position_changed=False,
    bint devotion_triggered=False,
    bint prev_damage_taken_self=False,
    bint prev_damage_taken_opp=False,
    str prev_skill_type="",
    str last_tick_abnormal="",
    str last_tick_target="",
    str abnormal_changed_name="",
    str abnormal_changed_target="",
    str abnormal_applied_name="",
    str abnormal_applied_target="",
    str skills_energy_changed_of="",
    str positive_changed_of="",
    str energy_changed_of="",
    str damage_taken_of="",
    int last_tick_damage_self=0,
    int last_tick_damage_opp=0,
    bint turn_end=False,
    dict counter_values=None,
    dict skill_count_own=None,
    dict team_counters_own=None,
    dict team_counters_opp=None,
    dict energy_cost_sum_self=None,
    int elements_used_count_self=0,
    int burst_triggered_count_own=0,
    int fainted_own=0,
    int fainted_opp=0,
    int lives_own=5,
    int lives_opp=5,
    team_elements_own=frozenset(),
    team_elements_opp=frozenset(),
    dict devotion_own=None,
    dict devotion_opp=None,
    dict abnormal_stacks_battle=None,
    int moe_team_stacks=0,
    battle_skill=None,
):
    """Cython 优化版本的 build_ctx

    通过将 Python 对象数据提取到 C 类型变量，大幅减少对象访问开销。
    """
    # ── Self sprite 数据提取 ──
    cdef dict ss_mods = self_sprite._modifiers
    cdef dict ss_stats = self_sprite.initial_stats
    cdef dict ss_counters = self_sprite.counters

    cdef int hp_self = self_sprite.current_hp
    cdef int hp_self_max = self_sprite.max_hp
    cdef double hp_self_ratio = safe_hp_ratio(hp_self, hp_self_max)
    cdef int priority_self = int(ss_mods.get("priority", 0))
    cdef int energy_self = self_sprite.energy

    # Bloodline & elements
    species_self = self_sprite.species
    bloodline_self = self_sprite.bloodline
    elements_self = tuple(species_self.elements) if species_self else ()

    # 调用 Python 的 _extract_sprite_effects (已高度优化，使用 Sprite 缓存)
    from backend.engine.snapshot import _extract_sprite_effects
    (
        stat_stages_self,
        abnormal_stacks_self,
        is_charging_self,
        charged_self,
        positive_count_self,
    ) = _extract_sprite_effects(self_sprite)

    # 调用 Cython 版本的技能摘要
    (
        skill_elements_self,
        skill_element_counts_self,
        skills_energy_sum_self,
        zero_cost_skill_count_self,
    ) = collect_skill_summary_cy(getattr(self_sprite, 'skills', None) or [])

    cdef int skill_element_count_self = len(skill_elements_self)

    # 四维属性（已应用修正）
    cdef int atk_self = self_sprite.atk_with_modifiers
    cdef int def_self = self_sprite.def_with_modifiers
    cdef int sp_atk_self = self_sprite.sp_atk_with_modifiers
    cdef int sp_def_self = self_sprite.sp_def_with_modifiers
    cdef int speed_self = compute_speed_self_cy(ss_stats, ss_mods, stat_stages_self)

    # 修正值（使用 Sprite 缓存）
    cdef double damage_reduction_mod_self = self_sprite.damage_reduction_modifier
    cdef double power_mult_mod_self = self_sprite.power_mult_modifier
    cdef double damage_mult_mod_self = self_sprite.damage_mult_modifier
    cdef double energy_cost_mult_mod_self = self_sprite.energy_cost_mult_modifier
    cdef double combo_mult_mod_self = self_sprite.combo_mult_modifier
    cdef double life_drain_mod_self = self_sprite.life_drain_modifier

    # 计数器
    cdef int times_entered_val = ss_counters.get("times_entered", 0)
    cdef int times_left_val = ss_counters.get("times_left", 0)

    # 状态
    cdef bint first_action_self = self_sprite.first_action
    cdef bint first_action_battle_self = self_sprite.first_action_battle
    cdef bint is_fainted_self = self_sprite.is_fainted
    cdef int entry_turn = self_sprite.entry_turn
    cdef bint just_entered = (entry_turn == turn and turn >= 0)

    # Energy cost sum
    ecs = energy_cost_sum_self or {}

    # ── Opponent sprite 数据提取 ──
    cdef dict os_mods = opp_sprite._modifiers
    cdef dict os_stats = opp_sprite.initial_stats

    cdef int hp_opp = opp_sprite.current_hp
    cdef int hp_opp_max = opp_sprite.max_hp
    cdef double hp_opp_ratio = safe_hp_ratio(hp_opp, hp_opp_max)
    cdef int energy_opp = opp_sprite.energy

    # Bloodline & elements
    species_opp = opp_sprite.species
    bloodline_opp = opp_sprite.bloodline if opp_sprite else ''
    elements_opp = tuple(species_opp.elements) if species_opp else ()

    # 效果提取
    (
        stat_stages_opp,
        abnormal_stacks_opp,
        is_charging_opp,
        charged_opp,
        positive_count_opp,
    ) = _extract_sprite_effects(opp_sprite)

    # 技能摘要
    (
        skill_elements_opp,
        skill_element_counts_opp,
        skills_energy_sum_opp,
        _zero_cost_skill_count_opp,
    ) = collect_skill_summary_cy(getattr(opp_sprite, 'skills', None) or [])

    cdef int skill_element_count_opp = len(skill_elements_opp)

    # 四维属性
    cdef int atk_opp = opp_sprite.atk_with_modifiers
    cdef int def_opp = opp_sprite.def_with_modifiers
    cdef int sp_atk_opp = opp_sprite.sp_atk_with_modifiers
    cdef int sp_def_opp = opp_sprite.sp_def_with_modifiers
    cdef int speed_opp = compute_speed_cy(os_stats, stat_stages_opp)

    # 修正值
    cdef double damage_reduction_mod_opp = opp_sprite.damage_reduction_modifier
    cdef double power_mult_mod_opp = opp_sprite.power_mult_modifier
    cdef double damage_mult_mod_opp = opp_sprite.damage_mult_modifier

    # ── Teams (from GlobalEffects) ──
    cdef str own_team = team
    cdef str opp_team = "B" if team == "A" else "A"
    cdef dict mark_stacks_own = {}
    cdef int mark_count_own = 0
    cdef dict mark_stacks_opp = {}
    cdef int mark_count_opp = 0
    cdef double mark_bonus_own = 0.0
    cdef str weather = ""

    # 导入 MarkEffect 类型
    from backend.vm.effect import MarkEffect

    if globals_:
        for m in globals_.mark_effects.get(own_team, ()):
            if not isinstance(m, MarkEffect):
                continue
            mark_stacks_own[m.name] = mark_stacks_own.get(m.name, 0) + m.stacks
            mark_count_own += m.stacks
            if m.damage_mult:
                cond = m.condition
                if cond == '' or (cond == 'is_first' and is_first) or (cond == 'not_first' and not is_first):
                    mark_bonus_own += m.damage_mult * m.stacks
        for m in globals_.mark_effects.get(opp_team, ()):
            if not isinstance(m, MarkEffect):
                continue
            mark_stacks_opp[m.name] = mark_stacks_opp.get(m.name, 0) + m.stacks
            mark_count_opp += m.stacks
        weather = globals_.weather if globals_ else ""

    # ── Skill ──
    sk = self_skill
    bs = battle_skill
    skill_mods = getattr(bs, '_modifiers', {}) if bs is not None else getattr(sk, '_modifiers', {}) if sk else {}

    cdef int power_self
    cdef int combo_base
    cdef int energy_cost_self
    cdef int combo_mod
    cdef int combo_set
    cdef int combo_self

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
    combo_self = max(1, combo_set) if combo_set > 0 else max(1, combo_base + combo_mod)

    cdef int energy_cost_reduction_self = 0  # engine tracks this

    # ── Opp skill ──
    osk = opp_skill
    cdef int power_opp = osk.power if osk and hasattr(osk, 'power') else 0
    cdef int energy_cost_opp = osk.energy_cost if osk and hasattr(osk, 'energy_cost') else 0

    # Skill 属性
    skill_type_self = getattr(sk, 'skill_type', "")
    skill_type_opp = getattr(osk, 'skill_type', "") if osk else ""
    element_self = getattr(sk, 'element', "")
    element_opp = getattr(osk, 'element', "") if osk else ""
    skill_tag_self = getattr(sk, 'tag', "")

    # Element advantage (使用 Cython 优化版本)
    cdef double element_advantage = get_element_advantage_cy(
        element_self,
        list(elements_opp),
        _TYPE_CHART
    ) if sk else 1.0

    # ── Build EventContext ──
    event_ctx = EventContext(
        counter_succeeded=counter_succeeded,
        was_countered=was_countered,
        prev_counter_succeeded=prev_counter_succeeded,
        target_fainted=target_fainted,
        self_koed=is_fainted_self,
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

    # ── 计算 modifier 合并值 ──
    cdef double damage_reduction_self = min(1.0,
        damage_reduction_mod_self + skill_mods.get("damage_reduction", 0.0))
    cdef double power_mult_self = 1.0 + (power_mult_mod_self - 1.0) + (skill_mods.get("power_mult", 1.0) - 1.0)
    cdef double damage_mult_self = 1.0 + (damage_mult_mod_self - 1.0) + (skill_mods.get("damage_mult", 1.0) - 1.0)

    # ── Build Ctx ──
    return Ctx(
        event=event_ctx,
        # Bloodline / Elements
        bloodline_self=bloodline_self,
        bloodline_opp=bloodline_opp,
        elements_self=elements_self,
        elements_opp=elements_opp,
        # Self sprite
        hp_self=hp_self,
        hp_self_ratio=hp_self_ratio,
        hp_self_max=hp_self_max,
        energy_self=energy_self,
        priority_self=priority_self,
        atk_self=atk_self,
        def_self=def_self,
        sp_atk_self=sp_atk_self,
        sp_def_self=sp_def_self,
        speed_self=speed_self,
        damage_reduction_self=damage_reduction_self,
        power_mult_self=power_mult_self,
        damage_mult_self=damage_mult_self,
        energy_cost_mult_self=energy_cost_mult_mod_self,
        combo_mult_self=combo_mult_mod_self,
        life_drain_self=life_drain_mod_self,
        abnormal_count_self=sum(abnormal_stacks_self.values()),
        abnormal_stacks_self=abnormal_stacks_self,
        positive_count_self=positive_count_self,
        first_action_self=first_action_self,
        first_action_battle_self=first_action_battle_self,
        charged_self=charged_self,
        is_charging_self=is_charging_self,
        is_charging_opp=is_charging_opp,
        times_entered_self=times_entered_val,
        times_left_self=times_left_val,
        elements_used_count_self=elements_used_count_self,
        skills_energy_sum_self=skills_energy_sum_self,
        just_entered=just_entered,
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
        energy_opp=energy_opp,
        atk_opp=atk_opp,
        def_opp=def_opp,
        sp_atk_opp=sp_atk_opp,
        sp_def_opp=sp_def_opp,
        speed_opp=speed_opp,
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
        adjacent_power_sum=0,
        power_opp=power_opp,
        skill_type_self=skill_type_self,
        skill_type_opp=skill_type_opp,
        element_self=element_self,
        element_opp=element_opp,
        skill_tag_self=skill_tag_self,
        combo_self=combo_self,
        element_advantage=element_advantage,
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
