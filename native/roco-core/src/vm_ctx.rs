//! ctx — 回合快照（只读寄存器组）+ 地址映射（移植自 backend/vm/ctx.py）。
//!
//! 每次技能调用构建一个全新 Ctx，因此技能 #2 能观察到技能 #1 的效果。
//! 字段与 Python Ctx dataclass 一一对应，缺一不可（cond/resolve 按
//! 字段名寻址）。

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// 确定性序列化/比较用的有序集合。
pub type StrSet = BTreeSet<String>;
pub type IntMap = BTreeMap<String, i64>;
pub type F64Map = BTreeMap<String, f64>;

/// 每次触发的事件标志（EventContext）——描述"刚发生了什么"，
/// 不是快照状态。非观察者评估期间全部为默认值。
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct EventContext {
    #[serde(default)]
    pub counter_succeeded: bool,
    #[serde(default)]
    pub was_countered: bool,
    #[serde(default)]
    pub prev_counter_succeeded: bool,
    #[serde(default)]
    pub target_fainted: bool,
    #[serde(default)]
    pub self_koed: bool,
    #[serde(default)]
    pub opp_switched: bool,
    #[serde(default)]
    pub self_switched: bool,
    #[serde(default)]
    pub sprite_left_of: String,
    #[serde(default)]
    pub turn_end: bool,
    #[serde(default)]
    pub skill_position_changed: bool,
    #[serde(default)]
    pub devotion_triggered: bool,
    #[serde(default)]
    pub last_tick_abnormal: String,
    #[serde(default)]
    pub last_tick_target: String,
    #[serde(default)]
    pub abnormal_changed_name: String,
    #[serde(default)]
    pub abnormal_changed_target: String,
    #[serde(default)]
    pub abnormal_applied_name: String,
    #[serde(default)]
    pub abnormal_applied_target: String,
    #[serde(default)]
    pub skills_energy_changed_of: String,
    #[serde(default)]
    pub positive_changed_of: String,
    #[serde(default)]
    pub positive_changed_stat: String,
    #[serde(default)]
    pub positive_changed_steps: i64,
    #[serde(default)]
    pub energy_changed_of: String,
    #[serde(default)]
    pub heal_of: String,
    #[serde(default)]
    pub damage_taken_of: String,
}

/// 回合快照。字段名与 Python 版一致（`_self`/`_opp`/`_own` 后缀体系）。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Ctx {
    #[serde(default)]
    pub event: EventContext,

    // ── 己方精灵 ──
    #[serde(default)]
    pub hp_self: i64,
    #[serde(default = "default_one_f")]
    pub hp_self_ratio: f64,
    #[serde(default = "default_hundred")]
    pub hp_self_max: i64,
    #[serde(default)]
    pub energy_self: i64,
    #[serde(default = "default_hundred")]
    pub atk_self: i64,
    #[serde(default = "default_hundred")]
    pub def_self: i64,
    #[serde(default = "default_hundred")]
    pub sp_atk_self: i64,
    #[serde(default = "default_hundred")]
    pub sp_def_self: i64,
    #[serde(default = "default_hundred")]
    pub speed_self: i64,
    #[serde(default)]
    pub priority_self: i64,
    #[serde(default)]
    pub damage_reduction_self: f64,
    #[serde(default)]
    pub abnormal_count_self: i64,
    #[serde(default)]
    pub abnormal_stacks_self: IntMap,
    #[serde(default)]
    pub positive_count_self: i64,
    #[serde(default)]
    pub first_action_self: bool,
    #[serde(default)]
    pub first_action_battle_self: bool,
    #[serde(default)]
    pub charged_self: bool,
    #[serde(default)]
    pub is_charging_self: bool,
    #[serde(default)]
    pub is_charging_opp: bool,
    #[serde(default)]
    pub times_entered_self: i64,
    #[serde(default)]
    pub times_left_self: i64,
    #[serde(default)]
    pub elements_used_count_self: i64,
    #[serde(default)]
    pub skills_energy_sum_self: i64,
    #[serde(default)]
    pub just_entered: bool,
    #[serde(default)]
    pub just_acted_self: bool,
    #[serde(default)]
    pub skill_elements_self: StrSet,
    #[serde(default)]
    pub stat_stages_self: IntMap,
    #[serde(default)]
    pub energy_cost_sum_self: IntMap,
    #[serde(default)]
    pub zero_cost_skill_count_self: i64,
    #[serde(default = "default_one_f")]
    pub power_mult_self: f64,
    #[serde(default = "default_one_f")]
    pub damage_mult_self: f64,
    #[serde(default)]
    pub energy_cost_mult_self: f64,
    #[serde(default)]
    pub combo_mult_self: f64,
    #[serde(default)]
    pub life_drain_self: f64,
    #[serde(default)]
    pub mark_bonus_own: f64,

    // ── 血脉 / 属性 ──
    #[serde(default)]
    pub bloodline_self: String,
    #[serde(default)]
    pub bloodline_opp: String,
    #[serde(default)]
    pub elements_self: Vec<String>,
    #[serde(default)]
    pub elements_opp: Vec<String>,

    // ── 敌方精灵 ──
    #[serde(default)]
    pub hp_opp: i64,
    #[serde(default = "default_one_f")]
    pub hp_opp_ratio: f64,
    #[serde(default = "default_hundred")]
    pub hp_opp_max: i64,
    #[serde(default)]
    pub energy_opp: i64,
    #[serde(default = "default_hundred")]
    pub atk_opp: i64,
    #[serde(default = "default_hundred")]
    pub def_opp: i64,
    #[serde(default = "default_hundred")]
    pub sp_atk_opp: i64,
    #[serde(default = "default_hundred")]
    pub sp_def_opp: i64,
    #[serde(default = "default_hundred")]
    pub speed_opp: i64,
    #[serde(default)]
    pub damage_reduction_opp: f64,
    #[serde(default)]
    pub abnormal_count_opp: i64,
    #[serde(default)]
    pub abnormal_stacks_opp: IntMap,
    #[serde(default)]
    pub positive_count_opp: i64,
    #[serde(default)]
    pub charged_opp: bool,
    #[serde(default)]
    pub skill_elements_opp: StrSet,
    #[serde(default)]
    pub skill_element_count_self: i64,
    #[serde(default)]
    pub skill_element_count_opp: i64,
    #[serde(default)]
    pub stat_stages_opp: IntMap,
    #[serde(default)]
    pub skills_energy_sum_opp: i64,
    #[serde(default = "default_one_f")]
    pub power_mult_opp: f64,
    #[serde(default = "default_one_f")]
    pub damage_mult_opp: f64,

    // ── 双方队伍 ──
    #[serde(default)]
    pub mark_count_own: i64,
    #[serde(default)]
    pub mark_stacks_own: IntMap,
    #[serde(default)]
    pub mark_count_opp: i64,
    #[serde(default)]
    pub mark_stacks_opp: IntMap,
    #[serde(default)]
    pub mark_count_both: i64,
    #[serde(default)]
    pub skill_count_own: IntMap,
    #[serde(default)]
    pub skill_element_counts_self: IntMap,
    #[serde(default)]
    pub skill_element_counts_opp: IntMap,
    #[serde(default)]
    pub team_counters_own: IntMap,
    #[serde(default)]
    pub team_counters_opp: IntMap,
    #[serde(default)]
    pub team_elements_own: StrSet,
    #[serde(default)]
    pub team_elements_opp: StrSet,
    #[serde(default)]
    pub devotion_own: IntMap,
    #[serde(default)]
    pub devotion_opp: IntMap,
    #[serde(default)]
    pub abnormal_stacks_battle: IntMap,
    #[serde(default)]
    pub fainted_own: i64,
    #[serde(default)]
    pub fainted_opp: i64,
    #[serde(default = "default_five")]
    pub lives_own: i64,
    #[serde(default = "default_five")]
    pub lives_opp: i64,
    #[serde(default)]
    pub burst_triggered_count_own: i64,
    #[serde(default)]
    pub moe_team_stacks: i64,

    // ── 技能（当前发动的技能） ──
    #[serde(default)]
    pub power_self: i64,
    #[serde(default)]
    pub adjacent_power_sum: i64,
    #[serde(default)]
    pub power_opp: i64,
    #[serde(default)]
    pub skill_type_self: String,
    #[serde(default)]
    pub skill_type_opp: String,
    #[serde(default)]
    pub element_self: String,
    #[serde(default)]
    pub element_opp: String,
    #[serde(default = "default_one_f")]
    pub element_advantage: f64,
    #[serde(default)]
    pub skill_tag_self: String,
    #[serde(default)]
    pub combo_self: i64,
    #[serde(default)]
    pub energy_cost_self: i64,
    #[serde(default)]
    pub energy_cost_reduction_self: i64,
    #[serde(default)]
    pub energy_cost_opp: i64,
    #[serde(default)]
    pub energy_delta_self: i64,
    #[serde(default)]
    pub heal_delta_self: i64,
    #[serde(default)]
    pub heal_delta_opp: i64,
    #[serde(default)]
    pub skill_name_self: String,
    #[serde(default)]
    pub damage_taken_this_turn: i64,
    #[serde(default)]
    pub damage_reduced_self: i64,
    #[serde(default)]
    pub prev_skill_type: String,
    #[serde(default)]
    pub prev_damage_taken_self: bool,
    #[serde(default)]
    pub prev_damage_taken_opp: bool,

    // ── 技能追踪 ──
    #[serde(default)]
    pub skill_index: i64,
    #[serde(default)]
    pub last_tick_damage_self: i64,
    #[serde(default)]
    pub last_tick_damage_opp: i64,

    // ── 战场 ──
    #[serde(default)]
    pub weather: String,
    #[serde(default)]
    pub turn: i64,
    #[serde(default)]
    pub is_first: bool,

    // ── 计次器快照 ──
    #[serde(default)]
    pub counter_values: IntMap,
}

impl Default for Ctx {
    fn default() -> Self {
        serde_json::from_str("{}").unwrap()
    }
}

fn default_one_f() -> f64 { 1.0 }
fn default_hundred() -> i64 { 100 }
fn default_five() -> i64 { 5 }

impl Ctx {
    /// 返回 self↔opp、own↔opp 镜像互换的新 Ctx（post_damage 观察者回放用）。
    /// 字段清单与 Python swapped_view 一一对应；注意：
    /// - is_charging 不在交换清单里（保持不变）；
    /// - energy_delta 只有 _self 侧字段，Python 循环里 hasattr 跳过 → 保持不变；
    /// - event 只拷贝不交换。
    pub fn swapped_view(&self) -> Ctx {
        let mut o = self.clone();

        // self ↔ opp（标量）
        std::mem::swap(&mut o.hp_self, &mut o.hp_opp);
        std::mem::swap(&mut o.hp_self_max, &mut o.hp_opp_max);
        std::mem::swap(&mut o.hp_self_ratio, &mut o.hp_opp_ratio);
        std::mem::swap(&mut o.energy_self, &mut o.energy_opp);
        std::mem::swap(&mut o.atk_self, &mut o.atk_opp);
        std::mem::swap(&mut o.def_self, &mut o.def_opp);
        std::mem::swap(&mut o.sp_atk_self, &mut o.sp_atk_opp);
        std::mem::swap(&mut o.sp_def_self, &mut o.sp_def_opp);
        std::mem::swap(&mut o.speed_self, &mut o.speed_opp);
        std::mem::swap(&mut o.damage_reduction_self, &mut o.damage_reduction_opp);
        std::mem::swap(&mut o.abnormal_count_self, &mut o.abnormal_count_opp);
        std::mem::swap(&mut o.positive_count_self, &mut o.positive_count_opp);
        std::mem::swap(&mut o.charged_self, &mut o.charged_opp);
        std::mem::swap(&mut o.skills_energy_sum_self, &mut o.skills_energy_sum_opp);
        std::mem::swap(&mut o.power_mult_self, &mut o.power_mult_opp);
        std::mem::swap(&mut o.damage_mult_self, &mut o.damage_mult_opp);
        std::mem::swap(&mut o.last_tick_damage_self, &mut o.last_tick_damage_opp);
        std::mem::swap(&mut o.prev_damage_taken_self, &mut o.prev_damage_taken_opp);
        std::mem::swap(&mut o.bloodline_self, &mut o.bloodline_opp);
        std::mem::swap(&mut o.elements_self, &mut o.elements_opp);
        std::mem::swap(&mut o.heal_delta_self, &mut o.heal_delta_opp);
        // energy_delta：Python 循环里 _opp 侧不存在被 hasattr 跳过 → 保持不变

        // dict / set 字段
        o.abnormal_stacks_self = self.abnormal_stacks_opp.clone();
        o.abnormal_stacks_opp = self.abnormal_stacks_self.clone();
        o.stat_stages_self = self.stat_stages_opp.clone();
        o.stat_stages_opp = self.stat_stages_self.clone();
        o.skill_elements_self = self.skill_elements_opp.clone();
        o.skill_elements_opp = self.skill_elements_self.clone();
        o.skill_element_counts_self = self.skill_element_counts_opp.clone();
        o.skill_element_counts_opp = self.skill_element_counts_self.clone();
        std::mem::swap(&mut o.skill_element_count_self, &mut o.skill_element_count_opp);

        // 技能字段
        std::mem::swap(&mut o.power_self, &mut o.power_opp);
        std::mem::swap(&mut o.skill_type_self, &mut o.skill_type_opp);
        std::mem::swap(&mut o.element_self, &mut o.element_opp);
        std::mem::swap(&mut o.energy_cost_self, &mut o.energy_cost_opp);

        // team own ↔ opp
        std::mem::swap(&mut o.mark_count_own, &mut o.mark_count_opp);
        o.mark_stacks_own = self.mark_stacks_opp.clone();
        o.mark_stacks_opp = self.mark_stacks_own.clone();
        o.team_counters_own = self.team_counters_opp.clone();
        o.team_counters_opp = self.team_counters_own.clone();
        o.team_elements_own = self.team_elements_opp.clone();
        o.team_elements_opp = self.team_elements_own.clone();
        o.devotion_own = self.devotion_opp.clone();
        o.devotion_opp = self.devotion_own.clone();
        std::mem::swap(&mut o.fainted_own, &mut o.fainted_opp);
        std::mem::swap(&mut o.lives_own, &mut o.lives_opp);

        o
    }
}
