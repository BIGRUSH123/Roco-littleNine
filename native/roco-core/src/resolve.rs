//! resolve — 值解析：字面量直通，或对 Ctx 的查询表达式（移植自
//! backend/vm/resolve.py + ctx.ADDRESS_MAP）。
//!
//! Rust 侧直接解释原始技能 JSON（绕过 Python 编译器），因此只处理
//! 后向兼容格式：原始 dict 查询 {"q","of",...} 与 "=@..." 公式串。
//! 类型化 IRValue（Literal/Query/RefExpr）只存在于编译产物中，不在此处理。

use crate::vm_ctx::{Ctx, StrSet};
use serde_json::Value as J;
use std::collections::BTreeMap;

/// Python `int | float | str | bool`（+ 容器）的 Rust 对应。
#[derive(Debug, Clone, PartialEq)]
pub enum Val {
    I(i64),
    F(f64),
    S(String),
    B(bool),
    Set(StrSet),
    Map(BTreeMap<String, i64>),
    L(Vec<Val>),
    Null,
}

// serde 透传：序列化为原始 JSON 标量/容器（不是 tagged 枚举）。
impl serde::Serialize for Val {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.to_json().serialize(serializer)
    }
}

impl<'de> serde::Deserialize<'de> for Val {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let j = J::deserialize(deserializer)?;
        match scalar_of(&j) {
            Some(v) => Ok(v),
            None if j.is_null() => Ok(Val::Null),
            None => Err(serde::de::Error::custom("Val: not a scalar")),
        }
    }
}

impl Default for Val {
    fn default() -> Self {
        Val::I(0)
    }
}

impl Val {
    /// Python truthiness（not raw）。
    pub fn is_falsy(&self) -> bool {
        match self {
            Val::I(i) => *i == 0,
            Val::F(f) => *f == 0.0,
            Val::S(s) => s.is_empty(),
            Val::B(b) => !*b,
            Val::Set(s) => s.is_empty(),
            Val::Map(m) => m.is_empty(),
            Val::L(l) => l.is_empty(),
            Val::Null => true,
        }
    }

    /// 数值视图（bool 在 Python 中是 int）。
    pub fn as_num(&self) -> Option<f64> {
        match self {
            Val::I(i) => Some(*i as f64),
            Val::F(f) => Some(*f),
            Val::B(b) => Some(if *b { 1.0 } else { 0.0 }),
            _ => None,
        }
    }

    pub fn to_json(&self) -> J {
        match self {
            Val::I(i) => J::from(*i),
            Val::F(f) => serde_json::Number::from_f64(*f).map(J::Number).unwrap_or(J::Null),
            Val::S(s) => J::String(s.clone()),
            Val::B(b) => J::Bool(*b),
            Val::Set(s) => J::Array(s.iter().map(|x| J::String(x.clone())).collect()),
            Val::Map(m) => {
                let mut obj = serde_json::Map::new();
                for (k, v) in m {
                    obj.insert(k.clone(), J::from(*v));
                }
                J::Object(obj)
            }
            Val::L(l) => J::Array(l.iter().map(|x| x.to_json()).collect()),
            Val::Null => J::Null,
        }
    }
}

/// Python `int(x)`：向零截断。
pub fn py_int(x: f64) -> i64 {
    x.trunc() as i64
}

/// 把 JSON 标量转成 Val（dict 交给调用方按查询处理）。
pub fn scalar_of(j: &J) -> Option<Val> {
    scalar_val(j)
}

/// 把 JSON 标量转成 Val（dict 交给调用方按查询处理）。
fn scalar_val(j: &J) -> Option<Val> {
    match j {
        J::Bool(b) => Some(Val::B(*b)),
        J::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(Val::I(i))
            } else {
                n.as_f64().map(Val::F)
            }
        }
        J::String(s) => Some(Val::S(s.clone())),
        _ => None,
    }
}

// ── ADDRESS_MAP（ctx.py 的 (of, q) → 字段）──

/// dict 型寄存器：需 'name' 子键索引。
const NAMED_DICT_QUERIES: &[&str] = &[
    "counter_value", "abnormal_stacks", "devotion", "mark_stacks", "skill_count", "team_counter",
];

/// 返回 (of, q) 对应的 Ctx 字段值；None = ADDRESS_MAP 未命中。
pub fn address_lookup<'a>(ctx: &'a Ctx, of: &str, q: &str) -> Option<Val> {
    use Val::*;
    let v = match (of, q) {
        // sprite_self
        ("sprite_self", "hp") => I(ctx.hp_self),
        ("sprite_self", "hp_ratio") => F(ctx.hp_self_ratio),
        ("sprite_self", "energy") => I(ctx.energy_self),
        ("sprite_self", "energy_cost") => I(ctx.energy_cost_self),
        ("sprite_self", "skills_energy_sum") => I(ctx.skills_energy_sum_self),
        ("sprite_self", "abnormal_count") => I(ctx.abnormal_count_self),
        ("sprite_self", "abnormal_stacks") => Map(ctx.abnormal_stacks_self.clone()),
        ("sprite_self", "times_entered") => I(ctx.times_entered_self),
        ("sprite_self", "times_left") => I(ctx.times_left_self),
        ("sprite_self", "elements_used_count") => I(ctx.elements_used_count_self),
        ("sprite_self", "positive_count") => I(ctx.positive_count_self),
        ("sprite_self", "zero_cost_skill_count") => I(ctx.zero_cost_skill_count_self),
        ("sprite_self", "priority") => I(ctx.priority_self),
        ("sprite_self", "atk") => I(ctx.atk_self),
        ("sprite_self", "def") => I(ctx.def_self),
        ("sprite_self", "sp_atk") => I(ctx.sp_atk_self),
        ("sprite_self", "sp_def") => I(ctx.sp_def_self),
        ("sprite_self", "speed") => I(ctx.speed_self),
        ("sprite_self", "hp_max") => I(ctx.hp_self_max),
        ("sprite_self", "adjacent_power_sum") => I(ctx.adjacent_power_sum),
        ("sprite_self", "damage_reduced") => I(ctx.damage_reduced_self),
        ("sprite_self", "damage_reduction") => F(ctx.damage_reduction_self),
        ("sprite_self", "last_tick_damage") => I(ctx.last_tick_damage_self),
        ("sprite_self", "charged") => B(ctx.charged_self),
        ("sprite_self", "is_charging") | ("sprite_self", "_charging") => B(ctx.is_charging_self),
        ("sprite_opp", "is_charging") | ("sprite_opp", "_charging") => B(ctx.is_charging_opp),
        ("sprite_self", "first_action") => B(ctx.first_action_self),
        ("sprite_self", "first_action_battle") => B(ctx.first_action_battle_self),
        ("sprite_self", "bloodline") => S(ctx.bloodline_self.clone()),
        ("sprite_self", "elements") => Set(ctx.elements_self.iter().cloned().collect()),
        ("sprite_self", "element_advantage") => F(ctx.element_advantage),
        ("sprite_self", "energy_cost_sum") => Map(ctx.energy_cost_sum_self.clone()),
        ("sprite_self", "power_mult") => F(ctx.power_mult_self),
        ("sprite_self", "damage_mult") => F(ctx.damage_mult_self),
        ("sprite_self", "energy_cost_mult") => F(ctx.energy_cost_mult_self),
        ("sprite_self", "combo_mult") => F(ctx.combo_mult_self),
        ("sprite_self", "life_drain") => F(ctx.life_drain_self),
        ("sprite_self", "mark_bonus") => F(ctx.mark_bonus_own),
        ("sprite_self", "energy_delta") => I(ctx.energy_delta_self),
        ("sprite_self", "heal_delta") => I(ctx.heal_delta_self),

        // sprite_opp
        ("sprite_opp", "bloodline") => S(ctx.bloodline_opp.clone()),
        ("sprite_opp", "elements") => Set(ctx.elements_opp.iter().cloned().collect()),
        ("sprite_opp", "hp") => I(ctx.hp_opp),
        ("sprite_opp", "hp_ratio") => F(ctx.hp_opp_ratio),
        ("sprite_opp", "energy") => I(ctx.energy_opp),
        ("sprite_opp", "energy_cost") => I(ctx.energy_cost_opp),
        ("sprite_opp", "abnormal_count") => I(ctx.abnormal_count_opp),
        ("sprite_opp", "abnormal_stacks") => Map(ctx.abnormal_stacks_opp.clone()),
        ("sprite_opp", "positive_count") => I(ctx.positive_count_opp),
        ("sprite_opp", "last_tick_damage") => I(ctx.last_tick_damage_opp),
        ("sprite_opp", "atk") => I(ctx.atk_opp),
        ("sprite_opp", "def") => I(ctx.def_opp),
        ("sprite_opp", "sp_atk") => I(ctx.sp_atk_opp),
        ("sprite_opp", "sp_def") => I(ctx.sp_def_opp),
        ("sprite_opp", "speed") => I(ctx.speed_opp),
        ("sprite_opp", "charged") => B(ctx.charged_opp),
        ("sprite_opp", "damage_reduction") => F(ctx.damage_reduction_opp),
        ("sprite_opp", "hp_max") => I(ctx.hp_opp_max),
        ("sprite_opp", "skills_energy_sum") => I(ctx.skills_energy_sum_opp),
        ("sprite_opp", "power_mult") => F(ctx.power_mult_opp),
        ("sprite_opp", "damage_mult") => F(ctx.damage_mult_opp),
        ("sprite_opp", "heal_delta") => I(ctx.heal_delta_opp),

        // battle / team_both
        ("battle", "abnormal_stacks") => Map(ctx.abnormal_stacks_battle.clone()),
        ("battle", "weather") => S(ctx.weather.clone()),
        ("team_both", "mark_count") => I(ctx.mark_count_both),

        // team_own
        ("team_own", "mark_count") => I(ctx.mark_count_own),
        ("team_own", "mark_stacks") => Map(ctx.mark_stacks_own.clone()),
        ("team_own", "skill_count") => Map(ctx.skill_count_own.clone()),
        ("team_own", "team_counter") => Map(ctx.team_counters_own.clone()),
        ("team_own", "devotion") => Map(ctx.devotion_own.clone()),
        ("team_own", "fainted") => I(ctx.fainted_own),
        ("team_own", "burst_triggered_count") => I(ctx.burst_triggered_count_own),
        ("team_own", "lives") => I(ctx.lives_own),
        ("team_own", "elements") => Set(ctx.team_elements_own.clone()),
        ("team_own", "moe_stacks") => I(ctx.moe_team_stacks),
        ("sprite_self", "lives") => I(ctx.lives_own),

        // team_opp
        ("team_opp", "mark_count") => I(ctx.mark_count_opp),
        ("team_opp", "mark_stacks") => Map(ctx.mark_stacks_opp.clone()),
        ("team_opp", "team_counter") => Map(ctx.team_counters_opp.clone()),
        ("team_opp", "devotion") => Map(ctx.devotion_opp.clone()),
        ("team_opp", "fainted") => I(ctx.fainted_opp),
        ("team_opp", "lives") => I(ctx.lives_opp),
        ("team_opp", "elements") => Set(ctx.team_elements_opp.clone()),
        ("sprite_opp", "lives") => I(ctx.lives_opp),

        // skill_off_0
        ("skill_off_0", "power_base") => I(ctx.power_self),
        ("skill_off_0", "element") => S(ctx.element_self.clone()),
        ("skill_off_0", "adjacent_power_sum") => I(ctx.adjacent_power_sum),
        ("skill_off_0", "combo_current") => I(ctx.combo_self),
        ("skill_off_0", "energy_cost") => I(ctx.energy_cost_self),
        ("skill_off_0", "counter_value") => Map(ctx.counter_values.clone()),
        ("skill_off_0", "energy_cost_reduction") => I(ctx.energy_cost_reduction_self),

        // skill_opp_current
        ("skill_opp_current", "power_base") => I(ctx.power_opp),
        ("skill_opp_current", "element") => S(ctx.element_opp.clone()),
        ("skill_opp_current", "energy_total") => I(ctx.energy_cost_opp),

        _ => return None,
    };
    Some(v)
}

/// per → scale → offset 变换链（_apply_transforms）。
/// Python 语义：int ⊕ int 保持 int（122*3 → int 366）；含 float → float。
fn apply_transforms(raw: Val, value: &J) -> Val {
    if matches!(raw, Val::S(_) | Val::B(_)) {
        return raw;
    }
    let mut raw = raw;
    if let Some(per) = value.get("per") {
        let per = per.as_f64().unwrap_or(0.0);
        if per != 0.0 {
            match raw {
                Val::I(_) | Val::F(_) => {
                    raw = Val::I(py_int(raw.as_num().unwrap_or(0.0) / per));
                }
                _ => {}
            }
        }
    }
    if let Some(scale) = value.get("scale") {
        match (raw.clone(), scale.as_i64()) {
            (Val::I(i), Some(si)) => raw = Val::I(i.wrapping_mul(si)),
            (v, _) => {
                if let Some(sf) = scale.as_f64() {
                    match v {
                        Val::I(i) => raw = Val::F(i as f64 * sf),
                        Val::F(f) => raw = Val::F(f * sf),
                        _ => {}
                    }
                }
            }
        }
    }
    if let Some(offset) = value.get("offset") {
        match (raw.clone(), offset.as_i64()) {
            (Val::I(i), Some(oi)) => raw = Val::I(i.wrapping_add(oi)),
            (v, _) => {
                if let Some(of) = offset.as_f64() {
                    match v {
                        Val::I(i) => raw = Val::F(i as f64 + of),
                        Val::F(f) => raw = Val::F(f + of),
                        _ => {}
                    }
                }
            }
        }
    }
    raw
}

/// resolve(ctx, value)：字面量直通 / dict 查询 / 公式串。
/// None = Python 侧抛错（KeyError 等）——调用方据此跳过观察者。
pub fn resolve(ctx: &Ctx, value: &J) -> Option<Val> {
    match value {
        J::Bool(_) | J::Number(_) => scalar_val(value),
        J::String(s) => {
            if s.starts_with('=') {
                Some(crate::formula::resolve_formula_string(ctx, &s[1..]))
            } else {
                Some(Val::S(s.clone()))
            }
        }
        J::Object(_) => resolve_dict_query(ctx, value).ok(),
        _ => None,
    }
}

/// _resolve_dict_query：{"q": ..., "of": ..., ...}。Err 对应 Python KeyError。
pub fn resolve_dict_query(ctx: &Ctx, value: &J) -> Result<Val, ()> {
    let q = value.get("q").and_then(|x| x.as_str());
    let q = q.ok_or(())?; // Query dict missing 'q' key
    let of = value.get("of").and_then(|x| x.as_str()).unwrap_or("sprite_self");

    // 派生查询
    if q == "hp_missing_ratio" {
        let ratio = if of == "sprite_self" { ctx.hp_self_ratio } else { ctx.hp_opp_ratio };
        return Ok(apply_transforms(Val::F(1.0 - ratio), value));
    }
    if q == "mark_count_both" {
        return Ok(apply_transforms(Val::I(ctx.mark_count_own + ctx.mark_count_opp), value));
    }
    if q == "is_fainted" {
        return Ok(Val::B(if of == "sprite_self" { ctx.event.self_koed } else { ctx.event.target_fainted }));
    }

    let mut raw = address_lookup(ctx, of, q).ok_or(())?; // Unknown query → KeyError

    // dict 型寄存器子索引
    if NAMED_DICT_QUERIES.contains(&q) {
        let sub_key = value.get("name").and_then(|x| x.as_str());
        raw = match raw {
            Val::Map(m) => Val::I(if let Some(k) = sub_key { m.get(k).copied().unwrap_or(0) } else { 0 }),
            _ => Val::I(0),
        };
    } else if q == "energy_cost_sum" {
        let sub_key = value.get("skill_type").and_then(|x| x.as_str())
            .or_else(|| value.get("element").and_then(|x| x.as_str()))
            .or_else(|| value.get("tag").and_then(|x| x.as_str()));
        raw = match raw {
            Val::Map(m) => Val::I(if let Some(k) = sub_key { m.get(k).copied().unwrap_or(0) } else { 0 }),
            _ => Val::I(0),
        };
    }

    // default 兜底（raw 为 falsy 时）：替换 raw 后继续走 transforms
    if let Some(default) = value.get("default") {
        if raw.is_falsy() {
            if let Some(dv) = scalar_val(default) {
                raw = dv;
            }
        }
    }

    // 字符串/布尔直接返回，不做数值变换
    if matches!(raw, Val::S(_) | Val::B(_)) {
        return Ok(raw);
    }

    Ok(apply_transforms(raw, value))
}

/// 按字段名取 Ctx/Event 字段（getattr 回退链）。
/// None = 该名字不存在于 Ctx 或 Event（Python getattr 默认 None）。
pub fn ctx_field_by_name(ctx: &Ctx, name: &str) -> Option<Val> {
    use Val::*;
    let v = match name {
        // Ctx 字段（与 vm_ctx.rs 全字段对齐）
        "hp_self" => I(ctx.hp_self),
        "hp_self_ratio" => F(ctx.hp_self_ratio),
        "hp_self_max" => I(ctx.hp_self_max),
        "energy_self" => I(ctx.energy_self),
        "atk_self" => I(ctx.atk_self),
        "def_self" => I(ctx.def_self),
        "sp_atk_self" => I(ctx.sp_atk_self),
        "sp_def_self" => I(ctx.sp_def_self),
        "speed_self" => I(ctx.speed_self),
        "priority_self" => I(ctx.priority_self),
        "damage_reduction_self" => F(ctx.damage_reduction_self),
        "abnormal_count_self" => I(ctx.abnormal_count_self),
        "positive_count_self" => I(ctx.positive_count_self),
        "first_action_self" => B(ctx.first_action_self),
        "first_action_battle_self" => B(ctx.first_action_battle_self),
        "charged_self" => B(ctx.charged_self),
        "is_charging_self" => B(ctx.is_charging_self),
        "is_charging_opp" => B(ctx.is_charging_opp),
        "times_entered_self" => I(ctx.times_entered_self),
        "times_left_self" => I(ctx.times_left_self),
        "elements_used_count_self" => I(ctx.elements_used_count_self),
        "skills_energy_sum_self" => I(ctx.skills_energy_sum_self),
        "just_entered" => B(ctx.just_entered),
        "just_acted_self" => B(ctx.just_acted_self),
        "zero_cost_skill_count_self" => I(ctx.zero_cost_skill_count_self),
        "power_mult_self" => F(ctx.power_mult_self),
        "damage_mult_self" => F(ctx.damage_mult_self),
        "energy_cost_mult_self" => F(ctx.energy_cost_mult_self),
        "combo_mult_self" => F(ctx.combo_mult_self),
        "life_drain_self" => F(ctx.life_drain_self),
        "mark_bonus_own" => F(ctx.mark_bonus_own),
        "bloodline_self" => S(ctx.bloodline_self.clone()),
        "bloodline_opp" => S(ctx.bloodline_opp.clone()),
        "hp_opp" => I(ctx.hp_opp),
        "hp_opp_ratio" => F(ctx.hp_opp_ratio),
        "hp_opp_max" => I(ctx.hp_opp_max),
        "energy_opp" => I(ctx.energy_opp),
        "atk_opp" => I(ctx.atk_opp),
        "def_opp" => I(ctx.def_opp),
        "sp_atk_opp" => I(ctx.sp_atk_opp),
        "sp_def_opp" => I(ctx.sp_def_opp),
        "speed_opp" => I(ctx.speed_opp),
        "damage_reduction_opp" => F(ctx.damage_reduction_opp),
        "abnormal_count_opp" => I(ctx.abnormal_count_opp),
        "positive_count_opp" => I(ctx.positive_count_opp),
        "charged_opp" => B(ctx.charged_opp),
        "skill_element_count_self" => I(ctx.skill_element_count_self),
        "skill_element_count_opp" => I(ctx.skill_element_count_opp),
        "skills_energy_sum_opp" => I(ctx.skills_energy_sum_opp),
        "power_mult_opp" => F(ctx.power_mult_opp),
        "damage_mult_opp" => F(ctx.damage_mult_opp),
        "mark_count_own" => I(ctx.mark_count_own),
        "mark_count_opp" => I(ctx.mark_count_opp),
        "mark_count_both" => I(ctx.mark_count_both),
        "fainted_own" => I(ctx.fainted_own),
        "fainted_opp" => I(ctx.fainted_opp),
        "lives_own" => I(ctx.lives_own),
        "lives_opp" => I(ctx.lives_opp),
        "burst_triggered_count_own" => I(ctx.burst_triggered_count_own),
        "moe_team_stacks" => I(ctx.moe_team_stacks),
        "power_self" => I(ctx.power_self),
        "adjacent_power_sum" => I(ctx.adjacent_power_sum),
        "power_opp" => I(ctx.power_opp),
        "skill_type_self" => S(ctx.skill_type_self.clone()),
        "skill_type_opp" => S(ctx.skill_type_opp.clone()),
        "element_self" => S(ctx.element_self.clone()),
        "element_opp" => S(ctx.element_opp.clone()),
        "element_advantage" => F(ctx.element_advantage),
        "skill_tag_self" => S(ctx.skill_tag_self.clone()),
        "combo_self" => I(ctx.combo_self),
        "energy_cost_self" => I(ctx.energy_cost_self),
        "energy_cost_reduction_self" => I(ctx.energy_cost_reduction_self),
        "energy_cost_opp" => I(ctx.energy_cost_opp),
        "energy_delta_self" => I(ctx.energy_delta_self),
        "heal_delta_self" => I(ctx.heal_delta_self),
        "heal_delta_opp" => I(ctx.heal_delta_opp),
        "skill_name_self" => S(ctx.skill_name_self.clone()),
        "damage_taken_this_turn" => I(ctx.damage_taken_this_turn),
        "damage_reduced_self" => I(ctx.damage_reduced_self),
        "prev_skill_type" => S(ctx.prev_skill_type.clone()),
        "prev_damage_taken_self" => B(ctx.prev_damage_taken_self),
        "prev_damage_taken_opp" => B(ctx.prev_damage_taken_opp),
        "skill_index" => I(ctx.skill_index),
        "last_tick_damage_self" => I(ctx.last_tick_damage_self),
        "last_tick_damage_opp" => I(ctx.last_tick_damage_opp),
        "weather" => S(ctx.weather.clone()),
        "turn" => I(ctx.turn),
        "is_first" => B(ctx.is_first),
        // EventContext 字段
        "counter_succeeded" => B(ctx.event.counter_succeeded),
        "was_countered" => B(ctx.event.was_countered),
        "prev_counter_succeeded" => B(ctx.event.prev_counter_succeeded),
        "target_fainted" => B(ctx.event.target_fainted),
        "self_koed" => B(ctx.event.self_koed),
        "opp_switched" => B(ctx.event.opp_switched),
        "self_switched" => B(ctx.event.self_switched),
        "sprite_left_of" => S(ctx.event.sprite_left_of.clone()),
        "turn_end" => B(ctx.event.turn_end),
        "skill_position_changed" => B(ctx.event.skill_position_changed),
        "devotion_triggered" => B(ctx.event.devotion_triggered),
        "last_tick_abnormal" => S(ctx.event.last_tick_abnormal.clone()),
        "last_tick_target" => S(ctx.event.last_tick_target.clone()),
        "abnormal_changed_name" => S(ctx.event.abnormal_changed_name.clone()),
        "abnormal_changed_target" => S(ctx.event.abnormal_changed_target.clone()),
        "abnormal_applied_name" => S(ctx.event.abnormal_applied_name.clone()),
        "abnormal_applied_target" => S(ctx.event.abnormal_applied_target.clone()),
        "skills_energy_changed_of" => S(ctx.event.skills_energy_changed_of.clone()),
        "positive_changed_of" => S(ctx.event.positive_changed_of.clone()),
        "positive_changed_stat" => S(ctx.event.positive_changed_stat.clone()),
        "positive_changed_steps" => I(ctx.event.positive_changed_steps),
        "energy_changed_of" => S(ctx.event.energy_changed_of.clone()),
        "heal_of" => S(ctx.event.heal_of.clone()),
        "damage_taken_of" => S(ctx.event.damage_taken_of.clone()),
        _ => return None,
    };
    Some(v)
}
