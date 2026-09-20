//! replayer — Journal → 可变状态回放（移植自 backend/engine/replayer.py，
//! headless 语义：不做 UI 显示串，状态变更全部镜像）。

use crate::battle_state::{BattleSkill, BattleState, Effect, EffectKind, Mark, PendingModifier, Sprite};
use crate::journal::Mutation;
use crate::resolve::Val;
use crate::rng::PyRandom;
use crate::statics;
use serde_json::Value as J;
use std::collections::{BTreeMap, BTreeSet};

const STAGE_STATS: &[&str] = &["atk", "def", "sp_atk", "sp_def", "speed"];
const RATIO_STATS: &[&str] = &[
    "power_mult", "damage_mult", "damage_reduction", "energy_cost_mult",
    "heal_reverse", "life_drain", "ignore_resistance", "ignore_mods", "survive",
];
const SKILL_DISTRIBUTE_STATS: &[&str] = &["energy_cost", "power", "combo", "priority"];
/// 修饰符存总量（1.0+加成）的比率型：display_mult = delta - 1.0。
const TOTAL_BASED_RATIO_STATS: &[&str] = &["power_mult", "damage_mult"];
const SPRITE_LEVEL_ATTRS: &[&str] =
    &["max_energy", "starfall_consume_ratio", "immune_abnormal", "immune_stat_down"];
const ATTACK_TYPES: &[&str] = &["物攻", "魔攻", "动态攻击"];

pub fn is_ratio_stat(stat: &str) -> bool {
    RATIO_STATS.contains(&stat)
}

pub fn matches_skill_type(skill_filter: &str, skill_type: &str) -> bool {
    match skill_filter {
        "" | "all" => true,
        "attack" => ATTACK_TYPES.contains(&skill_type),
        "defense" => skill_type == "防御",
        "status" => skill_type == "状态",
        _ => true,
    }
}

/// eval_skill_where（engine/modifiers.py）。info: name/energy_cost/element/skill_type。
pub fn eval_skill_where(skill_where: Option<&J>, info: &BTreeMap<String, Val>) -> bool {
    let Some(sw) = skill_where else { return true };
    if !sw.is_object() {
        return true;
    }
    let obj = sw.as_object().unwrap();
    if !obj.contains_key("q") && !obj.contains_key("op") {
        for (field, expected) in obj {
            if expected.is_null() {
                return false;
            }
            let Some(actual) = info.get(field) else { return false };
            let Some(ev) = crate::resolve::scalar_of(expected) else { return false };
            if actual != &ev {
                return false;
            }
        }
        return true;
    }
    let q = sw.get("q").and_then(|x| x.as_str()).unwrap_or("");
    let op = sw.get("op").and_then(|x| x.as_str()).unwrap_or("eq");
    let expected = sw.get("value").cloned().unwrap_or(J::Null);
    let Some(actual) = info.get(q) else { return false };
    let ev = crate::resolve::scalar_of(&expected);
    let pair = || (actual.as_num(), ev.as_ref().and_then(|e| e.as_num()));
    match op {
        "gt" => matches!(pair(), (Some(a), Some(e)) if a > e),
        "gte" => matches!(pair(), (Some(a), Some(e)) if a >= e),
        "lt" => matches!(pair(), (Some(a), Some(e)) if a < e),
        "lte" => matches!(pair(), (Some(a), Some(e)) if a <= e),
        "eq" => match ev.as_ref() {
            Some(e) => actual == e,
            None => false,
        },
        "neq" => match ev.as_ref() {
            Some(e) => actual != e,
            None => true,
        },
        _ => false,
    }
}

#[derive(Clone, Copy, PartialEq)]
pub struct Loc(pub usize, pub usize); // (player_idx, sprite_idx)

/// 回放上下文（headless 语义）。
pub struct Replayer<'a> {
    pub state: &'a mut BattleState,
    pub team: &'static str,
    pub self_idx: usize,
    pub opp_idx: usize,
    /// py `self._self_skill`：以技能实例 id 持有（视角交换后仍指向原技能；
    /// 按下标会错误解析到交换后精灵的技能表——spec_0066 的 skill.压扁 键根因）
    pub self_skill_id: Option<u64>,
    /// py `self._leaving`：post_enemy_leave 的离场精灵（source=sprite_opp 用）
    pub leaving_loc: Option<Loc>,
    /// 视角交换（py post_damage/post_ko 的 owner 为防守方时仅换 self/opp
    /// 精灵对象，**team 不变**——lives/mark/inherit 等按 team 解析的都
    /// 仍用原 acting 方）
    pub swapped: bool,
    /// py `_trait_sourcing`：特性/观察者 then 回放期间为 true，
    /// StatChange 同步的 StatBuffEffect 标 is_inherent
    pub trait_sourcing: bool,
    /// py `_energy_deltas`：EnergyChange 的实际增量（受能量上限截断），
    /// 按 journal 中 EnergyChange 的出现顺序追加
    pub energy_actuals: Vec<i64>,
    pub rng: &'a mut PyRandom,
    cleared_position_stats: BTreeSet<String>,
    /// py replayer.is_headless（= battle._mcts_sim）：仿真模式跳过纯 UI 的
    /// 显示效果创建（StatBuff 展示用），战斗逻辑数据不受影响。
    pub headless: bool,
}

impl<'a> Replayer<'a> {
    pub fn new(
        state: &'a mut BattleState,
        team: &'static str,
        self_idx: usize,
        opp_idx: usize,
        self_skill_idx: Option<usize>,
        rng: &'a mut PyRandom,
    ) -> Self {
        // 构造期把下标解析为技能实例 id（沿用原 self 精灵的技能表）
        let pi = if team == "A" { 0 } else { 1 };
        let self_skill_id = self_skill_idx
            .and_then(|i| state.players[pi].team[self_idx].skills.get(i))
            .map(|b| b.skill_id);
        let headless = state.mcts_sim;
        Replayer {
            state,
            team,
            self_idx,
            opp_idx,
            self_skill_id,
            leaving_loc: None,
            swapped: false,
            trait_sourcing: false,
            energy_actuals: Vec::new(),
            headless,
            rng,
            cleared_position_stats: BTreeSet::new(),
        }
    }

    /// 显式指定技能实例 id（视角交换后的 post 事件用——下标属于原 acting
    /// 精灵，不能按交换后的 self 解析）。
    pub fn new_with_skill_id(
        state: &'a mut BattleState,
        team: &'static str,
        self_idx: usize,
        opp_idx: usize,
        self_skill_id: Option<u64>,
        rng: &'a mut PyRandom,
    ) -> Self {
        let headless = state.mcts_sim;
        Replayer {
            state,
            team,
            self_idx,
            opp_idx,
            self_skill_id,
            leaving_loc: None,
            swapped: false,
            trait_sourcing: false,
            energy_actuals: Vec::new(),
            headless,
            rng,
            cleared_position_stats: BTreeSet::new(),
        }
    }

    /// 按实例 id 定位技能 → (player_idx, sprite_idx, skill_pos)。
    fn find_skill(&self, id: u64) -> Option<(usize, usize, usize)> {
        for (pi, p) in self.state.players.iter().enumerate() {
            for (si, sp) in p.team.iter().enumerate() {
                if let Some(pos) = sp.skills.iter().position(|b| b.skill_id == id) {
                    return Some((pi, si, pos));
                }
            }
        }
        None
    }

    fn self_player(&self) -> usize {
        let base = if self.team == "A" { 0 } else { 1 };
        if self.swapped { 1 - base } else { base }
    }
    fn opp_player(&self) -> usize {
        1 - self.self_player()
    }
    fn team_idx(&self, team: &str) -> usize {
        if team == "A" { 0 } else { 1 }
    }
    fn other_team_key(&self) -> &'static str {
        if self.team == "A" { "B" } else { "A" }
    }

    /// replayer._target_sprite。
    fn target_loc(&mut self, target: &str) -> Loc {
        if target.starts_with("skill_at_") {
            return Loc(self.self_player(), self.self_idx);
        }
        match target {
            "sprite_self" | "self" | "team_own" | "skill_off_0" => Loc(self.self_player(), self.self_idx),
            "ally_new" => {
                let p = self.self_player();
                let ai = self.state.players[p].active_index;
                Loc(p, ai)
            }
            "enemy_new" => {
                let p = self.opp_player();
                let ai = self.state.players[p].active_index;
                Loc(p, ai)
            }
            "sprite_bench" => {
                let p = self.self_player();
                let ai = self.state.players[p].active_index;
                let bench: Vec<usize> = self.state.players[p]
                    .team
                    .iter()
                    .enumerate()
                    .filter(|(i, s)| *i != ai && !s.is_fainted())
                    .map(|(i, _)| i)
                    .collect();
                if bench.is_empty() {
                    Loc(p, self.self_idx)
                } else {
                    let idx = *self.rng.choice(&bench);
                    Loc(p, idx)
                }
            }
            _ => Loc(self.opp_player(), self.opp_idx),
        }
    }

    fn sprite_mut(&mut self, loc: Loc) -> &mut Sprite {
        &mut self.state.players[loc.0].team[loc.1]
    }

    fn mark_coexist(&self, loc: Loc) -> bool {
        self.state.players[loc.0].team[loc.1]
            .modifiers
            .get("mark_coexist")
            .copied()
            .unwrap_or(0.0)
            != 0.0
    }

    pub fn replay(&mut self, journal: &[Mutation]) {
        self.cleared_position_stats.clear();
        for m in journal {
            self.apply(m);
        }
    }

    pub fn apply(&mut self, m: &Mutation) {
        match m {
            Mutation::StatChange { .. } => self.apply_stat_change(m),
            Mutation::ModifierInjection { .. } => self.apply_modifier(m),
            Mutation::Damage { .. } => self.apply_damage(m),
            Mutation::Heal { .. } => self.apply_heal(m),
            Mutation::EnergyChange { .. } => self.apply_energy_change(m),
            Mutation::MarkChange { .. } => self.apply_mark_change(m),
            Mutation::AbnormalChange { .. } => self.apply_abnormal_change(m),
            Mutation::WeatherSet { weather, turns } => {
                self.state.globals.weather = weather.clone();
                self.state.globals.weather_turns = *turns;
            }
            Mutation::Dispel { .. } => self.apply_dispel(m),
            Mutation::Steal { .. } => self.apply_steal(m),
            Mutation::Tick { .. } => self.apply_tick(m),
            Mutation::Double { .. } => self.apply_double(m),
            Mutation::EffectDelta { .. } => self.apply_effect_delta(m),
            Mutation::Charge { target } => {
                let loc = self.target_loc(target);
                sync_state_effect(self.sprite_mut(loc), "charging");
            }
            Mutation::Escape { target, inherit, urgent, .. } => {
                // py replayer.py:1323-1333：user_name 取 _target_sprite(m.target) 的名字
                let loc = self.target_loc(target);
                if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                    let nm = self.sprite_mut(loc).name();
                    eprintln!("[rust escape-pending] team={} self=({},{}) target={} name={} urgent={}",
                        self.team, self.self_player(), self.self_idx, target, nm, urgent);
                }
                let user_name = self.sprite_mut(loc).name();
                self.state.pending_escape = Some(crate::battle_state::PendingEscape {
                    team: self.team.to_string(),
                    sprite_idx: self.self_idx,
                    user_name,
                    inherit: *inherit,
                    urgent: *urgent,
                });
            }
            Mutation::Return { target } => {
                let loc = self.target_loc(target);
                self.sprite_mut(loc).pending_return = true;
            }
            Mutation::Lock { target, turns } => {
                let loc = self.target_loc(target);
                let sprite = self.sprite_mut(loc);
                sprite.locked_turns = *turns;
                sync_state_effect(sprite, "locked");
            }
            Mutation::Interrupt { target } => {
                let loc = self.target_loc(target);
                let sprite = self.sprite_mut(loc);
                sprite.interrupted = true;
                sync_state_effect(sprite, "interrupted");
            }
            Mutation::Exchange { what, .. } => self.apply_exchange(what),
            Mutation::Reset { .. } => {}
            Mutation::Redirect { .. } => {}
            Mutation::Replay { .. } | Mutation::Borrow { .. } => {}
            Mutation::BurstGrant { .. } => self.apply_burst_grant(m),
            Mutation::TeamCounterDelta { target, key, delta } => {
                let t = if target == "opp" { self.other_team_key() } else { self.team };
                let counters = self.state.team_counters.entry(t.to_string()).or_default();
                *counters.entry(key.clone()).or_insert(0) += delta;
            }
            Mutation::LivesDelta { target_team, delta } => {
                let t = if target_team == "opp" { self.other_team_key() } else { self.team };
                let t_idx = self.team_idx(t);
                let player = &mut self.state.players[t_idx];
                if *delta < 0 && player.lives <= 0 {
                    return;
                }
                player.lives += delta;
            }
            Mutation::ScheduleEntry { turns, at, then } => {
                self.state.scheduled_effects.push(crate::battle_state::ScheduledEffect {
                    turn: self.state.turn + turns,
                    phase: at.clone(),
                    effects: then.clone(),
                    source: None,
                    // py _apply_schedule_entry：ctx_snapshot 记录回放方 team，
                    // 回合末执行时按该队视角解析 sprite_opp/self
                    ctx_snapshot: serde_json::json!({ "team": self.team }),
                });
            }
            Mutation::InheritEffects { .. } => self.apply_inherit_effects(m),
            Mutation::Transform { .. } => self.apply_transform(m),
            Mutation::TraitInteraction { .. } => self.apply_trait_interaction(m),
            Mutation::GainSkills { .. } => {}
            Mutation::CounterRegister { .. } => {}
        }
    }

    /// py `_apply_inherit_effects_mutation`（洁癖类：离场后增益被换上的精灵继承）。
    fn apply_inherit_effects(&mut self, m: &Mutation) {
        let Mutation::InheritEffects {
            source_key,
            target_key,
            scope,
            via_pending,
            inherit_stat_effects,
            ..
        } = m
        else {
            return;
        };
        // source：self → replayer.self；sprite_opp 且存在 leaving → leaving；
        // 其余 → opp（py _resolve_source）
        let src_loc = match source_key.as_str() {
            "self" => Loc(self.self_player(), self.self_idx),
            "sprite_opp" => self
                .leaving_loc
                .unwrap_or(Loc(self.opp_player(), self.opp_idx)),
            _ => Loc(self.opp_player(), self.opp_idx),
        };
        let inherited: Vec<Effect> = {
            let src = &self.state.players[src_loc.0].team[src_loc.1];
            src.active_effects
                .iter()
                .filter(|e| {
                    if *inherit_stat_effects {
                        e.is_stat_buff()
                            && !matches!(
                                &e.kind,
                                EffectKind::StatBuff { is_inherent: true, .. }
                            )
                    } else {
                        e.scope == *scope
                    }
                })
                .cloned()
                .collect()
        };
        if inherited.is_empty() {
            return;
        }
        if *via_pending {
            let payloads: Vec<J> = inherited
                .iter()
                .filter_map(|e| serde_json::to_value(e).ok())
                .collect();
            self.state
                .pending_effects
                .entry(self.team.to_string())
                .or_default()
                .extend(payloads);
            return;
        }
        let target_loc = if target_key == "enemy_new" {
            Loc(self.opp_player(), self.opp_idx)
        } else {
            Loc(self.self_player(), self.self_idx)
        };
        for e in inherited {
            self.state.players[target_loc.0].team[target_loc.1].add_effect(e);
        }
    }

    fn apply_stat_change(&mut self, m: &Mutation) {
        let Mutation::StatChange { target, stat, steps, scope, source, .. } = m else { return };
        let loc = self.target_loc(target);
        let immune = *steps < 0
            && STAGE_STATS.contains(&stat.as_str())
            && self.check_immune(loc, "immune_stat_down", stat);
        if immune {
            return;
        }
        let src = source.clone().unwrap_or_else(|| "skill".into());
        let inherent = self.trait_sourcing;
        sync_stat_buff_effect(self.sprite_mut(loc), stat, *steps, scope, &src, inherent, "add");
        // py _apply_stat_change 421：`if self.is_headless: return ""` ——
        // 以下为纯 UI 显示逻辑，MCTS 仿真模式整体跳过（stages/非 stage 两块）。
        if self.headless {
            return;
        }
        // display-only 效果（py _apply_stat_change 尾部）：_STAGE_STATS 且有
        // source 时创建 steps=0 显示 StatBuff（速度用 display_value，其余
        // 用 display_mult=steps×10%，additive 累计）
        if STAGE_STATS.contains(&stat.as_str()) {
            if let Some(src_opt) = source.as_deref().filter(|s| !s.is_empty()) {
                let sprite = self.sprite_mut(loc);
                if stat == "speed" {
                    sync_mult_display_effect(
                        sprite, stat, 0.0, scope, src_opt, Some(*steps as f64 * 10.0), true,
                    );
                } else {
                    sync_mult_display_effect(
                        sprite, stat, *steps as f64 * 0.1, scope, src_opt, None, true,
                    );
                }
            }
        }
        // py 443-445：非舞台属性（power/energy_cost/priority/combo）且有
        // source、steps≠0 → display_value = steps×单位 的显示效果
        else if let Some(src_opt) = source.as_deref().filter(|s| !s.is_empty()) {
            if *steps != 0
                && matches!(stat.as_str(), "power" | "energy_cost" | "priority" | "combo")
            {
                let sprite = self.sprite_mut(loc);
                sync_mult_display_effect(
                    sprite,
                    stat,
                    0.0,
                    scope,
                    src_opt,
                    Some(*steps as f64 * step_unit(stat) as f64),
                    false,
                );
            }
        }
    }

    fn check_immune(&self, loc: Loc, immune_type: &str, target_name: &str) -> bool {
        let sprite = &self.state.players[loc.0].team[loc.1];
        for e in &sprite.active_effects {
            if let EffectKind::Modifier { attr, .. } = &e.kind {
                if attr == immune_type && (e.name.is_empty() || e.name == target_name) {
                    return true;
                }
            }
        }
        false
    }

    fn apply_modifier(&mut self, m: &Mutation) {
        let Mutation::ModifierInjection {
            target,
            stat,
            value,
            scope,
            mode,
            name,
            skill_filter,
            skill_where,
            if_type,
            source,
            on_next,
            ttl,
            ..
        } = m
        else {
            return;
        };
        let m_value = match value {
            Val::F(f) => *f,
            Val::I(i) => *i as f64,
            Val::B(b) => {
                if *b { 1.0 } else { 0.0 }
            }
            _ => return,
        };

        // devotion → player.devotion
        if stat == "devotion" {
            let t = if target == "opp" { self.other_team_key() } else { self.team };
            let t_idx = self.team_idx(t);
            let mut devotion_name = name.clone().unwrap_or_default();
            if devotion_name.is_empty() || devotion_name == "random" {
                let types: Vec<String> = statics::devotion_types().to_vec();
                if types.is_empty() {
                    return;
                }
                if mode == "add" {
                    let total = m_value as i64;
                    for _ in 0..total {
                        let pick = self.rng.choice(&types).clone();
                        *self.state.players[t_idx].devotion.entry(pick).or_insert(0) += 1;
                    }
                    return;
                }
                devotion_name = self.rng.choice(&types).clone();
            }
            let player = &mut self.state.players[t_idx];
            let cur = player.devotion.get(&devotion_name).copied().unwrap_or(0);
            let nv = match mode.as_str() {
                "add" => cur + m_value as i64,
                _ => m_value as i64,
            };
            player.devotion.insert(devotion_name, nv);
            return;
        }

        let loc = self.target_loc(target);
        let skill_scoped = target.starts_with("skill_");

        if *on_next {
            let sprite = self.sprite_mut(loc);
            sprite.pending_modifiers.push(PendingModifier {
                stat: stat.clone(),
                value: m_value,
                mode: mode.clone(),
                if_type: if_type.clone(),
                name: name.clone(),
                source: source.clone(),
            });
            let skill_scoped_on = target.starts_with("skill_");
            if !skill_scoped_on && matches!(scope.as_str(), "turn" | "battlefield" | "persistent") {
                sprite.mod_scopes.insert(stat.clone(), scope.clone());
            }
            return;
        }

        // skill_filter=all → 分发到全部技能
        if !skill_scoped
            && skill_filter.as_deref() == Some("all")
            && SKILL_DISTRIBUTE_STATS.contains(&stat.as_str())
        {
            let mult = self.energy_cost_delta_mult(loc);
            let delta = if stat == "energy_cost" { m_value * mult } else { m_value };
            let n = {
                let sprite = self.sprite_mut(loc);
                sprite.skills.len()
            };
            for i in 0..n {
                let (nv, bs_name) = {
                    let sprite = self.sprite_mut(loc);
                    let bs = &mut sprite.skills[i];
                    let cur = bs.modifiers.get(stat).copied().unwrap_or(0.0);
                    (mode_apply(cur, delta, mode), bs.base.name.clone())
                };
                let sprite = self.sprite_mut(loc);
                let bs = &mut sprite.skills[i];
                bs.modifiers.insert(stat.to_string(), nv);
                if scope == "permanent" && !bs_name.is_empty() {
                    sprite
                        .modifiers
                        .insert(format!("skill.{}.{}", bs_name, stat), nv);
                }
            }
            // display-only 效果（_apply_to_all_skills 尾部：source 非空时同步）
            if let Some(src) = source.as_deref().filter(|s| !s.is_empty()) {
                let delta_disp = delta; // 已含 energy_cost mult
                let sprite = self.sprite_mut(loc);
                sync_mult_display_effect(sprite, stat, 0.0, scope, src, Some(delta_disp), false);
            }
            return;
        }

        // skill_where / 非 all 过滤 → 匹配技能
        if !skill_scoped
            && (skill_where.is_some()
                || (skill_filter.is_some() && skill_filter.as_deref() != Some("all")))
        {
            let mark_mod = self
                .state
                .globals
                .mark_effects
                .get(self.team)
                .map(|marks| {
                    marks
                        .iter()
                        .filter(|mk| mk.energy_mod != 0)
                        .map(|mk| mk.energy_mod * mk.stacks)
                        .sum::<i64>()
                })
                .unwrap_or(0) as f64;
            let mult = self.energy_cost_delta_mult(loc);
            let delta = if stat == "energy_cost" { m_value * mult } else { m_value };
            let mut applied = false;
            let sprite = self.sprite_mut(loc);
            for bs in sprite.skills.iter_mut() {
                let mut info = BTreeMap::new();
                info.insert("name".into(), Val::S(bs.name()));
                info.insert(
                    "energy_cost".into(),
                    Val::I(((bs.energy_cost() as f64) - mark_mod).max(0.0) as i64),
                );
                info.insert("element".into(), Val::S(bs.base.element.clone()));
                info.insert("skill_type".into(), Val::S(bs.base.skill_type.clone()));
                if !eval_skill_where(skill_where.as_ref(), &info) {
                    continue;
                }
                if let Some(f) = skill_filter.as_deref() {
                    if !matches_skill_type(f, &bs.base.skill_type) {
                        continue;
                    }
                }
                let cur = bs.modifiers.get(stat).copied().unwrap_or(0.0);
                let nv = mode_apply(cur, delta, mode);
                bs.modifiers.insert(stat.to_string(), nv);
                applied = true;
            }
            // 直改登记（_apply_to_matching_skills：跨回合持久化，
            // reapply_all_direct_mods 在每回合清理后恢复）
            if applied && scope != "turn" && (mode == "add" || mode == "set") {
                let mut dict = serde_json::Map::new();
                dict.insert("op".into(), J::from("power_mod"));
                dict.insert("attr".into(), J::from(stat.clone()));
                dict.insert("delta".into(), J::from(value.as_num().unwrap_or(0.0)));
                dict.insert("mode".into(), J::from(mode.clone()));
                if let Some(sw) = skill_where {
                    dict.insert("skill_where".into(), sw.clone());
                }
                if let Some(sf) = skill_filter {
                    dict.insert("skill_filter".into(), J::from(sf.clone()));
                }
                if let Some(src) = source {
                    dict.insert("source".into(), J::from(src.clone()));
                }
                if *ttl > 0 {
                    dict.insert("ttl".into(), J::from(*ttl));
                }
                let entry = J::Object(dict);
                let sprite = self.sprite_mut(loc);
                if !sprite.trait_direct_effects.contains(&entry) {
                    sprite.trait_direct_effects.push(entry);
                }
                let team_key = if loc.0 == 0 { "A" } else { "B" };
                self.state
                    .direct_mod_sprite_ids
                    .insert((team_key.to_string(), loc.1));
            }
            // display-only 效果（_apply_to_matching_skills 尾部）
            if applied {
                if let Some(src) = source.as_deref().filter(|s| !s.is_empty()) {
                    let eff_scope = if scope.is_empty() { "battlefield" } else { scope.as_str() };
                    let (dv, dm) = if stat == "energy_cost" {
                        (Some(delta), None)
                    } else if stat == "power_mod" {
                        (Some(delta * 10.0), None)
                    } else if RATIO_STATS.contains(&stat.as_str()) {
                        let bonus = if TOTAL_BASED_RATIO_STATS.contains(&stat.as_str()) {
                            delta - 1.0
                        } else {
                            delta
                        };
                        (None, Some(bonus))
                    } else {
                        (Some(delta), None)
                    };
                    let disp_scope = if stat == "power_mod" { "battlefield" } else { eff_scope };
                    let sprite = self.sprite_mut(loc);
                    sync_matching_display_effect(sprite, stat, disp_scope, src, dv, dm, *ttl);
                }
            }
            return;
        }

        if skill_scoped {
            if let Some(pos) = target.strip_prefix("skill_at_") {
                if !self.cleared_position_stats.contains(stat) {
                    self.cleared_position_stats.insert(stat.clone());
                    let p = self.self_player();
                    let si = self.self_idx;
                    for bs in self.state.players[p].team[si].skills.iter_mut() {
                        bs.modifiers.remove(stat);
                        if stat == "drive" {
                            bs.transmission = bs.base.transmission;
                        } else if stat == "sealed" {
                            bs.sealed = false;
                        }
                    }
                }
                let pos_i: i64 = pos.rsplit('_').next().and_then(|x| x.parse().ok()).unwrap_or(0) - 1;
                let p = self.self_player();
                let si = self.self_idx;
                // py 575-576：add 模式 energy_cost 乘 energy_cost_delta_mult
                let m_value_eff = if stat == "energy_cost" && mode == "add" {
                    m_value * self.energy_cost_delta_mult(Loc(p, si))
                } else {
                    m_value
                };
                let sprite = &mut self.state.players[p].team[si];
                if pos_i >= 0 && (pos_i as usize) < sprite.skills.len() {
                    let bs = &mut sprite.skills[pos_i as usize];
                    if stat == "drive" {
                        bs.transmission = m_value as i64;
                    } else if stat == "sealed" {
                        bs.sealed = m_value != 0.0;
                    }
                    let cur = bs.modifiers.get(stat).copied();
                    let nv = modifier_apply(cur, m_value_eff, mode, stat);
                    bs.modifiers.insert(stat.clone(), nv);
                } else {
                    apply_mode_with_defaults(&mut sprite.modifiers, stat, m_value_eff, mode);
                }
                return;
            }
            if let Some(sid) = self.self_skill_id {
                // py：target_mods = self._self_skill._modifiers（技能对象，
                // 与当前 self 精灵无关）；永久键写到 self 精灵的 _modifiers，
                // 键名取【技能对象】的名字
                // py 575-576：add 模式 energy_cost 乘 energy_cost_delta_mult
                // （仅技能级写入；永久键用原始值——py 549-555 不乘）
                let m_value_eff = if stat == "energy_cost" && mode == "add" {
                    m_value * self.energy_cost_delta_mult(loc)
                } else {
                    m_value
                };
                let located_dbg = self.find_skill(sid);
                if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                    eprintln!(
                        "[rust skill-mod write] stat={stat} v={m_value_eff} sid={sid} found={}",
                        located_dbg.is_some()
                    );
                }
                let located = located_dbg;
                let located = self.find_skill(sid);
                if let Some((spi, ssi, spos)) = located {
                    let (bs_name, nv) = {
                        let bs = &mut self.state.players[spi].team[ssi].skills[spos];
                        let cur = bs.modifiers.get(stat).copied();
                        let nv = modifier_apply(cur, m_value_eff, mode, stat);
                        bs.modifiers.insert(stat.clone(), nv);
                        (bs.base.name.clone(), nv)
                    };
                    if scope == "permanent" && !bs_name.is_empty() {
                        let key = format!("skill.{}.{}", bs_name, stat);
                        // 永久键写入 replayer.self（视角交换后为当前 self）
                        let sprite = self.sprite_mut(loc);
                        let cur = sprite.modifiers.get(&key).copied();
                        let nv = match mode.as_str() {
                            "set" => nv,
                            "add" => cur.unwrap_or(0.0) + m_value,
                            // py replayer.py:555 multiply：键 = (cur or 1.0) ×
                            // 【原始注入值 m.value】，不是技能修正计算后的 nv——
                            // 用 nv 会把技能本体的累积再乘进来（spec_0068：
                            // 山火 power_mult 键 py 2→4 vs rust 2→8）
                            "multiply" => {
                                let base = match cur {
                                    Some(c) if c != 0.0 => c,
                                    _ => 1.0,
                                };
                                base * m_value
                            }
                            _ => nv,
                        };
                        sprite.modifiers.insert(key.clone(), nv);
                        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                            eprintln!(
                                "[rust perm persist] turn={} key={key} +{m_value} -> {nv} (team={t} self={si})",
                                turn = self.state.turn,
                                t = self.team,
                                si = self.self_idx,
                            );
                        }
                    }
                    // py 589-592：skill 级写入走到 _apply_modifier 尾部时同样
                    // 使 target 精灵的属性缓存失效（不区分 skill_scoped）
                    if matches!(
                        stat.as_str(),
                        "atk" | "def" | "sp_atk" | "sp_def" | "damage_reduction"
                            | "power_mult" | "damage_mult" | "energy_cost_mult"
                            | "combo_mult" | "life_drain"
                    ) {
                        self.sprite_mut(loc).invalidate_stat_cache();
                    }
                    return;
                }
                let m_value_eff = if stat == "energy_cost" && mode == "add" {
                    m_value * self.energy_cost_delta_mult(loc)
                } else {
                    m_value
                };
                let sprite = self.sprite_mut(loc);
                apply_mode_with_defaults(&mut sprite.modifiers, stat, m_value_eff, mode);
                return;
            }
        }

        // 默认：sprite._modifiers
        {
            // py 575-576：add 模式 energy_cost 乘 energy_cost_delta_mult
            let m_value_eff = if stat == "energy_cost" && mode == "add" {
                m_value * self.energy_cost_delta_mult(loc)
            } else {
                m_value
            };
            let sprite = self.sprite_mut(loc);
            let cur = sprite.modifiers.get(stat).copied();
            let nv = modifier_apply(cur, m_value_eff, mode, stat);
            sprite.modifiers.insert(stat.clone(), nv);
            // py replayer 589-592：这些 stat 变更使属性缓存失效
            if matches!(
                stat.as_str(),
                "atk" | "def" | "sp_atk" | "sp_def" | "damage_reduction"
                    | "power_mult" | "damage_mult" | "energy_cost_mult"
                    | "combo_mult" | "life_drain"
            ) {
                sprite.invalidate_stat_cache();
            }
        }

        // sprite 级属性同步 ModifierEffect
        if !skill_scoped && SPRITE_LEVEL_ATTRS.contains(&stat.as_str()) {
            let source_str = source.clone().unwrap_or_else(|| "trait".into());
            let sprite = self.sprite_mut(loc);
            let existing = sprite
                .active_effects
                .iter_mut()
                .find(|e| matches!(&e.kind, EffectKind::Modifier { attr, .. } if attr == stat));
            match existing {
                Some(e) => {
                    if let EffectKind::Modifier { value: ev, .. } = &mut e.kind {
                        *ev = m_value;
                    }
                    if stat.starts_with("immune_") {
                        if let Some(n) = name {
                            e.name = n.clone();
                        }
                    }
                }
                None => {
                    let effect_name = if stat.starts_with("immune_") {
                        name.clone().unwrap_or_default()
                    } else {
                        format!("{}-{}", source.clone().unwrap_or_else(|| "trait".into()), stat)
                    };
                    sprite.active_effects.push(Effect {
                        name: effect_name,
                        source: source_str,
                        scope: scope.clone(),
                        ttl: 0,
                        cooldown: 0,
                        kind: EffectKind::Modifier {
                            target: target.clone(),
                            attr: stat.clone(),
                            value: m_value,
                            mode: mode.clone(),
                            skill_where: None,
                        },
                    });
                }
            }
        }

        // 不可见 modifier 的 scope 追踪
        if !skill_scoped && matches!(scope.as_str(), "turn" | "battlefield" | "persistent") {
            self.sprite_mut(loc)
                .mod_scopes
                .insert(stat.clone(), scope.clone());
        }

        // py _apply_modifier 630：`if self.is_headless: return ""` ——
        // 以下（可见 StatBuff + display-only StatBuff）为纯 UI 显示逻辑。
        if self.headless {
            return;
        }
        // 可见 StatBuff（py _apply_modifier 635-647）：combo/priority/
        // life_drain/power_mod 的 steps 型可见效果（_VISIBLE_MOD_STATS）
        if !skill_scoped && m_value != 0.0 {
            if matches!(stat.as_str(), "combo" | "priority" | "life_drain" | "power_mod") {
                let steps = if is_ratio_stat(stat) {
                    (m_value * step_unit(stat) as f64) as i64
                } else {
                    m_value as i64
                };
                if steps != 0 {
                    let src = source.clone().unwrap_or_else(|| "skill".into());
                    let sprite = self.sprite_mut(loc);
                    sync_stat_buff_effect(
                        sprite, stat, steps, scope, &src, false, mode.as_str(),
                    );
                }
            }
        }

        // display-only StatBuff（py _apply_modifier 尾部 648-669：
        // source 非空（特性注入）时创建 steps=0 显示效果。
        // 注意 py 此处【没有】skill_scoped 门——skill_off_0/skill_at_N
        // 的 mutation 也会在 acting 精灵上建显示效果）
        if let Some(src) = source.as_deref().filter(|s| !s.is_empty()) {
            if STAGE_STATS.contains(&stat.as_str()) {
                let sprite = self.sprite_mut(loc);
                sync_mult_display_effect(
                    sprite, stat, m_value, scope, src, None, mode == "add",
                );
            } else if matches!(stat.as_str(), "combo" | "priority" | "life_drain" | "power_mod") {
                if is_ratio_stat(stat) {
                    let sprite = self.sprite_mut(loc);
                    sync_mult_display_effect(sprite, stat, m_value, scope, src, None, false);
                } else if stat == "power_mod" {
                    let sprite = self.sprite_mut(loc);
                    sync_mult_display_effect(
                        sprite, stat, 0.0, scope, src, Some(m_value * 10.0), false,
                    );
                } else {
                    let sprite = self.sprite_mut(loc);
                    sync_mult_display_effect(
                        sprite, stat, 0.0, scope, src, Some(m_value), false,
                    );
                }
            }
        }
    }

    fn energy_cost_delta_mult(&self, loc: Loc) -> f64 {
        self.state.players[loc.0].team[loc.1]
            .modifiers
            .get("energy_cost_delta_mult")
            .copied()
            .unwrap_or(1.0)
    }

    fn apply_damage(&mut self, m: &Mutation) {
        let Mutation::Damage { target, amount, .. } = m else { return };
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            eprintln!("[rust dmg] target={target} amount={amount}");
        }
        let loc = self.target_loc(target);
        let actual = self.sprite_mut(loc).take_damage(*amount);

        if target != "sprite_self" {
            let self_loc = Loc(self.self_player(), self.self_idx);
            let mut drain_pct = self.state.players[self_loc.0].team[self_loc.1]
                .modifiers
                .get("life_drain")
                .copied()
                .unwrap_or(0.0);
            if let Some(sid) = self.self_skill_id {
                let bs_drain = self
                    .find_skill(sid)
                    .map(|(spi, ssi, spos)| {
                        self.state.players[spi].team[ssi].skills[spos]
                            .modifiers
                            .get("life_drain")
                            .copied()
                            .unwrap_or(0.0)
                    })
                    .unwrap_or(0.0);
                drain_pct = drain_pct.max(bs_drain);
            }
            if drain_pct > 0.0 {
                let healed = crate::damage::py_round(actual as f64 * drain_pct);
                self.sprite_mut(self_loc).heal(healed);
            }
        }
        let _ = loc;
    }

    fn apply_heal(&mut self, m: &Mutation) {
        let Mutation::Heal { target, amount } = m else { return };
        let loc = self.target_loc(target);
        self.sprite_mut(loc).heal(*amount);
    }

    fn apply_energy_change(&mut self, m: &Mutation) {
        let Mutation::EnergyChange { target, delta } = m else { return };
        let loc = self.target_loc(target);
        let sprite = self.sprite_mut(loc);
        // py：记录【实际】增量（受能量上限/下限截断），供
        // fire_mutation_events 的 ctx.energy_delta_self 使用
        let actual = if *delta > 0 {
            sprite.gain_energy(*delta)
        } else {
            sprite.lose_energy(-*delta)
        };
        self.energy_actuals.push(actual);
    }

    fn apply_mark_change(&mut self, m: &Mutation) {
        let Mutation::MarkChange { target_team, name, delta, action, ratio, source_abnormal } = m
        else {
            return;
        };
        let self_loc = Loc(self.self_player(), self.self_idx);
        match action.as_str() {
            "apply" => {
                let team = if target_team == "own" { self.team } else { self.other_team_key() };
                let category = statics::classify_mark(name).to_string();
                let coexist = self.mark_coexist(self_loc);
                self.apply_mark_to(team, name, &category, *delta, coexist);
            }
            "dispel" => {
                let team = if target_team == "own" { self.team } else { self.other_team_key() };
                let count = if *delta != 0 { *delta } else { 1 };
                if let Some(marks) = self.state.globals.mark_effects.get_mut(team) {
                    if let Some(mark) = marks.iter_mut().find(|mk| mk.name == *name && mk.stacks > 0) {
                        let removed = mark.stacks.min(count);
                        mark.stacks -= removed;
                    }
                    marks.retain(|mk| mk.stacks > 0);
                }
            }
            "steal" => {
                let opp_team = self.other_team_key();
                let team = if target_team == "own" { self.team } else { opp_team };
                let from_team = if team == self.team { opp_team } else { self.team };
                let count = if *delta != 0 { *delta } else { 1 };
                let mut moved: Option<(String, i64, String)> = None;
                if let Some(marks) = self.state.globals.mark_effects.get_mut(from_team) {
                    if let Some(mark) = marks.iter_mut().find(|mk| mk.name == *name && mk.stacks > 0) {
                        let removed = mark.stacks.min(count);
                        mark.stacks -= removed;
                        moved = Some((mark.name.clone(), removed, mark.category.clone()));
                    }
                    if let Some(ms) = self.state.globals.mark_effects.get_mut(from_team) {
                        ms.retain(|mk| mk.stacks > 0);
                    }
                }
                if !name.is_empty() {
                    if let Some((mname, removed, category)) = moved {
                        let coexist = self.mark_coexist(self_loc);
                        self.apply_mark_to(team, &mname, &category, removed, coexist);
                    }
                }
            }
            "convert" => {
                let Some(source_name) = source_abnormal else { return };
                let total_stacks: i64 = self.state.players[self_loc.0].team[self_loc.1]
                    .active_effects
                    .iter()
                    .filter(|e| e.is_abnormal() && e.name == *source_name)
                    .map(|e| e.stacks())
                    .sum();
                if total_stacks <= 0 {
                    return;
                }
                let marks = ((total_stacks as f64 * *ratio) as i64).max(1);
                let mut consumed = if *ratio > 0.0 { (marks as f64 / *ratio) as i64 } else { total_stacks };
                let sprite = self.sprite_mut(self_loc);
                for e in sprite.active_effects.iter_mut() {
                    if e.is_abnormal() && e.name == *source_name {
                        if let EffectKind::Abnormal { stacks, .. } = &mut e.kind {
                            let remove = (*stacks).min(consumed);
                            *stacks -= remove;
                            consumed -= remove;
                        }
                        if consumed <= 0 {
                            break;
                        }
                    }
                }
                let team = if target_team == "own" { self.team } else { self.other_team_key() };
                let category = statics::classify_mark(name).to_string();
                let coexist = self.mark_coexist(self_loc);
                self.apply_mark_to(team, name, &category, marks, coexist);
            }
            _ => {}
        }
    }

    fn apply_mark_to(&mut self, team: &str, name: &str, category: &str, stacks: i64, coexist: bool) {
        let me_list = self.state.globals.mark_effects.entry(team.to_string()).or_default();
        if let Some(existing) = me_list.iter_mut().find(|e| e.name == name) {
            existing.stacks += stacks;
            return;
        }
        let new_mark = match statics::mark_template(name) {
            Some(mut tpl) => {
                tpl.stacks = stacks;
                tpl
            }
            None => Mark {
                name: name.to_string(),
                source: "skill".into(),
                scope: "persistent".into(),
                stacks,
                category: category.to_string(),
                ..Mark::default()
            },
        };
        if !coexist {
            me_list.retain(|e| e.category != category);
        }
        me_list.push(new_mark);
    }

    fn apply_transform(&mut self, m: &Mutation) {
        let Mutation::Transform { species, skills, reset_hp, reset_energy } = m else {
            return;
        };
        let loc = Loc(self.self_player(), self.self_idx);
        // py：lookup_species(name)；查不到则用当前物种数值套新名
        let new_species = match crate::species_db::get(species, "") {
            Some(entry) => entry.to_species(),
            None => {
                let cur = &self.state.players[loc.0].team[loc.1].species;
                let mut sp = cur.clone();
                sp.name = species.clone();
                sp
            }
        };
        let new_skills: Vec<BattleSkill> = skills
            .as_ref()
            .map(|names| crate::data::skills::build_skills(names))
            .unwrap_or_default();
        let sprite = self.sprite_mut(loc);
        if *reset_hp {
            sprite.current_hp = sprite.max_hp;
        }
        if *reset_energy {
            let me = sprite.max_energy();
            sprite.energy = me;
            let _ = me;
        }
        sprite.transform(new_species, new_skills);
    }

    fn apply_abnormal_change(&mut self, m: &Mutation) {
        let Mutation::AbnormalChange { target, name, delta, scope } = m else { return };
        let loc = self.target_loc(target);
        // py replayer 844-846：萌化（delta>0）走 apply_moe 形态退化
        // （不经过免疫门与普通 abnormal 叠加）
        if name == "萌化" && *delta > 0 {
            let removed = {
                let sprite = self.sprite_mut(loc);
                sprite.apply_moe(*delta);
                sprite.moe_position
            };
            let _ = removed;
            return;
        }
        if *delta > 0 && self.check_immune(loc, "immune_abnormal", name) {
            return;
        }
        self.sync_abnormal_effect(loc, name, *delta, scope);
    }

    fn sync_abnormal_effect(&mut self, loc: Loc, name: &str, delta: i64, scope: &str) {
        let sprite = self.sprite_mut(loc);
        let existing = sprite
            .active_effects
            .iter_mut()
            .find(|e| e.is_abnormal() && e.name == name);
        if let Some(existing) = existing {
            if let EffectKind::Abnormal { stacks, .. } = &mut existing.kind {
                *stacks += delta;
            }
            if existing.stacks() <= 0 {
                sprite.active_effects.retain(|e| !(e.is_abnormal() && e.name == name));
            }
            return;
        }
        if delta <= 0 {
            return;
        }
        let new_effect = match statics::abnormal_template(name) {
            Some(mut tpl) => {
                if !scope.is_empty() {
                    tpl.scope = scope.to_string();
                }
                if let EffectKind::Abnormal { stacks, tick_per_stack, .. } = &mut tpl.kind {
                    *stacks = delta;
                    // py replayer.py:886-896 从模板构造时【没有】拷贝 tick_per_stack
                    // ——实际生效的是 dataclass 默认 True（寄生模板是 False，
                    // 但回合末 tick 仍按层数算，spec_0149 小皮球 44 vs 22 即此）。
                    // 必须复刻这个 quirk，不能"修正"它。
                    *tick_per_stack = true;
                }
                tpl
            }
            None => Effect {
                name: name.to_string(),
                source: "skill".into(),
                scope: scope.to_string(),
                ttl: 0,
                cooldown: 0,
                kind: EffectKind::Abnormal {
                    stacks: delta,
                    tick_damage_pct: 0.0,
                    tick_element: String::new(),
                    decay_on_tick: false,
                    max_stacks: 0,
                    tick_per_stack: true,
                },
            },
        };
        sprite.active_effects.push(new_effect);
    }

    fn apply_dispel(&mut self, m: &Mutation) {
        let Mutation::Dispel { target, what, name, limit, source, .. } = m else { return };
        let loc = self.target_loc(target);
        match what.as_str() {
            "positive" => {
                if let Some(src) = source {
                    dispel_by_source(self.sprite_mut(loc), src, true);
                } else {
                    let n = limit.unwrap_or(-1);
                    self.sprite_mut(loc).dispel_positive(n);
                }
            }
            "negative" => {
                if let Some(src) = source {
                    dispel_by_source(self.sprite_mut(loc), src, false);
                } else {
                    let n = limit.unwrap_or(-1);
                    self.sprite_mut(loc).dispel_negative(n);
                }
            }
            "abnormal" => {
                if let Some(src) = source {
                    let sprite = self.sprite_mut(loc);
                    sprite.active_effects.retain(|e| !(e.source == *src && e.is_abnormal()));
                } else {
                    let nm = name.clone().unwrap_or_default();
                    // py replayer 931-938：驱散萌化异常 → 解除形态退化
                    if nm == "萌化" && self.sprite_mut(loc).moe_position > 0 {
                        let pos = self.sprite_mut(loc).moe_position;
                        self.sprite_mut(loc).remove_moe(pos);
                    } else {
                        self.sprite_mut(loc).remove_effect(&nm, "abnormal");
                    }
                }
            }
            "mark" => {
                let team = if target == "team_own" { self.team } else { self.other_team_key() };
                let count = limit.unwrap_or(1);
                if let Some(nm) = name {
                    if let Some(marks) = self.state.globals.mark_effects.get_mut(team) {
                        if let Some(mark) = marks.iter_mut().find(|mk| mk.name == *nm && mk.stacks > 0) {
                            let removed = mark.stacks.min(count);
                            mark.stacks -= removed;
                        }
                        marks.retain(|mk| mk.stacks > 0);
                    }
                } else if let Some(marks) = self.state.globals.mark_effects.get_mut(team) {
                    let available: Vec<usize> = marks
                        .iter()
                        .enumerate()
                        .filter(|(_, mk)| mk.stacks > 0)
                        .map(|(i, _)| i)
                        .collect();
                    if !available.is_empty() {
                        let idx = *self.rng.choice(&available);
                        let mark = &mut marks[idx];
                        let removed = mark.stacks.min(count);
                        mark.stacks -= removed;
                    }
                    marks.retain(|mk| mk.stacks > 0);
                }
            }
            _ => {}
        }
    }

    fn apply_steal(&mut self, m: &Mutation) {
        let Mutation::Steal { from_target, what, name, amount, action } = m else { return };
        match what.as_str() {
            "positive" => {
                let from_loc = self.target_loc(from_target);
                let self_loc = Loc(self.self_player(), self.self_idx);
                let positives: Vec<Effect> = self.state.players[from_loc.0].team[from_loc.1]
                    .active_effects
                    .iter()
                    .filter(|e| e.is_stat_buff() && e.steps() > 0)
                    .cloned()
                    .collect();
                if action == "copy" {
                    let sprite = self.sprite_mut(self_loc);
                    for e in positives {
                        sprite.add_effect(e);
                    }
                } else {
                    self.state.players[from_loc.0].team[from_loc.1]
                        .active_effects
                        .retain(|e| !(e.is_stat_buff() && e.steps() > 0));
                    let sprite = self.sprite_mut(self_loc);
                    for e in positives {
                        sprite.add_effect(e);
                    }
                }
            }
            "energy" => {
                let amount = amount.unwrap_or(0);
                if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                    let sl = Loc(self.self_player(), self.self_idx);
                    let fl = self.target_loc(from_target);
                    eprintln!(
                        "[rust steal] from_target={from_target} amount={amount} swapped={} self=({},{})e={} from=({},{})e={}",
                        self.swapped,
                        sl.0,
                        sl.1,
                        self.state.players[sl.0].team[sl.1].energy,
                        fl.0,
                        fl.1,
                        self.state.players[fl.0].team[fl.1].energy,
                    );
                }
                if from_target == "team_opp" {
                    let opp_p = self.opp_player();
                    let mut total_stolen = 0;
                    for s in self.state.players[opp_p].team.iter_mut() {
                        if s.energy <= 0 {
                            continue;
                        }
                        let s_stolen = s.energy.min(amount);
                        s.lose_energy(s_stolen);
                        total_stolen += s_stolen;
                    }
                    let self_loc = Loc(self.self_player(), self.self_idx);
                    self.sprite_mut(self_loc).gain_energy(total_stolen);
                } else {
                    let from_loc = self.target_loc(from_target);
                    let stolen = self.state.players[from_loc.0].team[from_loc.1].energy.min(amount);
                    self.state.players[from_loc.0].team[from_loc.1].lose_energy(stolen);
                    let self_loc = Loc(self.self_player(), self.self_idx);
                    self.sprite_mut(self_loc).gain_energy(stolen);
                }
            }
            "mark" => {
                let from_team = if from_target == "team_own" { "A" } else { "B" };
                let to_team = self.team;
                if let Some(nm) = name {
                    let found = self.state.globals.mark_effects.get(from_team).and_then(|ms| {
                        ms.iter().find(|mk| mk.name == *nm && mk.stacks > 0).map(|mk| {
                            (mk.stacks, mk.category.clone())
                        })
                    });
                    if let Some((stacks, category)) = found {
                        if let Some(ms) = self.state.globals.mark_effects.get_mut(from_team) {
                            ms.retain(|mk| !(mk.name == *nm && mk.stacks <= 0));
                            ms.retain(|mk| mk.stacks > 0);
                        }
                        let coexist = self.mark_coexist(Loc(self.self_player(), self.self_idx));
                        self.apply_mark_to(to_team, nm, &category, stacks, coexist);
                    }
                }
            }
            _ => {}
        }
    }

    fn apply_tick(&mut self, m: &Mutation) {
        let Mutation::Tick { target, abnormal_name } = m else { return };
        let loc = self.target_loc(target);
        let sprite = self.sprite_mut(loc);
        let stacks = sprite.get_stacks(abnormal_name);
        if stacks <= 0 {
            return;
        }
        let mut dmg_pct = 0.03f64;
        let mut tick_element = String::new();
        let mut tick_per_stack = true;
        for e in &sprite.active_effects {
            if e.is_abnormal() && e.name == *abnormal_name {
                if let EffectKind::Abnormal {
                    tick_damage_pct: p,
                    tick_element: el,
                    tick_per_stack: ps,
                    ..
                } = &e.kind
                {
                    if *p != 0.0 {
                        dmg_pct = *p;
                    }
                    tick_element = el.clone();
                    tick_per_stack = *ps;
                    break;
                }
            }
        }
        let raw = if tick_per_stack {
            crate::damage::py_round(sprite.max_hp as f64 * dmg_pct * stacks as f64).max(1)
        } else {
            crate::damage::py_round(sprite.max_hp as f64 * dmg_pct).max(1)
        };
        let attrs = sprite.species.elements();
        let mult = statics::element_mult(&tick_element, &attrs);
        let dmg = crate::damage::py_round(raw as f64 * mult).max(1);
        sprite.take_damage(dmg);
    }

    fn apply_double(&mut self, m: &Mutation) {
        let Mutation::Double { target, what, name } = m else { return };
        let loc = self.target_loc(target);
        match what.as_str() {
            "positive" => {
                self.sprite_mut(loc).double_positive();
            }
            "negative" => {
                self.sprite_mut(loc).double_negative();
            }
            "abnormal" => {
                let nm = name.clone().unwrap_or_default();
                let sprite = self.sprite_mut(loc);
                let stacks = sprite.get_stacks(&nm);
                if stacks > 0 {
                    sprite.update_stacks(&nm, stacks * 2);
                }
            }
            _ => {}
        }
    }

    /// Exchange：HP 比例 / 效果 / 技能 / 相邻技能换位。
    fn apply_exchange(&mut self, what: &str) {
        let a = Loc(self.self_player(), self.self_idx);
        let b = Loc(self.opp_player(), self.opp_idx);
        match what {
            "hp_ratio" => {
                let (a_hp, a_max) = {
                    let sa = self.sprite_mut(a);
                    (sa.current_hp, sa.max_hp)
                };
                let (b_hp, b_max) = {
                    let sb = self.sprite_mut(b);
                    (sb.current_hp, sb.max_hp)
                };
                let new_a = if b_max != 0 {
                    crate::damage::py_round(b_hp as f64 / b_max as f64 * a_max as f64)
                } else {
                    0
                };
                let new_b = if a_max != 0 {
                    crate::damage::py_round(a_hp as f64 / a_max as f64 * b_max as f64)
                } else {
                    0
                };
                self.sprite_mut(a).current_hp = new_a;
                self.sprite_mut(b).current_hp = new_b;
            }
            "effects" => {
                let ea = self.sprite_mut(a).active_effects.clone();
                let eb = self.sprite_mut(b).active_effects.clone();
                self.sprite_mut(a).active_effects = eb;
                self.sprite_mut(b).active_effects = ea;
            }
            "skills" => {
                let sa = self.sprite_mut(a).skills.clone();
                let sb = self.sprite_mut(b).skills.clone();
                self.sprite_mut(a).skills = sb;
                self.sprite_mut(b).skills = sa;
            }
            "adjacent_skills" => {
                let sprite = self.sprite_mut(a);
                let n = sprite.skills.len();
                let mut i = 0;
                while i + 1 < n {
                    if i % 2 == 0 {
                        sprite.skills.swap(i, i + 1);
                    }
                    i += 2;
                }
            }
            _ => {}
        }
    }

    fn apply_effect_delta(&mut self, m: &Mutation) {        let Mutation::EffectDelta { target, what, delta } = m else { return };
        let loc = self.target_loc(target);
        let sprite = self.sprite_mut(loc);
        for e in sprite.active_effects.iter_mut() {
            match &mut e.kind {
                EffectKind::Abnormal { stacks, max_stacks, .. } if what == "negative" => {
                    let mut new_stacks = *stacks + delta;
                    if *max_stacks != 0 && new_stacks > *max_stacks {
                        new_stacks = *max_stacks;
                    }
                    *stacks = new_stacks;
                }
                EffectKind::StatBuff { stat_key, steps, .. } => {
                    let direction = if stat_key == "energy_cost" {
                        if what == "negative" && *steps > 0 {
                            1
                        } else if what == "positive" && *steps < 0 {
                            -1
                        } else {
                            0
                        }
                    } else if what == "positive" && *steps > 0 {
                        1
                    } else if what == "negative" && *steps < 0 {
                        -1
                    } else {
                        0
                    };
                    *steps += direction * delta;
                }
                _ => {}
            }
        }
        if what == "positive" {
            for key in ["combo", "power", "priority"] {
                let val = sprite.modifiers.get(key).copied().unwrap_or(0.0);
                if val > 0.0 {
                    sprite.modifiers.insert(key.to_string(), val + *delta as f64);
                }
            }
            for key in ["energy_cost"] {
                let val = sprite.modifiers.get(key).copied().unwrap_or(0.0);
                if val < 0.0 {
                    sprite.modifiers.insert(key.to_string(), val - *delta as f64);
                }
            }
        }
    }

    fn apply_burst_grant(&mut self, m: &Mutation) {
        let Mutation::BurstGrant { target, skill_where, skill_filter, effects, .. } = m else {
            return;
        };
        let loc = self.target_loc(target);
        let sprite = self.sprite_mut(loc);
        for bs in sprite.skills.iter_mut() {
            if let Some(sw) = skill_where {
                let mut info = BTreeMap::new();
                info.insert("name".into(), Val::S(bs.name()));
                info.insert("energy_cost".into(), Val::I(bs.energy_cost()));
                info.insert("element".into(), Val::S(bs.base.element.clone()));
                info.insert("skill_type".into(), Val::S(bs.base.skill_type.clone()));
                if !eval_skill_where(Some(sw), &info) {
                    continue;
                }
            }
            if let Some(f) = skill_filter {
                if f != "all" && !matches_skill_type(f, &bs.base.skill_type) {
                    continue;
                }
            }
            bs.burst_effects.extend(effects.iter().cloned());
            bs.modifiers
                .insert("burst".into(), if !bs.burst_effects.is_empty() { 1.0 } else { 0.0 });
        }
    }

    fn apply_trait_interaction(&mut self, m: &Mutation) {
        let Mutation::TraitInteraction { action, target, copy_from, new_ability } = m else {
            return;
        };
        let loc = if matches!(target.as_str(), "sprite_self" | "self") {
            Loc(self.self_player(), self.self_idx)
        } else {
            Loc(self.opp_player(), self.opp_idx)
        };
        match action.as_str() {
            "suppress" => {
                self.sprite_mut(loc).trait_suppressed = true;
            }
            "remove" => {
                let sprite = self.sprite_mut(loc);
                sprite.trait_suppressed = true;
                if let Some(ab) = new_ability {
                    sprite.species.ability = ab.clone();
                    sprite.species.ability_id = 0;
                    sprite.trait_suppressed = false;
                }
            }
            "copy" => {
                let from_loc = if copy_from.as_deref() == Some("sprite_opp") {
                    Loc(self.opp_player(), self.opp_idx)
                } else {
                    Loc(self.self_player(), self.self_idx)
                };
                if from_loc == loc {
                    return;
                }
                let (ability, ability_id) = {
                    let src = self.sprite_mut(from_loc);
                    (src.species.ability.clone(), src.species.ability_id)
                };
                if ability.is_empty() && ability_id == 0 {
                    return;
                }
                let sprite = self.sprite_mut(loc);
                sprite.species.ability = ability;
                sprite.species.ability_id = ability_id;
                sprite.trait_suppressed = false;
            }
            _ => {}
        }
    }
}

fn mode_apply(cur: f64, delta: f64, mode: &str) -> f64 {
    match mode {
        "add" => cur + delta,
        "set" => delta,
        "multiply" => {
            if cur != 0.0 {
                cur * delta
            } else {
                delta
            }
        }
        _ => delta,
    }
}

/// _modifiers 写入（add 模式 cur 缺省：damage_reduction/life_drain=0.0，
/// ratio=1.0，其他=0.0；multiply cur None → value）。
fn apply_mode_with_defaults(mods: &mut BTreeMap<String, f64>, stat: &str, value: f64, mode: &str) {
    let cur = mods.get(stat).copied();
    let nv = match mode {
        "set" => value,
        "add" => {
            let base = cur.unwrap_or_else(|| {
                if stat == "damage_reduction" || stat == "life_drain" {
                    0.0
                } else if is_ratio_stat(stat) {
                    1.0
                } else {
                    0.0
                }
            });
            base + value
        }
        "multiply" => {
            if let Some(c) = cur {
                c * value
            } else {
                value
            }
        }
        _ => value,
    };
    mods.insert(stat.to_string(), nv);
}

fn modifier_apply(cur: Option<f64>, value: f64, mode: &str, stat: &str) -> f64 {
    match mode {
        "set" => value,
        "add" => {
            let base = cur.unwrap_or_else(|| {
                if stat == "damage_reduction" || stat == "life_drain" {
                    0.0
                } else if is_ratio_stat(stat) {
                    1.0
                } else {
                    0.0
                }
            });
            base + value
        }
        "multiply" => {
            if let Some(c) = cur {
                c * value
            } else {
                value
            }
        }
        _ => value,
    }
}

fn sync_state_effect(sprite: &mut Sprite, state_type: &str) {
    sprite.active_effects.retain(
        |e| !(e.is_state() && matches!(&e.kind, EffectKind::State { state_type: st, .. } if st == state_type)),
    );
    sprite.active_effects.push(Effect {
        name: state_type.to_string(),
        source: "skill".into(),
        scope: "turn".into(),
        ttl: 0,
        cooldown: 0,
        kind: EffectKind::State {
            state_type: state_type.to_string(),
            params: J::Null,
        },
    });
}

fn dispel_by_source(sprite: &mut Sprite, source: &str, positive_only: bool) {
    sprite.active_effects.retain(|e| {
        !(e.is_stat_buff()
            && e.source == source
            && ((positive_only && e.steps() > 0) || (!positive_only && e.steps() < 0)))
    });
}

/// _sync_stat_buff_effect：按 (stat_key, scope) 合并或新建 StatBuffEffect。
/// mode="set" 时替换 steps，否则累加（py 默认 "add"）。
pub fn sync_stat_buff_effect(
    sprite: &mut Sprite,
    stat_key: &str,
    steps: i64,
    scope: &str,
    source: &str,
    is_inherent: bool,
    mode: &str,
) {
    if let Some(existing) = sprite.active_effects.iter_mut().find(|e| {
        e.is_stat_buff()
            && matches!(&e.kind, EffectKind::StatBuff { stat_key: k, .. } if k == stat_key)
            && e.scope == scope
    }) {
        if let EffectKind::StatBuff { steps: es, is_inherent: inh, .. } = &mut existing.kind {
            if mode == "set" {
                *es = steps;
            } else {
                *es += steps;
            }
            if is_inherent && !*inh {
                *inh = true;
            }
        }
        return;
    }
    sprite.active_effects.push(Effect {
        name: stat_key.to_string(),
        source: source.to_string(),
        scope: scope.to_string(),
        ttl: 0,
        cooldown: 0,
        kind: EffectKind::StatBuff {
            stat_key: stat_key.to_string(),
            steps,
            display_mult: None,
            display_value: None,
            is_inherent,
        },
    });
}

// ── display-only 效果（replayer.py _sync_mult_display_effect /
//    _apply_to_matching_skills 尾部）──

/// vm/effect.py _STEP_UNITS（步进单位）。
fn step_unit(stat: &str) -> i64 {
    match stat {
        "power" | "speed" | "life_drain" => 10,
        _ => 1,
    }
}

/// vm/effect.py _STAT_LABELS（效果显示名）。
fn stat_label(stat: &str) -> String {
    match stat {
        "atk" => "物攻", "sp_atk" => "魔攻", "def" => "物防", "sp_def" => "魔防",
        "speed" => "速度", "power" => "威力", "priority" => "先手",
        "energy_cost" => "能耗", "combo" => "连击", "life_drain" => "吸血",
        "power_mod" => "威力", "power_mult" => "威力倍率", "damage_mult" => "伤害倍率",
        "damage_reduction" => "减伤", "energy_cost_mult" => "能耗倍率",
        "heal_reverse" => "回复反转", "ignore_resistance" => "无视抗性",
        "ignore_mods" => "无视修正", "survive" => "不屈", "combo_set" => "连击固定",
        "swift" => "迅捷", "drive" => "传动",
        other => return other.to_string(),
    }
    .to_string()
}

/// _sync_mult_display_effect：按 (stat_key, source, steps==0) 合并或新建
/// 纯显示 StatBuff（mult_value 进 display_mult，display_value 携带绝对值；
/// additive=true 时累加而非替换——累计式 stat_stage 触发用）。
fn sync_mult_display_effect(
    sprite: &mut Sprite,
    stat_key: &str,
    mult_value: f64,
    scope: &str,
    source: &str,
    display_value: Option<f64>,
    additive: bool,
) {
    if let Some(existing) = sprite.active_effects.iter_mut().find(|e| {
        e.is_stat_buff()
            && matches!(&e.kind, EffectKind::StatBuff { stat_key: k, steps: 0, .. } if k == stat_key)
            && e.source == source
    }) {
        if let EffectKind::StatBuff { display_mult: dm, display_value: dv, .. } = &mut existing.kind {
            if additive {
                *dm = Some(dm.unwrap_or(0.0) + mult_value);
                if let Some(d) = display_value {
                    *dv = Some(dv.unwrap_or(0.0) + d);
                }
            } else {
                *dm = Some(mult_value);
                if let Some(d) = display_value {
                    *dv = Some(d);
                }
            }
        }
        existing.scope = scope.to_string();
        return;
    }
    sprite.active_effects.push(Effect {
        name: stat_label(stat_key),
        source: source.to_string(),
        scope: scope.to_string(),
        ttl: 0,
        cooldown: 0,
        kind: EffectKind::StatBuff {
            stat_key: stat_key.to_string(),
            steps: 0,
            display_mult: Some(mult_value),
            display_value,
            is_inherent: false,
        },
    });
}

/// _apply_to_matching_skills 尾部的显示效果：energy_cost/power_mod/
/// 比率型/普通值各有分支；已有同 (stat_key, source, steps==0) 效果时
/// 更新 display 字段与 scope（ttl 取 max）。
fn sync_matching_display_effect(
    sprite: &mut Sprite,
    stat_key: &str,
    scope: &str,
    source: &str,
    display_value: Option<f64>,
    display_mult: Option<f64>,
    ttl: i64,
) {
    if let Some(existing) = sprite.active_effects.iter_mut().find(|e| {
        e.is_stat_buff()
            && matches!(&e.kind, EffectKind::StatBuff { stat_key: k, steps: 0, .. } if k == stat_key)
            && e.source == source
    }) {
        if let EffectKind::StatBuff { display_mult: dm, display_value: dv, .. } = &mut existing.kind {
            if display_mult.is_some() {
                *dm = display_mult;
            }
            if let Some(d) = display_value {
                *dv = Some(d);
            }
        }
        existing.scope = scope.to_string();
        if ttl > 0 {
            existing.ttl = existing.ttl.max(ttl);
        }
        return;
    }
    sprite.active_effects.push(Effect {
        name: stat_label(stat_key),
        source: source.to_string(),
        scope: scope.to_string(),
        ttl,
        cooldown: 0,
        kind: EffectKind::StatBuff {
            stat_key: stat_key.to_string(),
            steps: 0,
            display_mult,
            display_value,
            is_inherent: false,
        },
    });
}

// BattleSkill 引用保持（后续 execute_skill 桥使用）
#[allow(unused)]
fn _keep(_bs: &BattleSkill) {}
