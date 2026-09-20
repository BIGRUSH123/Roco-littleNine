//! snapshot — build_ctx：从可变状态构建 VM Ctx（移植自
//! backend/engine/snapshot.py）。Python 的缓存层不移植——按需重算等价。

use crate::battle_state::{BattleState, EffectKind, IMap};
use crate::resolve::Val;
use crate::statics;
use crate::vm_ctx::{Ctx, StrSet};
use serde_json::Value as J;
use std::collections::BTreeMap;

type FMap = BTreeMap<String, f64>;

/// build_ctx 的调用参数（除双方精灵外的注入项）。
#[derive(Debug, Clone, Default)]
pub struct BuildCtxOpts {
    pub team: &'static str,
    pub self_idx: usize,
    pub opp_idx: usize,
    /// 当前技能槽位（self sprite 的 skills 下标）
    pub self_skill_idx: Option<usize>,
    /// 对手当前技能槽位
    pub opp_skill_idx: Option<usize>,
    pub turn: i64,
    pub is_first: bool,
    /// 事件标志：换宠/脱离触发点注入（post_leave → self_switched，
    /// post_enemy_leave → opp_switched，post_ko → target_fainted）。
    pub self_switched: bool,
    pub opp_switched: bool,
    pub target_fainted: bool,
    /// 应对事件标志（py _make_ctx 的 counter_succeeded/was_countered/
    /// prev_counter_succeeded；技能内 when:counter_succeeded 依赖）
    pub counter_succeeded: bool,
    pub was_countered: bool,
    pub prev_counter_succeeded: bool,
    /// 传动后逐技能触发（py _fire_skill_position_changed 的
    /// skill_position_changed=True）
    pub skill_position_changed: bool,
    pub damage_taken_this_turn: i64,
    pub damage_reduced_self: i64,
    pub skill_index: i64,
    pub prev_skill_type: String,
    pub prev_damage_taken_self: bool,
    pub prev_damage_taken_opp: bool,
    pub last_tick_damage_self: i64,
    pub last_tick_damage_opp: i64,
    pub elements_used_count_self: i64,
    pub energy_cost_sum_self: IMap,
    pub skill_count_own: IMap,
    pub energy_cost_reduction_self: i64,
    pub adjacent_power_sum: i64,
    /// py execute_skill 传入的 burst_triggered_count(team)：本队已触发过的
    /// 不同迸发技能数（雷暴类威力/能耗成长依赖）
    pub burst_triggered_count_own: i64,
    /// py 以技能【记录】（CompiledSkill，battle_skill=None）构建 ctx 的覆盖：
    /// 应对效果注入路径（battle.py 1464 `_build_ctx(target, user, counter_record,
    /// None, ...)`）用。此时 power/combo/energy_cost/skill_type/element 全部取自
    /// 该技能记录，技能级 _modifiers 视为空。None = 走 BattleSkill 语义。
    pub self_skill_record: Option<SelfSkillRecord>,
}

/// py CompiledSkill 的头部字段子集（仅 ctx 构建所需）。
#[derive(Clone, Debug, Default)]
pub struct SelfSkillRecord {
    pub power: i64,
    pub combo: i64,
    pub energy_cost: i64,
    pub element: String,
    pub skill_type: String,
}

/// py `_extract_sprite_effects`：阶段/异常层数等。py 返回的是精灵
/// 【活缓存引用】——ctx 构建后的变化对后续读取可见；rust 需在使用点
/// 借助 `refresh_live_effects` 重取以镜像该语义。
pub fn sprite_effects_summary(sprite: &crate::battle_state::Sprite)
    -> (IMap, IMap, bool, bool, i64)
{    let mut stages: IMap = BTreeMap::new();
    let mut abnormals: IMap = BTreeMap::new();
    let mut charging = false;
    let mut charged = false;
    let mut positive = 0i64;
    for e in &sprite.active_effects {
        match &e.kind {
            EffectKind::StatBuff { stat_key, steps, .. } => {
                *stages.entry(stat_key.clone()).or_insert(0) += *steps;
                if *steps > 0 {
                    positive += 1;
                }
            }
            EffectKind::Abnormal { stacks, .. } => {
                *abnormals.entry(e.name.clone()).or_insert(0) += *stacks;
            }
            EffectKind::State { state_type, .. } => {
                if state_type == "charging" {
                    charging = true;
                } else if state_type == "charged" {
                    charged = true;
                }
            }
            _ => {}
        }
    }
    (stages, abnormals, charging, charged, positive)
}

/// _collect_skill_summary：(elements, counts, energy_sum, zero_cost)。
fn skill_summary(sprite: &crate::battle_state::Sprite)
    -> (StrSet, IMap, i64, i64)
{
    let mut elements: StrSet = StrSet::new();
    let mut counts: IMap = BTreeMap::new();
    let mut energy_sum = 0i64;
    let mut zero_cost = 0i64;
    for sk in &sprite.skills {
        let (el, cost) = if sk.nullified {
            (
                sk.element_override.clone(),
                sk.mech_energy_reduction + sk.modifiers.get("energy_cost").copied().unwrap_or(0.0) as i64,
            )
        } else {
            match &sk.element_override {
                s if !s.is_empty() => (s.clone(), sk.base.energy_cost
                    + sk.modifiers.get("energy_cost").copied().unwrap_or(0.0) as i64
                    + sk.mech_energy_reduction),
                _ => (
                    sk.base.element.clone(),
                    sk.base.energy_cost
                        + sk.modifiers.get("energy_cost").copied().unwrap_or(0.0) as i64
                        + sk.mech_energy_reduction,
                ),
            }
        };
        if !el.is_empty() {
            elements.insert(el.clone());
            *counts.entry(el).or_insert(0) += 1;
        }
        energy_sum += cost;
        if cost == 0 {
            zero_cost += 1;
        }
    }
    (elements, counts, energy_sum, zero_cost)
}

fn mark_aggregate(
    state: &BattleState,
    own_team: &str,
    opp_team: &str,
    is_first: bool,
) -> (IMap, i64, IMap, i64, f64) {
    let mut stacks_own: IMap = BTreeMap::new();
    let mut count_own = 0i64;
    let mut stacks_opp: IMap = BTreeMap::new();
    let mut count_opp = 0i64;
    let mut bonus_own = 0.0f64;
    if let Some(marks) = state.globals.mark_effects.get(own_team) {
        for m in marks {
            *stacks_own.entry(m.name.clone()).or_insert(0) += m.stacks;
            count_own += m.stacks;
            if m.damage_mult != 0.0 {
                let cond = m.condition.as_str();
                if cond.is_empty()
                    || (cond == "is_first" && is_first)
                    || (cond == "not_first" && !is_first)
                {
                    bonus_own += m.damage_mult * m.stacks as f64;
                }
            }
        }
    }
    if let Some(marks) = state.globals.mark_effects.get(opp_team) {
        for m in marks {
            *stacks_opp.entry(m.name.clone()).or_insert(0) += m.stacks;
            count_opp += m.stacks;
        }
    }
    (stacks_own, count_own, stacks_opp, count_opp, bonus_own)
}

pub fn build_ctx(state: &BattleState, o: &BuildCtxOpts) -> Ctx {
    let own_team = o.team;
    let opp_team = crate::battle_state::BattleState::opponent_of(own_team);
    let own_pi = if own_team == "A" { 0 } else { 1 };
    let opp_pi = 1 - own_pi;

    let ss = &state.players[own_pi].team[o.self_idx];
    let os = &state.players[opp_pi].team[o.opp_idx];

    // ── Self ──
    let hp_self = ss.current_hp;
    let hp_self_max = ss.max_hp;
    let hp_self_ratio = if hp_self_max > 0 { hp_self as f64 / hp_self_max as f64 } else { 0.0 };
    let elements_self: Vec<String> = ss.species.elements();
    let (stat_stages_self, abnormal_stacks_self, is_charging_self, charged_self, positive_self) =
        sprite_effects_summary(ss);
    let (skill_elements_self, skill_element_counts_self, skills_energy_sum_self, zero_cost_self) =
        skill_summary(ss);

    // ── Opp ──
    let hp_opp = os.current_hp;
    let hp_opp_max = os.max_hp;
    let hp_opp_ratio = if hp_opp_max > 0 { hp_opp as f64 / hp_opp_max as f64 } else { 0.0 };
    let elements_opp: Vec<String> = os.species.elements();
    let (stat_stages_opp, abnormal_stacks_opp, is_charging_opp, charged_opp, positive_opp) =
        sprite_effects_summary(os);
    let (skill_elements_opp, skill_element_counts_opp, skills_energy_sum_opp, _zc_opp) =
        skill_summary(os);
    let skill_element_count_self = skill_elements_self.len() as i64;
    let skill_element_count_opp = skill_elements_opp.len() as i64;

    // ── Marks ──
    let (mark_stacks_own, mark_count_own, mark_stacks_opp, mark_count_opp, mark_bonus_own) =
        mark_aggregate(state, own_team, opp_team, o.is_first);

    // ── Skill ──
    let bs = o.self_skill_idx.and_then(|i| ss.skills.get(i));
    let (power_self, combo_base, energy_cost_self, skill_mods): (i64, i64, i64, &FMap) =
        match (bs, o.self_skill_record.as_ref()) {
            // BattleSkill 语义：power/energy_cost 含技能级修正，combo 取 base
            (Some(b), _) => (b.power(), b.base.combo, b.energy_cost(), &b.modifiers),
            // 技能记录语义（py CompiledSkill）：全部取记录原值，_modifiers 为空
            (None, Some(r)) => (r.power, r.combo, r.energy_cost, empty_fmap()),
            (None, None) => (0, 1, 0, empty_fmap()),
        };
    let combo_mod = ss.modifiers.get("combo").copied().unwrap_or(0.0) as i64;
    let combo_set = ss.modifiers.get("combo_set").copied().unwrap_or(0.0) as i64;
    let combo_self = if combo_set > 0 {
        combo_set.max(1)
    } else {
        (combo_base + combo_mod).max(1)
    };

    // ── Opp skill ──
    let osk = o.opp_skill_idx.and_then(|i| os.skills.get(i));
    let (power_opp, energy_cost_opp, skill_type_opp, element_opp) = match osk {
        Some(k) => (k.power(), k.energy_cost(), k.skill_type(), k.element()),
        None => (0, 0, String::new(), String::new()),
    };

    // ── 修正值（_modifiers 直读）──
    let dr_mod_self = ss.modifier("damage_reduction", 0.0);
    let pm_mod_self = ss.modifier("power_mult", 1.0);
    let dm_mod_self = ss.modifier("damage_mult", 1.0);
    let ecm_mod_self = ss.modifier("energy_cost_mult", 0.0);
    let cm_mod_self = ss.modifier("combo_mult", 0.0);
    let ld_mod_self = ss.modifier("life_drain", 0.0);
    let dr_mod_opp = os.modifier("damage_reduction", 0.0);
    let pm_mod_opp = os.modifier("power_mult", 1.0);
    let dm_mod_opp = os.modifier("damage_mult", 1.0);

    // speed：self 带 modifiers，opp 不带（Python 不对称，镜像）
    let speed_base_self = (ss
        .initial_stats
        .get("speed")
        .copied()
        .unwrap_or(100)
        + stat_stages_self.get("speed").copied().unwrap_or(0) * 10)
        .max(0);
    let speed_mod_self = ss.modifier("speed", 0.0);
    let speed_self = if speed_mod_self != 0.0 {
        (crate::damage::py_round(speed_base_self as f64 * (1.0 + speed_mod_self))).max(1)
    } else {
        speed_base_self
    };
    let speed_opp = (os
        .initial_stats
        .get("speed")
        .copied()
        .unwrap_or(100)
        + stat_stages_opp.get("speed").copied().unwrap_or(0) * 10)
        .max(0);

    // ── 队伍聚合 ──
    let mut fainted_own = 0i64;
    let mut team_elements_own: StrSet = StrSet::new();
    let mut moe_team_stacks = 0i64;
    for (i, s) in state.players[own_pi].team.iter().enumerate() {
        if s.is_fainted() {
            fainted_own += 1;
        }
        for el in s.species.elements() {
            team_elements_own.insert(el);
        }
        if i != o.self_idx {
            moe_team_stacks += s.get_stacks("萌化");
        }
    }
    let mut fainted_opp = 0i64;
    let mut team_elements_opp: StrSet = StrSet::new();
    for s in state.players[opp_pi].team.iter() {
        if s.is_fainted() {
            fainted_opp += 1;
        }
        for el in s.species.elements() {
            team_elements_opp.insert(el);
        }
    }

    // 全场异常层数合计
    let mut abnormal_stacks_battle: IMap = BTreeMap::new();
    for (k, v) in &abnormal_stacks_self {
        *abnormal_stacks_battle.entry(k.clone()).or_insert(0) += v;
    }
    for (k, v) in &abnormal_stacks_opp {
        *abnormal_stacks_battle.entry(k.clone()).or_insert(0) += v;
    }

    // ── 事件上下文 ──
    let mut event = crate::vm_ctx::EventContext::default();
    event.self_koed = ss.is_fainted();
    event.turn_end = false;
    event.positive_changed_stat = String::new();
    event.positive_changed_steps = 0;
    event.sprite_left_of = String::new();
    event.self_switched = o.self_switched;
    event.opp_switched = o.opp_switched;
    event.target_fainted = o.target_fainted;
    event.counter_succeeded = o.counter_succeeded;
    event.was_countered = o.was_countered;
    event.prev_counter_succeeded = o.prev_counter_succeeded;
    event.skill_position_changed = o.skill_position_changed;
    let _ = &mut event;

    // skill_type_self / element_self / tag 来自当前技能（BattleSkill 语义；
    // 技能记录语义下取记录字段，tag 为空——py getattr(sk,'tag','') 对
    // CompiledSkill 恒为 ""）
    let (skill_type_self, element_self, skill_tag_self) = match (bs, o.self_skill_record.as_ref()) {
        (Some(b), _) => (b.skill_type(), b.element(), String::new()),
        (None, Some(r)) => (r.skill_type.clone(), r.element.clone(), String::new()),
        (None, None) => (String::new(), String::new(), String::new()),
    };

    let element_advantage = if !element_self.is_empty() && !elements_opp.is_empty() {
        statics::element_mult(&element_self, &elements_opp)
    } else {
        1.0
    };

    let mut ctx = Ctx {
        event,        bloodline_self: ss.bloodline.clone(),
        bloodline_opp: os.bloodline.clone(),
        elements_self: elements_self.clone(),
        elements_opp: elements_opp.clone(),
        hp_self,
        hp_self_ratio,
        hp_self_max,
        hp_opp,
        hp_opp_ratio,
        hp_opp_max,
        energy_self: ss.energy,
        energy_opp: os.energy,
        speed_opp,
        priority_self: ss.modifiers.get("priority").copied().unwrap_or(0.0) as i64,
        atk_self: ss.stat_with_modifiers("atk"),
        def_self: ss.stat_with_modifiers("def"),
        sp_atk_self: ss.stat_with_modifiers("sp_atk"),
        sp_def_self: ss.stat_with_modifiers("sp_def"),
        speed_self,
        damage_reduction_self: (dr_mod_self + skill_mods.get("damage_reduction").copied().unwrap_or(0.0)).min(1.0),
        damage_reduction_opp: dr_mod_opp,
        power_mult_self: 1.0 + (pm_mod_self - 1.0)
            + (skill_mods.get("power_mult").copied().unwrap_or(1.0) - 1.0),
        damage_mult_self: 1.0 + (dm_mod_self - 1.0)
            + (skill_mods.get("damage_mult").copied().unwrap_or(1.0) - 1.0),
        energy_cost_mult_self: ecm_mod_self,
        combo_mult_self: cm_mod_self,
        life_drain_self: ld_mod_self,
        atk_opp: os.stat_with_modifiers("atk"),
        def_opp: os.stat_with_modifiers("def"),
        sp_atk_opp: os.stat_with_modifiers("sp_atk"),
        sp_def_opp: os.stat_with_modifiers("sp_def"),
        abnormal_count_self: abnormal_stacks_self.values().sum(),
        abnormal_stacks_self,
        positive_count_self: positive_self,
        first_action_self: ss.first_action,
        first_action_battle_self: ss.first_action_battle,
        charged_self,
        is_charging_self,
        is_charging_opp,
        times_entered_self: ss.counters.get("times_entered").copied().unwrap_or(0),
        times_left_self: ss.counters.get("times_left").copied().unwrap_or(0),
        elements_used_count_self: o.elements_used_count_self,
        skills_energy_sum_self,
        just_entered: ss.entry_turn == o.turn && o.turn >= 0,
        skill_elements_self,
        skill_element_counts_self,
        skill_element_count_self,
        stat_stages_self,
        energy_cost_sum_self: o.energy_cost_sum_self.clone(),
        zero_cost_skill_count_self: zero_cost_self,
        power_mult_opp: pm_mod_opp,
        damage_mult_opp: dm_mod_opp,
        abnormal_count_opp: abnormal_stacks_opp.values().sum(),
        abnormal_stacks_opp,
        positive_count_opp: positive_opp,
        charged_opp,
        skill_elements_opp,
        skill_element_counts_opp,
        skill_element_count_opp,
        stat_stages_opp,
        skills_energy_sum_opp,
        mark_count_own,
        mark_stacks_own,
        mark_count_opp,
        mark_stacks_opp,
        mark_bonus_own,
        mark_count_both: mark_count_own + mark_count_opp,
        skill_count_own: o.skill_count_own.clone(),
        team_counters_own: state
            .team_counters
            .get(own_team)
            .cloned()
            .unwrap_or_default(),
        team_counters_opp: state
            .team_counters
            .get(opp_team)
            .cloned()
            .unwrap_or_default(),
        team_elements_own,
        team_elements_opp,
        devotion_own: state.players[own_pi].devotion.clone(),
        devotion_opp: state.players[opp_pi].devotion.clone(),
        abnormal_stacks_battle,
        fainted_own,
        fainted_opp,
        lives_own: state.players[own_pi].lives,
        lives_opp: state.players[opp_pi].lives,
        burst_triggered_count_own: o.burst_triggered_count_own,
        moe_team_stacks,
        power_self,
        adjacent_power_sum: o.adjacent_power_sum,
        power_opp,
        skill_type_self: skill_type_self.clone(),
        element_self: element_self.clone(),
        element_advantage,
        skill_tag_self,
        combo_self,
        energy_cost_self,
        energy_cost_reduction_self: o.energy_cost_reduction_self,
        energy_cost_opp,
        skill_name_self: bs.map(|b| b.name()).unwrap_or_default(),
        damage_taken_this_turn: o.damage_taken_this_turn,
        damage_reduced_self: o.damage_reduced_self,
        prev_skill_type: o.prev_skill_type.clone(),
        prev_damage_taken_self: o.prev_damage_taken_self,
        prev_damage_taken_opp: o.prev_damage_taken_opp,
        skill_index: o.skill_index,
        last_tick_damage_self: o.last_tick_damage_self,
        last_tick_damage_opp: o.last_tick_damage_opp,
        weather: state.globals.weather.clone(),
        turn: o.turn,
        is_first: o.is_first,
        counter_values: state.counter_values.clone(),
        ..Ctx::default()
    };
    // osk 的 skill_type/element
    ctx.skill_type_opp = skill_type_opp;
    ctx.element_opp = element_opp;
    ctx
}

fn empty_fmap() -> &'static FMap {
    static EMPTY: std::sync::OnceLock<FMap> = std::sync::OnceLock::new();
    EMPTY.get_or_init(FMap::new)
}

// Val 引用保留（skill_where 计算等后续使用）
#[allow(unused)]
fn _keep(_: &Val, _: &J) {}
