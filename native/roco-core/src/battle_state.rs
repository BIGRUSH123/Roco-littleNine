//! battle_state — 可变战斗状态层（移植自 sim/sprite.py + globals.py +
//! player.py + battleskill.py 的状态字段；效果对象层级来自 vm/effect.py）。
//!
//! 设计：
//! - 效果统一为 `Effect { identity, kind }`，kind 对应 EffectObject 子类；
//! - Python 的效果/属性缓存（dirty 重建）不移植——按需重算，行为等价；
//! - save/restore = `BattleState::clone()`（廉价结构体克隆，MCTS 回滚）；
//! - 技能引用用 `skill_id`（构造期唯一编号），镜像 Python 对象引用语义
//!   （换位后引用跟随技能对象）。

use crate::journal::IrPayload;
use crate::resolve::Val;
use serde::{Deserialize, Serialize};
use serde_json::Value as J;
use std::collections::{BTreeMap, BTreeSet};

pub type IMap = BTreeMap<String, i64>;
pub type FMap = BTreeMap<String, f64>;

// ── 物种（静态数据，来自 BattleSpec）──

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Species {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub number: String,
    #[serde(default)]
    pub attributes: String, // 逗号分隔元素，如 "火,翼"
    #[serde(default)]
    pub bloodline: String,
    #[serde(default)]
    pub ability: String,
    #[serde(default)]
    pub ability_id: i64,
    #[serde(default)]
    pub pre_species: String,
    #[serde(default)]
    pub bloodline_skills: IMap,
    // 六维（base；effective_stat 用 initial_stats 而非这里）
    #[serde(default)]
    pub base: BTreeMap<String, i64>,
}

impl Species {
    pub fn elements(&self) -> Vec<String> {
        self.attributes
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect()
    }
}

// ── 技能 ──

pub fn next_skill_id() -> u64 {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    COUNTER.fetch_add(1, Ordering::Relaxed)
}

/// 战斗中技能槽 = 静态 SkillJson + 可变状态。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BattleSkill {
    #[serde(default)]
    pub skill_id: u64,
    pub base: crate::data::skills::SkillJson,
    #[serde(default)]
    pub modifiers: FMap,
    #[serde(default)]
    pub replaced_by: Option<crate::data::skills::SkillJson>,
    #[serde(default)]
    pub cooldown: i64,
    #[serde(default = "one_f")]
    pub next_attack_mult: f64,
    #[serde(default)]
    pub nullified: bool,
    #[serde(default)]
    pub sealed: bool,
    #[serde(default)]
    pub is_temporary: bool,
    #[serde(default)]
    pub transmission: i64,
    #[serde(default)]
    pub element_override: String,
    #[serde(default)]
    pub mech_energy_reduction: i64,
    #[serde(default)]
    pub burst_effects: Vec<IrPayload>,
}

fn one_f() -> f64 { 1.0 }

impl BattleSkill {
    pub fn from_base(base: crate::data::skills::SkillJson) -> Self {
        let transmission = base.transmission;
        BattleSkill {
            skill_id: next_skill_id(),
            base,
            modifiers: BTreeMap::new(),
            replaced_by: None,
            cooldown: 0,
            next_attack_mult: 1.0,
            nullified: false,
            sealed: false,
            is_temporary: false,
            transmission,
            element_override: String::new(),
            mech_energy_reduction: 0,
            burst_effects: vec![],
        }
    }

    fn effective_base(&self) -> &crate::data::skills::SkillJson {
        self.replaced_by.as_ref().unwrap_or(&self.base)
    }

    pub fn name(&self) -> String {
        if self.nullified {
            return "(打断)".into();
        }
        self.effective_base().name.clone()
    }

    pub fn element(&self) -> String {
        if !self.element_override.is_empty() {
            return self.element_override.clone();
        }
        if self.nullified {
            return String::new();
        }
        self.effective_base().element.clone()
    }

    pub fn skill_type(&self) -> String {
        if self.nullified {
            return "物攻".into();
        }
        self.effective_base().skill_type.clone()
    }

    pub fn counter(&self) -> String {
        if self.nullified {
            return "无".into();
        }
        self.effective_base().counter.clone()
    }

    pub fn priority(&self) -> i64 {
        if self.nullified {
            return 0;
        }
        self.effective_base().priority
    }

    pub fn combo(&self) -> i64 {
        let base_combo = if self.nullified { -1 } else { self.effective_base().combo };
        base_combo + self.modifiers.get("combo").copied().unwrap_or(0.0) as i64
    }

    pub fn power(&self) -> i64 {
        let base_power = if self.nullified { 0 } else { self.effective_base().power };
        base_power + self.modifiers.get("power").copied().unwrap_or(0.0) as i64
    }

    pub fn energy_cost(&self) -> i64 {
        let base_cost = if self.nullified { 0 } else { self.effective_base().energy_cost };
        base_cost
            + self.modifiers.get("energy_cost").copied().unwrap_or(0.0) as i64
            + self.mech_energy_reduction
    }

    pub fn is_attack(&self) -> bool {
        if self.nullified {
            return true;
        }
        matches!(self.effective_base().skill_type.as_str(), "物攻" | "魔攻" | "动态攻击")
    }

    pub fn is_defense(&self) -> bool {
        if self.nullified {
            return false;
        }
        self.effective_base().skill_type == "防御"
    }

    pub fn is_status(&self) -> bool {
        if self.nullified {
            return false;
        }
        self.effective_base().skill_type == "状态"
    }

    pub fn load_permanent_mods(&mut self, sprite_modifiers: &FMap) {
        if self.base.name.is_empty() {
            return;
        }
        let prefix = format!("skill.{}.", self.base.name);
        for (key, value) in sprite_modifiers {
            if let Some(stat) = key.strip_prefix(&prefix) {
                self.modifiers.insert(stat.to_string(), *value);
            }
        }
    }
}

// ── 效果对象（vm/effect.py 层级）──

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Effect {
    pub name: String,
    #[serde(default)]
    pub source: String,
    #[serde(default = "default_scope")]
    pub scope: String,
    #[serde(default)]
    pub ttl: i64,
    #[serde(default)]
    pub cooldown: i64,
    #[serde(rename = "kind")]
    pub kind: EffectKind,
}

fn default_scope() -> String { "battlefield".into() }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "effect_type", rename_all = "snake_case")]
pub enum EffectKind {
    Observer {
        #[serde(default)]
        cond: IrPayload,
        #[serde(default)]
        then: Vec<IrPayload>,
        #[serde(default)]
        listen: Vec<String>,
        #[serde(default = "one_i")]
        threshold: i64,
        #[serde(default = "default_true")]
        reset_on_fire: bool,
    },
    Modifier {
        #[serde(default = "default_sprite_self")]
        target: String,
        #[serde(default)]
        attr: String,
        #[serde(default)]
        value: f64,
        #[serde(default = "default_add")]
        mode: String,
        #[serde(default)]
        skill_where: Option<IrPayload>,
    },
    Abnormal {
        #[serde(default)]
        stacks: i64,
        #[serde(default)]
        tick_damage_pct: f64,
        #[serde(default)]
        tick_element: String,
        #[serde(default)]
        decay_on_tick: bool,
        #[serde(default)]
        max_stacks: i64,
        #[serde(default = "default_true")]
        tick_per_stack: bool,
    },
    StatBuff {
        #[serde(default)]
        stat_key: String,
        #[serde(default)]
        steps: i64,
        #[serde(default)]
        display_mult: Option<f64>,
        #[serde(default)]
        display_value: Option<f64>,
        #[serde(default)]
        is_inherent: bool,
    },
    State {
        #[serde(default)]
        state_type: String,
        #[serde(default)]
        params: IrPayload,
    },
    /// MarkEffect 的精灵级副本（印记本体在 GlobalState）
    Mark,
}

fn one_i() -> i64 { 1 }
fn default_true() -> bool { true }
fn default_add() -> String { "add".into() }
fn default_sprite_self() -> String { "sprite_self".into() }

impl Effect {
    pub fn is_stat_buff(&self) -> bool { matches!(self.kind, EffectKind::StatBuff { .. }) }
    pub fn is_abnormal(&self) -> bool { matches!(self.kind, EffectKind::Abnormal { .. }) }
    pub fn is_state(&self) -> bool { matches!(self.kind, EffectKind::State { .. }) }
    pub fn is_modifier(&self) -> bool { matches!(self.kind, EffectKind::Modifier { .. }) }

    pub fn steps(&self) -> i64 {
        match &self.kind {
            EffectKind::StatBuff { steps, .. } => *steps,
            _ => 0,
        }
    }

    pub fn set_steps(&mut self, steps: i64) {
        if let EffectKind::StatBuff { steps: s, .. } = &mut self.kind {
            *s = steps;
        }
    }

    pub fn stacks(&self) -> i64 {
        match &self.kind {
            EffectKind::Abnormal { stacks, .. } => *stacks,
            _ => 0,
        }
    }

    /// EffectObject.should_clear。
    pub fn should_clear(&self, reason: &str) -> bool {
        if reason == "reload" {
            return true;
        }
        match self.scope.as_str() {
            "turn" => reason == "turn_end",
            "battlefield" => reason == "leave" || reason == "faint",
            "persistent" => reason == "faint",
            _ => false,
        }
    }
}

// ── 精灵 ──

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PendingModifier {
    #[serde(default)]
    pub stat: String,
    #[serde(default)]
    pub value: f64,
    #[serde(default = "default_set_pm")]
    pub mode: String,
    #[serde(default)]
    pub if_type: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
}

fn default_set_pm() -> String { "set".into() }

/// 进化之力的首领形态数据（由 Python 导出器预计算）。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LeaderForm {
    #[serde(flatten)]
    pub species: Species,
    #[serde(default)]
    pub initial_stats: IMap,
    #[serde(default)]
    pub max_hp: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Sprite {
    pub species: Species,
    #[serde(default)]
    pub bloodline: String,
    #[serde(default)]
    pub bloodline_skills: IMap,
    pub skills: Vec<BattleSkill>,
    #[serde(default)]
    pub initial_stats: IMap,
    #[serde(default)]
    pub nature: Option<String>,
    #[serde(default)]
    pub iv: IMap,
    #[serde(default)]
    pub current_hp: i64,
    #[serde(default)]
    pub max_hp: i64,
    #[serde(default = "ten_i")]
    pub energy: i64,
    #[serde(default)]
    pub active_effects: Vec<Effect>,
    #[serde(default)]
    pub entry_turn: i64,
    #[serde(default)]
    pub counters: IMap,
    #[serde(default = "default_true2")]
    pub first_action: bool,
    #[serde(default = "default_true2")]
    pub first_action_battle: bool,
    #[serde(default)]
    pub pending_return: bool,
    #[serde(default)]
    pub interrupted: bool,
    #[serde(default)]
    pub locked_turns: i64,
    #[serde(default)]
    pub extra_skill_use: bool,
    #[serde(default)]
    pub charging: bool,
    #[serde(default)]
    pub charged_skill_index: i64,
    /// 引用跟随技能对象（用 skill_id 而非位置）
    #[serde(default)]
    pub charged_skill_ref: Option<u64>,
    #[serde(default)]
    pub modifiers: FMap,
    #[serde(default)]
    pub mod_scopes: BTreeMap<String, String>,
    #[serde(default)]
    pub last_abnormal_dmg: IMap,
    #[serde(default)]
    pub pending_effects: Vec<(Effect, i64)>,
    #[serde(default)]
    pub pending_modifiers: Vec<PendingModifier>,
    #[serde(default)]
    pub trait_suppressed: bool,
    /// 属性缓存（py Sprite._stat_cache_dirty / _cached_*）：None=脏。
    /// 仅 invalidate_stat_cache 时清空——transform 后保持旧值（py 既有行为）
    #[serde(skip)]
    pub stat_cache: std::cell::RefCell<Option<[i64; 4]>>,
    #[serde(default)]
    pub moe_chain: Vec<Species>,
    #[serde(default)]
    pub moe_position: i64,
    #[serde(default)]
    pub moe_origin: Option<Species>,
    #[serde(default)]
    pub moe_origin_skills: Vec<BattleSkill>,
    /// 进化之力目标形态（Python 导出器预计算；None = 无可用进化）
    #[serde(default)]
    pub leader_form: Option<LeaderForm>,
    /// 愿力血脉技能（Python 导出器按血脉预查；None = 无）
    #[serde(default)]
    pub bloodline_skill: Option<crate::data::skills::SkillJson>,
    /// 特性直改效果（原始 dict，供每回合重放）
    #[serde(default)]
    pub trait_direct_effects: Vec<J>,
    /// 直改修正追踪：技能名 → {attr: 累计 delta}（_remove_direct_mods 用）
    #[serde(default)]
    pub direct_mod_tracked: BTreeMap<String, BTreeMap<String, f64>>,
}

fn ten_i() -> i64 { 10 }
fn default_true2() -> bool { true }

// 步数换算常量（sprite.py）
const SPEED_STEP: i64 = 10;
const STEP_PCT: f64 = 10.0;

/// 非百分比型 stat_key
const NON_PCT_KEYS: &[&str] = &["power", "priority", "energy_cost", "combo", "life_drain", "combo_mult"];

impl Sprite {
    pub fn is_fainted(&self) -> bool { self.current_hp <= 0 }
    pub fn name(&self) -> String { self.species.name.clone() }

    /// stat_key 的总步数（StatBuffEffect 求和）。
    pub fn sum_steps(&self, stat_key: &str) -> i64 {
        self.active_effects
            .iter()
            .filter(|e| e.is_stat_buff())
            .filter_map(|e| match &e.kind {
                EffectKind::StatBuff { stat_key: k, steps, .. } if k == stat_key => Some(*steps),
                _ => None,
            })
            .sum()
    }

    /// 六维/非六维属性经效果修正后的有效值。
    pub fn effective_stat(&self, stat_key: &str) -> i64 {
        let total_steps = self.sum_steps(stat_key);
        if NON_PCT_KEYS.contains(&stat_key) {
            return total_steps;
        }
        let base = self.initial_stats.get(stat_key).copied().unwrap_or(0);
        if stat_key == "speed" {
            (base + total_steps * SPEED_STEP).max(0)
        } else {
            crate::damage::py_round(base as f64 * (1.0 + total_steps as f64 / STEP_PCT)).max(0)
        }
    }

    pub fn take_damage(&mut self, amount: i64) -> i64 {
        let actual = self.current_hp.min(amount);
        self.current_hp -= actual;
        if self.current_hp > 0 {
            self.check_freeze_death();
        }
        actual
    }

    pub fn heal(&mut self, amount: i64) -> i64 {
        let actual = (self.max_hp - self.current_hp).min(amount);
        self.current_hp += actual;
        actual
    }

    pub fn max_energy(&self) -> i64 {
        for e in &self.active_effects {
            if let EffectKind::Modifier { attr, value, .. } = &e.kind {
                if attr == "max_energy" {
                    return *value as i64;
                }
            }
        }
        10
    }

    pub fn gain_energy(&mut self, amount: i64) -> i64 {
        let room = (self.max_energy() - self.energy).max(0);
        let actual = room.min(amount);
        self.energy += actual;
        actual
    }

    pub fn lose_energy(&mut self, amount: i64) -> i64 {
        let actual = self.energy.min(amount);
        self.energy -= actual;
        actual
    }

    /// py Sprite.transform：替换 species + skills，保留 HP 比例/能量/效果/计数器。
    pub fn transform(&mut self, new_species: Species, new_skills: Vec<BattleSkill>) {
        let hp_ratio = self.current_hp as f64 / self.max_hp.max(1) as f64;
        self.species = new_species.clone();
        let final_stats = crate::species_db::compute_final_stats(
            &new_species.base,
            self.nature.as_deref(),
            &self.iv,
        );
        self.initial_stats = final_stats.clone();
        // 注意：py transform 不失效 _stat_cache（旧缓存会一直用到下次显式失效），
        // 此处必须保持一致——spec_0141 的差异在 rust 提前填充了缓存，见
        // rule_agent::estimate_damage 的绕行。
        self.max_hp = final_stats.get("hp").copied().unwrap_or(self.max_hp);
        self.current_hp =
            crate::damage::py_round(self.max_hp as f64 * hp_ratio).max(1);
        if !new_skills.is_empty() {
            self.skills = new_skills;
        }
        self.first_action = true;
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            eprintln!(
                "[rust transform] -> {} no={} nature={:?} iv={:?} stats={:?}",
                self.name(), new_species.number, self.nature, self.iv, self.initial_stats
            );
        }
    }

    /// py Sprite._build_moe_chain：沿 pre_species 向下走到最低形态。
    pub fn build_moe_chain(&mut self) {
        let mut chain = vec![self.species.clone()];
        let mut current = self.species.clone();
        while !current.pre_species.is_empty() {
            let Some(pre) = crate::species_db::lookup_by_number(&current.pre_species, "") else {
                break;
            };
            let sp = pre.to_species();
            chain.push(sp.clone());
            current = sp;
        }
        self.moe_chain = chain;
    }

    fn reset_moe_state(&mut self) {
        self.moe_chain.clear();
        self.moe_position = 0;
        self.moe_origin = None;
        self.moe_origin_skills.clear();
    }

    /// py Sprite._sync_moe_status_effect。
    fn sync_moe_status_effect(&mut self) {
        self.remove_effect("萌化", "abnormal");
        if self.moe_position > 0 {
            if let Some(mut tpl) = crate::statics::abnormal_template("萌化") {
                if let EffectKind::Abnormal { stacks, .. } = &mut tpl.kind {
                    *stacks = self.moe_position;
                }
                self.active_effects.push(tpl);
            }
        }
    }

    /// py Sprite._seal_exclusive_skills。
    fn seal_exclusive_skills(&mut self) -> Vec<String> {
        let species_name = self.species.name.clone();
        let mut events = Vec::new();
        for bs in self.skills.iter_mut() {
            let ex = bs.base.exclusive_to.clone();
            if !ex.is_empty() && ex != species_name && !bs.sealed {
                bs.sealed = true;
                events.push(format!("{} 专属技能锁定(需{})", bs.name(), ex));
            }
        }
        events
    }

    /// py Sprite._unseal_exclusive_skills。
    fn unseal_exclusive_skills(&mut self) -> Vec<String> {
        let mut events = Vec::new();
        for bs in self.skills.iter_mut() {
            if !bs.base.exclusive_to.is_empty() && bs.sealed {
                bs.sealed = false;
                events.push(format!("{} 专属技能解锁", bs.name()));
            }
        }
        events
    }

    /// py Sprite.apply_moe：施加萌化，沿进化链向下退化。
    pub fn apply_moe(&mut self, stacks: i64) -> Vec<String> {
        if stacks <= 0 {
            return vec![];
        }
        if !self.moe_chain.is_empty() && (self.moe_position as usize) < self.moe_chain.len() {
            let expected = self.moe_chain[self.moe_position as usize].number.clone();
            if self.species.number != expected {
                self.reset_moe_state();
            }
        }
        if self.moe_chain.is_empty() {
            self.moe_origin = Some(self.species.clone());
            self.moe_origin_skills = self.skills.clone();
            self.build_moe_chain();
            self.moe_position = 0;
        }
        let max_pos = self.moe_chain.len() as i64 - 1;
        let new_pos = self.moe_position + stacks;
        let no_worry = !self.trait_suppressed && self.species.ability_id == 20157;
        if self.moe_position >= max_pos {
            if no_worry {
                self.moe_position = new_pos;
                self.sync_moe_status_effect();
                return vec![format!(
                    "{} 萌化层数+{stacks}(共{}层，无忧无虑)",
                    self.name(), self.moe_position
                )];
            }
            return vec![format!("{} 已是最低形态，免疫萌化", self.name())];
        }
        let actual_new = new_pos.min(max_pos);
        let target = self.moe_chain[actual_new as usize].clone();
        let old_name = self.name();
        self.transform(target, vec![]);
        self.moe_position = actual_new;
        self.sync_moe_status_effect();
        self.remove_effect("首领化", "");
        let mut events = vec![format!(
            "{old_name} 萌化 → 变为{}({}层)",
            self.name(), self.moe_position
        )];
        events.extend(self.seal_exclusive_skills());
        if new_pos > max_pos && no_worry {
            self.moe_position = new_pos;
            self.sync_moe_status_effect();
            events.push(format!(
                "{} 萌化层数+{}(共{}层，无忧无虑)",
                self.name(), new_pos - max_pos, self.moe_position
            ));
        }
        events
    }

    /// py Sprite.remove_moe：沿进化链向上恢复。
    pub fn remove_moe(&mut self, stacks: i64) -> i64 {
        if stacks <= 0 || self.moe_position <= 0 {
            return 0;
        }
        let removed = stacks.min(self.moe_position);
        let new_pos = self.moe_position - removed;
        let (target_species, target_skills) = if new_pos == 0 {
            let sp = self.moe_origin.clone().unwrap_or_else(|| self.species.clone());
            let sk = self.moe_origin_skills.clone();
            self.moe_chain.clear();
            self.moe_origin = None;
            self.moe_origin_skills.clear();
            (sp, sk)
        } else {
            (self.moe_chain[new_pos as usize].clone(), vec![])
        };
        self.transform(target_species, target_skills);
        self.moe_position = new_pos;
        self.sync_moe_status_effect();
        self.unseal_exclusive_skills();
        removed
    }

    pub fn frozen_hp(&self) -> i64 {        let stacks = self.get_stacks("冻结");
        if stacks > 0 {
            crate::damage::py_round(self.max_hp as f64 * 0.05 * stacks as f64)
        } else {
            0
        }
    }

    pub fn check_freeze_death(&mut self) -> bool {
        if self.is_fainted() {
            return false;
        }
        let fhp = self.frozen_hp();
        if fhp > 0 && self.current_hp <= fhp {
            self.current_hp = 0;
            return true;
        }
        false
    }

    pub fn get_stacks(&self, name: &str) -> i64 {
        self.active_effects
            .iter()
            .filter(|e| e.is_abnormal() && e.name == name)
            .map(|e| e.stacks())
            .sum()
    }

    pub fn find_abnormal(&mut self, name: &str) -> Option<&mut Effect> {
        self.active_effects
            .iter_mut()
            .find(|e| e.is_abnormal() && e.name == name)
    }

    /// sprite.add_effect：StatBuff 按 (stat_key, scope) 合并；Abnormal 按名合并。
    pub fn add_effect(&mut self, mut effect: Effect) {
        // StatBuff：同名同 scope 合并步数
        if effect.is_stat_buff() {
            let (sk, sc, st) = match &effect.kind {
                EffectKind::StatBuff { stat_key, steps, .. } => {
                    (stat_key.clone(), effect.scope.clone(), *steps)
                }
                _ => unreachable!(),
            };
            for existing in self.active_effects.iter_mut() {
                if existing.is_stat_buff() && existing.scope == sc {
                    if let EffectKind::StatBuff { stat_key: ek, steps: es, .. } = &mut existing.kind
                    {
                        if *ek == sk {
                            *es += st;
                            return;
                        }
                    }
                }
            }
            self.active_effects.push(effect);
            return;
        }
        // Abnormal：同名合并层数
        if effect.is_abnormal() {
            let aname = effect.name.clone();
            for existing in self.active_effects.iter_mut() {
                if existing.is_abnormal() && existing.name == aname {
                    if let EffectKind::Abnormal { stacks, .. } = &mut existing.kind {
                        *stacks += effect.stacks();
                    }
                    self.check_freeze_death();
                    return;
                }
            }
            let is_freeze = aname == "冻结";
            self.active_effects.push(effect);
            if is_freeze {
                self.check_freeze_death();
            }
            return;
        }
        // State / Modifier / Observer / 其他：直接追加
        self.active_effects.push(effect);
    }

    pub fn remove_effect(&mut self, name: &str, category: &str) {
        self.active_effects
            .retain(|e| !(e.name == name && (category.is_empty() || effect_is_category(e, category))));
    }

    pub fn update_stacks(&mut self, name: &str, stacks: i64) {
        if let Some(ae) = self.find_abnormal(name) {
            if stacks > 0 {
                if let EffectKind::Abnormal { stacks: s, .. } = &mut ae.kind {
                    *s = stacks;
                }
            } else {
                self.active_effects.retain(|e| !(e.is_abnormal() && e.name == name));
            }
        } else if stacks > 0 {
            // 从模板创建
            if let Some(tpl) = crate::statics::abnormal_template(name) {
                let mut eff = tpl;
                if let EffectKind::Abnormal { stacks: s, .. } = &mut eff.kind {
                    *s = stacks;
                }
                self.active_effects.push(eff);
            }
        }
    }

    /// 清除指定 scope 的全部效果 + 同步 _modifiers。
    pub fn clear_effects(&mut self, scope: &str) {
        let scopes: Vec<&str> = if scope == "battlefield" || scope == "turn" {
            vec![scope, "aura"]
        } else {
            vec![scope]
        };
        let len_before = self.active_effects.len();
        let mut changed = false;
        self.active_effects
            .retain(|e| !scopes.contains(&e.scope.as_str()));
        if len_before != self.active_effects.len() {
            changed = true;
        }
        let mod_scopes = self.mod_scopes.clone();
        for (mod_key, mod_scope) in &mod_scopes {
            if *mod_scope == scope || ((scope == "battlefield" || scope == "turn") && mod_scope == "aura")
            {
                self.modifiers.remove(mod_key);
                self.mod_scopes.remove(mod_key);
                changed = true;
            }
        }
        // py clear_effects：changed 时 _invalidate_stat_cache
        if changed {
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                eprintln!(
                    "[rust clear] {} scope={} changed=true mod_scopes_before={:?}",
                    self.name(),
                    scope, mod_scopes
                );
            }
            self.invalidate_stat_cache();
        }
    }

    pub fn dispel_positive(&mut self, count: i64) -> usize {
        let targets: Vec<usize> = self
            .active_effects
            .iter()
            .enumerate()
            .filter(|(_, e)| {
                e.is_stat_buff()
                    && e.steps() > 0
                    && e.scope != "permanent"
                    && e.scope != "aura"
            })
            .map(|(i, _)| i)
            .take(if count >= 0 { count as usize } else { usize::MAX })
            .collect();
        let n = targets.len();
        for i in targets.into_iter().rev() {
            self.active_effects.remove(i);
        }
        n
    }

    pub fn dispel_negative(&mut self, count: i64) -> usize {
        let targets: Vec<usize> = self
            .active_effects
            .iter()
            .enumerate()
            .filter(|(_, e)| {
                e.is_stat_buff()
                    && e.steps() < 0
                    && e.scope != "permanent"
                    && e.scope != "aura"
            })
            .map(|(i, _)| i)
            .take(if count >= 0 { count as usize } else { usize::MAX })
            .collect();
        let n = targets.len();
        for i in targets.into_iter().rev() {
            self.active_effects.remove(i);
        }
        n
    }

    pub fn double_positive(&mut self) -> usize {
        let mut n = 0;
        for e in self.active_effects.iter_mut() {
            if let EffectKind::StatBuff { steps, .. } = &mut e.kind {
                if *steps > 0 {
                    *steps *= 2;
                    n += 1;
                }
            }
        }
        n
    }

    pub fn double_negative(&mut self) -> usize {
        let mut n = 0;
        for e in self.active_effects.iter_mut() {
            if let EffectKind::StatBuff { steps, .. } = &mut e.kind {
                if *steps < 0 {
                    *steps *= 2;
                    n += 1;
                }
            }
        }
        n
    }

    /// 回合末 TTL 衰减。返回被移除的效果。
    pub fn decrement_ttl(&mut self) -> Vec<Effect> {
        let mut removed = Vec::new();
        let mut surviving = Vec::new();
        for mut e in self.active_effects.drain(..) {
            if e.ttl > 0 {
                e.ttl -= 1;
                if e.ttl <= 0 {
                    removed.push(e);
                    continue;
                }
            }
            surviving.push(e);
        }
        self.active_effects = surviving;
        removed
    }

    pub fn process_pending_effects(&mut self) -> Vec<Effect> {
        let mut activated = Vec::new();
        let mut remaining = Vec::new();
        let pending = std::mem::take(&mut self.pending_effects);
        for (mut eff, delay) in pending {
            let delay = delay - 1;
            if delay <= 0 {
                self.add_effect(eff.clone());
                activated.push(eff);
            } else {
                remaining.push((eff, delay));
            }
        }
        self.pending_effects = remaining;
        activated
    }

    /// consume_pending_modifiers：消耗匹配 if_type 的 pending modifiers。
    pub fn consume_pending_modifiers(&mut self, skill_type: &str) -> Vec<PendingModifier> {
        let mut consumed = Vec::new();
        let mut remaining = Vec::new();
        let pending = std::mem::take(&mut self.pending_modifiers);
        for m in pending {
            let matched = match &m.if_type {
                None => true,
                Some(t) if t.is_empty() => true,
                Some(t) => skill_type_matches(skill_type, t),
            };
            if matched {
                let cur = self.modifiers.get(&m.stat).copied();
                let nv = match m.mode.as_str() {
                    "set" => m.value,
                    "add" => cur.unwrap_or(0.0) + m.value,
                    "multiply" => {
                        if cur.is_some() {
                            cur.unwrap() * m.value
                        } else {
                            m.value
                        }
                    }
                    _ => m.value,
                };
                self.modifiers.insert(m.stat.clone(), nv);
                consumed.push(m);
            } else {
                remaining.push(m);
            }
        }
        self.pending_modifiers = remaining;
        consumed
    }

    pub fn inc_counter(&mut self, key: &str, delta: i64) -> i64 {
        let v = self.counters.get(key).copied().unwrap_or(0) + delta;
        self.counters.insert(key.to_string(), v);
        v
    }

    /// atk_with_modifiers 等：round(initial * (1 + _modifiers[stat]))。
    /// Python _rebuild_stat_cache：base 缺省 100。
    /// py Sprite.{atk,def,sp_atk,sp_def}_with_modifiers：带缓存的读
    /// （缓存仅在 _invalidate_stat_cache 时失效——transform 后保持旧值，
    /// 属 py 既有行为，必须镜像）。其他 stat 无缓存，按需计算。
    pub fn stat_with_modifiers(&self, stat: &str) -> i64 {
        match stat {
            "atk" | "def" | "sp_atk" | "sp_def" => {
                let mut cache = self.stat_cache.borrow_mut();
                if cache.is_none() {
                    *cache = Some(self.compute_stat_cache());
                }
                let c = cache.as_ref().unwrap();
                match stat {
                    "atk" => c[0],
                    "def" => c[1],
                    "sp_atk" => c[2],
                    _ => c[3],
                }
            }
            _ => {
                let base = self.initial_stats.get(stat).copied().unwrap_or(100);
                let m = self.modifiers.get(stat).copied().unwrap_or(0.0);
                crate::damage::py_round(base as f64 * (1.0 + m))
            }
        }
    }

    /// py Sprite._invalidate_stat_cache。
    pub fn invalidate_stat_cache(&self) {
        *self.stat_cache.borrow_mut() = None;
    }

    /// py Sprite._rebuild_stat_cache。
    fn compute_stat_cache(&self) -> [i64; 4] {
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            eprintln!(
                "[rust stat-cache] {} atk_mod={:?} base={:?}",
                self.name(),
                self.modifiers.get("atk"),
                self.initial_stats.get("atk")
            );
        }        let atk_base = self.initial_stats.get("atk").copied().unwrap_or(100);
        let def_base = self.initial_stats.get("def").copied().unwrap_or(100);
        let sp_atk_base = self.initial_stats.get("sp_atk").copied().unwrap_or(100);
        let sp_def_base = self.initial_stats.get("sp_def").copied().unwrap_or(100);
        let atk_mod = self.modifiers.get("atk").copied().unwrap_or(0.0);
        let def_mod = self.modifiers.get("def").copied().unwrap_or(0.0);
        let sp_atk_mod = self.modifiers.get("sp_atk").copied().unwrap_or(0.0);
        let sp_def_mod = self.modifiers.get("sp_def").copied().unwrap_or(0.0);
        [
            crate::damage::py_round(atk_base as f64 * (1.0 + atk_mod)),
            crate::damage::py_round(def_base as f64 * (1.0 + def_mod)),
            crate::damage::py_round(sp_atk_base as f64 * (1.0 + sp_atk_mod)),
            crate::damage::py_round(sp_def_base as f64 * (1.0 + sp_def_mod)),
        ]
    }

    /// _modifiers 直读修正。
    pub fn modifier(&self, key: &str, default: f64) -> f64 {
        self.modifiers.get(key).copied().unwrap_or(default)
    }
}

fn effect_is_category(e: &Effect, category: &str) -> bool {
    match category {
        "stat" => e.is_stat_buff(),
        "abnormal" => e.is_abnormal(),
        "state" => e.is_state(),
        _ => true,
    }
}

/// sprite._skill_type_matches
pub fn skill_type_matches(skill_type: &str, if_type: &str) -> bool {
    match if_type {
        "attack" => matches!(skill_type, "物攻" | "魔攻" | "动态攻击"),
        "defense" => skill_type == "防御",
        "status" => skill_type == "状态",
        _ => false,
    }
}

// ── 道具 / 玩家 ──

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Item {
    pub name: String,
    pub max_uses: i64,
    #[serde(default)]
    pub cooldown_turns: i64,
    #[serde(default)]
    pub uses: i64,
    #[serde(default)]
    pub last_use_turn: i64,
}

impl Item {
    pub fn can_use(&self, current_turn: i64) -> bool {
        if self.uses >= self.max_uses {
            return false;
        }
        !(self.cooldown_turns != 0
            && self.uses > 0
            && current_turn - self.last_use_turn < self.cooldown_turns)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PlayerState {
    pub name: String,
    pub team: Vec<Sprite>,
    #[serde(default = "four_i")]
    pub lives: i64,
    #[serde(default)]
    pub active_index: usize,
    #[serde(default)]
    pub item: Option<Item>,
    #[serde(default)]
    pub devotion: IMap,
}

fn four_i() -> i64 { 4 }

impl PlayerState {
    pub fn active(&self) -> &Sprite {
        &self.team[self.active_index]
    }
    pub fn active_mut(&mut self) -> &mut Sprite {
        &mut self.team[self.active_index]
    }
    pub fn alive_sprites(&self) -> Vec<usize> {
        self.team
            .iter()
            .enumerate()
            .filter(|(_, s)| !s.is_fainted())
            .map(|(i, _)| i)
            .collect()
    }
    pub fn find_replacement(&self) -> Option<usize> {
        for i in self.alive_sprites() {
            if i != self.active_index {
                return Some(i);
            }
        }
        None
    }
}

// ── 印记（GlobalEffects）──

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct Mark {
    pub name: String,
    #[serde(default)]
    pub source: String,
    #[serde(default = "default_scope")]
    pub scope: String,
    #[serde(default)]
    pub ttl: i64,
    #[serde(default)]
    pub stacks: i64,
    #[serde(default = "default_positive")]
    pub category: String,
    #[serde(default)]
    pub power_bonus: i64,
    #[serde(default)]
    pub damage_mult: f64,
    #[serde(default)]
    pub speed_penalty: i64,
    #[serde(default)]
    pub energy_mod: i64,
    #[serde(default)]
    pub turn_end_energy: i64,
    #[serde(default)]
    pub turn_end_damage_pct: f64,
    #[serde(default)]
    pub switch_damage_pct: f64,
    #[serde(default)]
    pub switch_energy_loss: i64,
    #[serde(default)]
    pub starfall_damage: i64,
    #[serde(default)]
    pub condition: String,
}

fn default_positive() -> String { "positive".into() }

impl Mark {
    pub fn is_positive(&self) -> bool { self.category == "positive" }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct GlobalState {
    #[serde(default)]
    pub weather: String,
    #[serde(default)]
    pub weather_turns: i64,
    #[serde(default)]
    pub mark_effects: BTreeMap<String, Vec<Mark>>,
}

// ── 延迟 / 调度 ──

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PendingEffect {
    #[serde(default)]
    pub team: String,
    #[serde(default)]
    pub effect: IrPayload,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct PendingEscape {
    #[serde(default)]
    pub team: String,
    #[serde(default)]
    pub sprite_idx: usize,
    /// py 存的是 user_name（replayer.py:1331 / battle.py:1525），urgent 的
    /// 即时结算按【在场精灵名字】校验（battle_mechanics.py:460），按下标会
    /// 在双重脱离时误匹配新登场者（spec_0164 sim19 多脱离一次）。
    #[serde(default)]
    pub user_name: String,
    #[serde(default)]
    pub inherit: bool,
    /// py：仅 urgent 的 pending escape 会被 _resolve_pending_escape_if_urgent
    /// 即时结算；普通脱离等玩家/agent 选择（headless 下保持挂起）
    #[serde(default)]
    pub urgent: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ScheduledEffect {
    pub turn: i64,
    #[serde(default = "default_turn_start_s")]
    pub phase: String,
    #[serde(default)]
    pub effects: Vec<IrPayload>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub ctx_snapshot: IrPayload,
}

fn default_turn_start_s() -> String { "turn_start".into() }

// ── 对局规格输入（Python 侧构建）──

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SpriteSpec {
    pub name: String,
    #[serde(default)]
    pub form: String,
    #[serde(default)]
    pub number: String,
    #[serde(default)]
    pub attributes: String,
    #[serde(default)]
    pub ability: String,
    #[serde(default)]
    pub ability_id: i64,
    #[serde(default)]
    pub pre_species: String,
    #[serde(default)]
    pub bloodline: String,
    #[serde(default)]
    pub bloodline_skills: IMap,
    #[serde(default)]
    pub base: IMap,
    #[serde(default)]
    pub initial_stats: IMap,
    #[serde(default)]
    pub nature: Option<String>,
    #[serde(default)]
    pub iv: IMap,
    #[serde(default = "ten_i")]
    pub energy: i64,
    pub skills: Vec<crate::data::skills::SkillJson>,
    /// 进化之力目标形态（Python 导出器预计算）
    #[serde(default)]
    pub leader_form: Option<LeaderForm>,
    /// 愿力血脉技能
    #[serde(default)]
    pub bloodline_skill: Option<crate::data::skills::SkillJson>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PlayerSpec {
    pub name: String,
    #[serde(default = "four_i")]
    pub lives: i64,
    #[serde(default)]
    pub item: Option<Item>,
    #[serde(default)]
    pub devotion: IMap,
    pub sprites: Vec<SpriteSpec>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BattleSpec {
    #[serde(default)]
    pub weather: String,
    #[serde(default = "four_i2")]
    pub lives: i64,
    #[serde(default)]
    pub seed: i64,
    pub players: [PlayerSpec; 2],
}

fn four_i2() -> i64 { 4 }

// ── 对局可变状态 ──

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BattleState {
    pub players: [PlayerState; 2], // [A, B]
    #[serde(default)]
    pub globals: GlobalState,
    #[serde(default)]
    pub turn: i64,
    #[serde(default)]
    pub winner: Option<String>,
    #[serde(default)]
    pub team_counters: BTreeMap<String, IMap>,
    /// 引擎级 pending_effects：team → effects（borrowed_restore 等共用）
    #[serde(default)]
    pub pending_effects: BTreeMap<String, Vec<IrPayload>>,
    #[serde(default)]
    pub scheduled_effects: Vec<ScheduledEffect>,
    #[serde(default)]
    pub pending_escape: Option<PendingEscape>,
    /// 借用/愿力还原：键 "team:slot"
    #[serde(default)]
    pub borrowed_restore: BTreeMap<String, BattleSkill>,
    #[serde(default)]
    pub wish_restore: BTreeMap<String, BattleSkill>,
    // ── VM 引擎级状态 ──
    #[serde(default)]
    pub burst_effects: BTreeMap<String, Vec<(String, Vec<IrPayload>)>>,
    #[serde(default)]
    pub burst_names: BTreeMap<String, BTreeSet<String>>,
    #[serde(default)]
    pub counter_values: IMap,
    /// 直改修正生效中的精灵集合（team, sprite_index）
    #[serde(default)]
    pub direct_mod_sprite_ids: BTreeSet<(String, usize)>,
    /// skill_history：skill_id → [(技能名, IR 载荷数组, tag)]
    #[serde(default)]
    pub skill_history: BTreeMap<u64, Vec<(String, Vec<IrPayload>, IrPayload)>>,
    #[serde(default)]
    pub skill_tags: BTreeMap<u64, IMap>,
    #[serde(default)]
    pub devotion_effect_cache: BTreeMap<String, Vec<IrPayload>>,
    /// py battle._mcts_sim：MCTS 仿真模式（跳过 UI 显示效果创建与事件字符串）。
    /// 不参与序列化与摘要——只影响 replayer 的显示效果分支。
    #[serde(skip)]
    pub mcts_sim: bool,
}

pub const TEAM_A: &str = "A";
pub const TEAM_B: &str = "B";

impl BattleState {
    pub fn opponent_of(team: &str) -> &str {
        if team == TEAM_A { TEAM_B } else { TEAM_A }
    }

    /// py restore_mutable_state 尾部对全部精灵 `_invalidate_stat_cache()`
    /// （battle.py:493-497）：MCTS 回滚用整状态克隆还原，克隆会把旧缓存
    /// 一起带回来——必须在还原后显式清空，否则 transform（萌化换形态）
    /// 之后的四维一直是退化前旧值（spec_0141：晕晕鸡 192 vs py 176）。
    pub fn invalidate_all_stat_caches(&mut self) {
        for pi in 0..2 {
            for sprite in self.players[pi].team.iter_mut() {
                sprite.invalidate_stat_cache();
            }
        }
    }

    pub fn player(&self, team: &str) -> &PlayerState {
        if team == TEAM_A { &self.players[0] } else { &self.players[1] }
    }

    pub fn player_mut(&mut self, team: &str) -> &mut PlayerState {
        if team == TEAM_A { &mut self.players[0] } else { &mut self.players[1] }
    }

    pub fn self_opp_mut(&mut self, team: &str) -> (&mut Sprite, &mut Sprite) {
        let (p0, p1) = self.players.split_at_mut(1);
        let (self_p, opp_p) = if team == TEAM_A {
            (&mut p0[0], &mut p1[0])
        } else {
            (&mut p1[0], &mut p0[0])
        };
        let si = self_p.active_index;
        let oi = opp_p.active_index;
        (&mut self_p.team[si], &mut opp_p.team[oi])
    }

    /// 从规格构建（Python 侧已计算好 initial_stats/HP）。
    pub fn from_spec(spec: &BattleSpec) -> BattleState {
        let mut players = Vec::new();
        for ps in &spec.players {
            let team = Vec::new();
            let mut player = PlayerState {
                name: ps.name.clone(),
                team,
                lives: ps.lives,
                active_index: 0,
                item: ps.item.clone(),
                devotion: ps.devotion.clone(),
            };
            for ss in &ps.sprites {
                let species = Species {
                    name: ss.name.clone(),
                    number: ss.number.clone(),
                    attributes: ss.attributes.clone(),
                    bloodline: ss.bloodline.clone(),
                    ability: ss.ability.clone(),
                    ability_id: ss.ability_id,
                    pre_species: ss.pre_species.clone(),
                    bloodline_skills: ss.bloodline_skills.clone(),
                    base: ss.base.clone(),
                };
                let hp = ss.initial_stats.get("hp").copied().unwrap_or(0);
                let skills: Vec<BattleSkill> =
                    ss.skills.iter().cloned().map(BattleSkill::from_base).collect();
                let bloodline = if !ss.bloodline.is_empty() {
                    ss.bloodline.clone()
                } else {
                    species.elements().first().cloned().unwrap_or_default()
                };
                let sprite = Sprite {
                    species,
                    bloodline,
                    bloodline_skills: ss.bloodline_skills.clone(),
                    skills,
                    initial_stats: ss.initial_stats.clone(),
                    nature: ss.nature.clone(),
                    iv: ss.iv.clone(),
                    current_hp: hp,
                    max_hp: hp,
                    energy: ss.energy,
                    active_effects: vec![],
                    entry_turn: 0,
                    counters: BTreeMap::new(),
                    first_action: true,
                    first_action_battle: true,
                    pending_return: false,
                    interrupted: false,
                    locked_turns: 0,
                    extra_skill_use: false,
                    charging: false,
                    charged_skill_index: -1,
                    charged_skill_ref: None,
                    modifiers: BTreeMap::new(),
                    mod_scopes: BTreeMap::new(),
                    last_abnormal_dmg: BTreeMap::new(),
                    pending_effects: vec![],
                    pending_modifiers: vec![],
                    trait_suppressed: false,
                    stat_cache: std::cell::RefCell::new(None),
                    moe_chain: vec![],
                    moe_position: 0,
                    moe_origin: None,
                    moe_origin_skills: vec![],
                    leader_form: ss.leader_form.clone(),
                    bloodline_skill: ss.bloodline_skill.clone(),
                    trait_direct_effects: vec![],
                    direct_mod_tracked: BTreeMap::new(),
                };
                player.team.push(sprite);
            }
            players.push(player);
        }
        BattleState {
            players: [players.remove(0), players.remove(0)],
            globals: GlobalState {
                weather: spec.weather.clone(),
                weather_turns: 0,
                mark_effects: BTreeMap::new(),
            },
            turn: 0,
            winner: None,
            team_counters: BTreeMap::from([
                (TEAM_A.to_string(), IMap::new()),
                (TEAM_B.to_string(), IMap::new()),
            ]),
            pending_effects: BTreeMap::new(),
            scheduled_effects: vec![],
            pending_escape: None,
            borrowed_restore: BTreeMap::new(),
            wish_restore: BTreeMap::new(),
            burst_effects: BTreeMap::new(),
            burst_names: BTreeMap::new(),
            counter_values: IMap::new(),
            direct_mod_sprite_ids: BTreeSet::new(),
            skill_history: BTreeMap::new(),
            skill_tags: BTreeMap::new(),
            devotion_effect_cache: BTreeMap::new(),
            mcts_sim: false,
        }
    }
}

// Val 未直接使用但保留导入以备扩展
#[allow(unused)]
fn _assert_val_use(_: std::marker::PhantomData<Val>) {}
