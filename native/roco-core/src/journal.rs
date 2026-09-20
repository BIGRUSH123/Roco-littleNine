//! journal — VM 的 31 种 Mutation（移植自 backend/vm/journal.py）。
//!
//! VM 唯一的输出形态：一段不可变的 Mutation 序列，由引擎回放到
//! 可变战斗状态上。字段与 Python dataclass 一一对应；
//! 嵌套 IR 载荷（then/effects/cond/skill_where）暂用 serde_json::Value
//! 承载（executor 移植时决定是否升级为强类型）。

use crate::resolve::Val;
use serde::{Deserialize, Serialize};

/// 通用目标引用："sprite_self" | "sprite_opp" | "team_own" | "team_opp" | ...
pub type TargetRef = String;

/// 嵌套 IR 载荷占位（效果树 / 条件树 / 技能过滤条件）。
/// 语义由 executor 按编译器默认值规则解释。
pub type IrPayload = serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Mutation {
    /// 永久属性等级变化（StatChange）
    StatChange {
        target: TargetRef,
        stat: String,
        steps: i64,
        #[serde(default = "default_battlefield")]
        scope: String,
        #[serde(default)]
        source: Option<String>,
        #[serde(default)]
        element: Option<String>,
        #[serde(default)]
        per_element: Option<i64>,
        #[serde(default)]
        on_next: bool,
        #[serde(default)]
        if_type: Option<String>,
        #[serde(default)]
        skill_filter: Option<String>,
        #[serde(default)]
        skill_where: Option<IrPayload>,
    },
    /// 技能管线内部修饰符（ModifierInjection）。
    /// value 保留 Python 动态类型（flag_set 传 True/int 原样）。
    ModifierInjection {
        target: TargetRef,
        stat: String,
        #[serde(default)]
        value: Val,
        #[serde(default = "default_battlefield")]
        scope: String,
        #[serde(default = "default_set")]
        mode: String,
        #[serde(default)]
        name: Option<String>,
        #[serde(default)]
        source: Option<String>,
        #[serde(default)]
        element: Option<String>,
        #[serde(default)]
        per_element: Option<i64>,
        #[serde(default)]
        on_next: bool,
        #[serde(default)]
        if_type: Option<String>,
        #[serde(default)]
        skill_filter: Option<String>,
        #[serde(default)]
        skill_where: Option<IrPayload>,
        #[serde(default)]
        ttl: i64,
        #[serde(default)]
        then: Option<Vec<IrPayload>>,
    },
    /// 最终伤害（Damage）
    Damage {
        target: TargetRef,
        amount: i64,
        element: String,
        #[serde(rename = "type")]
        attack_type: String,
    },
    /// HP 恢复（Heal）
    Heal { target: TargetRef, amount: i64 },
    /// 能量增减（EnergyChange）
    EnergyChange { target: TargetRef, delta: i64 },
    /// 印记层数变化（MarkChange）
    MarkChange {
        target_team: String,
        name: String,
        delta: i64,
        #[serde(default = "default_apply")]
        action: String,
        #[serde(default = "default_one_f")]
        ratio: f64,
        #[serde(default)]
        source_abnormal: Option<String>,
    },
    /// 异常状态层数变化（AbnormalChange）
    AbnormalChange {
        target: TargetRef,
        name: String,
        delta: i64,
        #[serde(default = "default_battlefield")]
        scope: String,
    },
    /// 天气变更（WeatherSet）
    WeatherSet { weather: String, turns: i64 },
    /// 驱散（Dispel）
    Dispel {
        target: TargetRef,
        what: String,
        #[serde(default)]
        name: Option<String>,
        #[serde(default)]
        limit: Option<i64>,
        #[serde(default)]
        type_limit: Option<i64>,
        #[serde(default)]
        source: Option<String>,
    },
    /// 偷取（Steal）
    Steal {
        from_target: TargetRef,
        what: String,
        #[serde(default)]
        name: Option<String>,
        #[serde(default)]
        amount: Option<i64>,
        #[serde(default = "default_steal")]
        action: String,
    },
    /// 异常 tick 伤害（Tick）
    Tick { target: TargetRef, abnormal_name: String },
    /// 印记/效果翻倍（Double）
    Double {
        target: TargetRef,
        what: String,
        #[serde(default)]
        name: Option<String>,
    },
    /// 全体正/负效果加层（EffectDelta）
    EffectDelta {
        target: TargetRef,
        what: String,
        delta: i64,
    },
    /// 进入蓄力（Charge）
    Charge { target: TargetRef },
    /// 脱离/退场（Escape）
    Escape {
        target: TargetRef,
        #[serde(default)]
        inherit: bool,
        #[serde(default)]
        urgent: bool,
        #[serde(default)]
        then: Option<Vec<IrPayload>>,
    },
    /// 返场（Return）
    Return { target: TargetRef },
    /// 锁定对手换宠（Lock）
    Lock { target: TargetRef, turns: i64 },
    /// 打断目标行动（Interrupt）
    Interrupt { target: TargetRef },
    /// 交换（Exchange）
    Exchange { target: TargetRef, what: String },
    /// 重置属性（Reset）
    Reset { target: TargetRef, stat: String },
    /// 重定向下一次行动（Redirect）
    Redirect { target: TargetRef },
    /// 爆发效果授予（BurstGrant）
    BurstGrant {
        target: TargetRef,
        #[serde(default)]
        skill_where: Option<IrPayload>,
        #[serde(default)]
        skill_filter: Option<String>,
        #[serde(default)]
        effects: Vec<IrPayload>,
        #[serde(default)]
        source: String,
    },
    /// 重放已用技能（Replay）
    Replay {
        #[serde(rename = "from")]
        from_: TargetRef,
        #[serde(default)]
        skill_filter: Option<IrPayload>,
    },
    /// 借用对手当前技能（Borrow）
    Borrow { from_skill: String },
    /// 队伍计数器（TeamCounterDelta）
    TeamCounterDelta { target: String, key: String, delta: i64 },
    /// 玩家魔力/生命（LivesDelta）
    LivesDelta { target_team: String, delta: i64 },
    /// 延迟效果登记（ScheduleEntry）
    ScheduleEntry {
        turns: i64,
        #[serde(default = "default_turn_start")]
        at: String,
        #[serde(default)]
        then: Vec<IrPayload>,
    },
    /// 换宠时效果转移（InheritEffectsMutation）
    InheritEffects {
        source_key: String,
        target_key: String,
        #[serde(default = "default_battlefield")]
        scope: String,
        #[serde(default)]
        via_pending: bool,
        #[serde(default)]
        effects: Vec<IrPayload>,
        #[serde(default)]
        inherit_stat_effects: bool,
    },
    /// 变身（TransformMutation）
    Transform {
        species: String,
        #[serde(default)]
        skills: Option<Vec<String>>,
        #[serde(default)]
        reset_hp: bool,
        #[serde(default)]
        reset_energy: bool,
    },
    /// 特性压制/移除/复制（TraitInteractionMutation）
    TraitInteraction {
        action: String,
        target: String,
        #[serde(default)]
        copy_from: Option<String>,
        #[serde(default)]
        new_ability: Option<String>,
    },
    /// 授予临时技能（GainSkillsMutation）
    GainSkills {
        #[serde(default = "default_one_i")]
        count: i64,
        #[serde(default = "default_true")]
        exclude_carried: bool,
        #[serde(default = "default_learnset")]
        source: String,
        #[serde(default = "default_sprite_self")]
        target: TargetRef,
    },
    /// 注册持久计数器（CounterRegister）
    CounterRegister {
        #[serde(default)]
        name: Option<String>,
        #[serde(default)]
        cond: Option<IrPayload>,
        #[serde(default)]
        then: Vec<IrPayload>,
        #[serde(default = "default_persistent")]
        scope: String,
        #[serde(default)]
        listen: Option<Vec<String>>,
        #[serde(default = "default_one_i")]
        threshold: i64,
        #[serde(default = "default_true")]
        reset_on_fire: bool,
    },
}

pub type Journal = Vec<Mutation>;

// ── 默认值（与 Python dataclass 对齐）──
fn default_battlefield() -> String { "battlefield".into() }
fn default_set() -> String { "set".into() }
fn default_apply() -> String { "apply".into() }
fn default_one_f() -> f64 { 1.0 }
fn default_one_i() -> i64 { 1 }
fn default_steal() -> String { "steal".into() }
fn default_turn_start() -> String { "turn_start".into() }
fn default_true() -> bool { true }
fn default_learnset() -> String { "learnset".into() }
fn default_sprite_self() -> String { "sprite_self".into() }
fn default_persistent() -> String { "persistent".into() }
