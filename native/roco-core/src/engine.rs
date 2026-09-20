//! engine — BattleVMEngine 等价物（移植自 backend/engine/battle.py）。
//! execute_skill 17 步管线 + 观察者触发 + journal 三变换 + trait 加载。

use crate::battle_state::{BattleState, BattleSkill, Sprite, TEAM_A};
use crate::journal::{IrPayload, Mutation};
use crate::observers::{Observer, ObserverRegistry};
use crate::replayer::{eval_skill_where, Replayer};
use crate::resolve::Val;
use crate::rng::PyRandom;
use crate::snapshot::{build_ctx, BuildCtxOpts};
use crate::statics;
use crate::vm_ctx::Ctx;
use crate::vm_exec;
use serde_json::Value as J;
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

const SINGLE_OWNER_TRIGGERS: &[&str] = &[
    "post_entry", "post_leave", "post_skill", "turn_end", "post_abnormal_tick",
    "turn_start", "post_energy_change", "post_counter", "post_enemy_leave",
    "post_charge", "post_heal",
];

/// 执行一次技能所需的定位与事件参数。
pub struct ExecParams {
    pub team: &'static str,
    pub self_idx: usize,
    pub opp_idx: usize,
    pub self_skill_idx: usize,
    pub opp_skill_idx: Option<usize>,
    pub effects: Vec<J>,
    pub turn: i64,
    pub is_first: bool,
    pub opp_switched: bool,
    pub was_countered: bool,
    pub counter_succeeded: bool,
    pub skill_index: i64,
    pub devotion_triggered: bool,
    pub damage_taken_this_turn: i64,
    pub prev_skill_type: String,
    pub prev_damage_taken_self: bool,
    pub prev_damage_taken_opp: bool,
    pub skill_name: String,
    /// 技能 JSON 头部（InjectHitPass 镜像需要 skill_type/power/element/combo）
    pub skill: Option<J>,
}

#[derive(Clone)]
pub struct VmEngine {
    pub registry: ObserverRegistry,
    pub data_dir: PathBuf,
    /// post_enemy_leave 期间指向离场精灵（py leaving_sprite；source=sprite_opp
    /// 的 inherit 等效果需要）。调用方在 fire_trigger 前设置并在之后清除。
    pub leaving_loc: Option<(usize, usize)>,
}

impl VmEngine {
    pub fn new(data_dir: PathBuf) -> Self {
        VmEngine { registry: ObserverRegistry::new(), data_dir, leaving_loc: None }
    }

    /// B 视角搜索支持：py 交换 player_a/player_b 后观察者以精灵对象 id()
    /// 为 owner（交换天然免疫）；rust owner 是 (team, team_idx) 静态标签，
    /// 随视图交换会失配。搜索前调用翻转全部 owner 队伍标签，搜索结束后
    /// 再调用一次对称翻回（搜索期间新注册的观察者同样被对称还原）。
    pub fn flip_owner_teams(&mut self) {
        for o in self.registry.observers.iter_mut() {
            if let Some((t, idx)) = o.owner {
                o.owner = Some((
                    match t {
                        "A" => "B",
                        "B" => "A",
                        other => other,
                    },
                    idx,
                ));
            }
        }
    }

    fn rebuild_ctx(
        state: &BattleState,
        eng: &VmEngine,
        p: &ExecParams,
        ctx: &Ctx,
    ) -> Ctx {
        let _ = (state, eng, p, ctx);
        unreachable!()
    }

    /// py 的 ctx.stat_stages_* 是精灵活缓存引用（_extract_sprite_effects
    /// O(1) 路径）——ctx 构建后的阶段/异常变化对后续读取可见。
    /// rust 为纯快照，在 py 读取点之前用当前状态刷新以镜像。
    fn refresh_live_effects(
        state: &BattleState,
        team: &'static str,
        self_idx: usize,
        opp_idx: usize,
        ctx: &mut Ctx,
    ) {
        let own_pi = if team == "A" { 0 } else { 1 };
        let opp_pi = 1 - own_pi;
        let (stages_s, abn_s, _, _, _) =
            crate::snapshot::sprite_effects_summary(&state.players[own_pi].team[self_idx]);
        let (stages_o, abn_o, _, _, _) =
            crate::snapshot::sprite_effects_summary(&state.players[opp_pi].team[opp_idx]);
        ctx.stat_stages_self = stages_s;
        ctx.stat_stages_opp = stages_o;
        ctx.abnormal_stacks_self = abn_s;
        ctx.abnormal_stacks_opp = abn_o;
        ctx.abnormal_stacks_battle = {
            let mut m = ctx.abnormal_stacks_self.clone();
            for (k, v) in &ctx.abnormal_stacks_opp {
                *m.entry(k.clone()).or_insert(0) += v;
            }
            m
        };
    }

    /// ── 17 步 execute_skill 管线 ──
    pub fn execute_skill(
        &mut self,
        state: &mut BattleState,
        rng: &mut PyRandom,
        p: &ExecParams,
    ) -> Vec<Mutation> {
        let own_pi = if p.team == "A" { 0 } else { 1 };
        let opp_pi = 1 - own_pi;

        // 1. build_ctx
        let mut opts = BuildCtxOpts {
            team: p.team,
            self_idx: p.self_idx,
            opp_idx: p.opp_idx,
            self_skill_idx: Some(p.self_skill_idx),
            opp_skill_idx: p.opp_skill_idx,
            turn: p.turn,
            is_first: p.is_first,
            damage_taken_this_turn: p.damage_taken_this_turn,
            skill_index: p.skill_index,
            prev_skill_type: p.prev_skill_type.clone(),
            prev_damage_taken_self: p.prev_damage_taken_self,
            prev_damage_taken_opp: p.prev_damage_taken_opp,
            was_countered: p.was_countered,
            counter_succeeded: p.counter_succeeded,
            opp_switched: p.opp_switched,
            burst_triggered_count_own: state
                .burst_names
                .get(p.team)
                .map(|s| s.len() as i64)
                .unwrap_or(0),
            ..Default::default()
        };
        let mut ctx = build_ctx(state, &opts);

        // 2. pre_calc（攻方+守方）→ 回放 → 重建 ctx
        let opp_team = crate::battle_state::BattleState::opponent_of(p.team);
        let mut pre_calc_mods = self.fire_pre_event(state, "pre_calc", &ctx, p.team, p.self_idx);
        pre_calc_mods.extend(self.fire_pre_event(state, "pre_calc", &ctx, opp_team, p.opp_idx));
        if !pre_calc_mods.is_empty() {
            // py：pre_calc 用【无 self_skill】的 replayer 回放 →
            // skill_off_0 落到精灵级 modifiers（skill_at_N 不受影响）
            let mut r = Replayer::new(state, p.team, p.self_idx, p.opp_idx, None, rng);
            r.replay(&pre_calc_mods);
            ctx = build_ctx(state, &opts);
        }

        // 3. pre_modifier
        let pre_mods = self.fire_pre_event(state, "pre_modifier", &ctx, p.team, p.self_idx);

        // 4. VM 执行（排序 + 隐含 hit 注入）
        let mut journal = vm_exec::execute_with_skill_header(&ctx, &p.effects, p.skill.as_ref());
        // py 的 vm_effects 为【编译后】效果（含隐式 hit）——登记/历史同源
        let vm_effects = vm_exec::augment_with_header(&p.effects, p.skill.as_ref());

        // 5. pre_mods 前插
        if !pre_mods.is_empty() {
            let mut all = pre_mods;
            all.extend(journal);
            journal = all;
        }

        // 6. pre_defend（守方）
        let pre_defend = self.fire_pre_event(state, "pre_defend", &ctx, opp_team, p.opp_idx);
        if !pre_defend.is_empty() {
            let mut all = pre_defend;
            all.extend(journal);
            journal = all;
        }

        // 7. burst 登记
        if p.is_first && !vm_effects.is_empty() {
            state
                .burst_effects
                .entry(p.team.to_string())
                .or_default()
                .push((p.skill_name.clone(), vm_effects.clone()));
            state
                .burst_names
                .entry(p.team.to_string())
                .or_default()
                .insert(p.skill_name.clone());
        }

        // 8-10. journal 三变换
        journal = self.handle_replay(state, &ctx, journal, p.team, p.self_idx);
        journal = Self::handle_borrow(&ctx, journal);
        journal = Self::handle_redirect(journal);

        // 11. 同技能修正调整（modifiers.py）
        let debug = std::env::var("ROCO_DEBUG_DMG").is_ok();
        if debug {
            for m in &journal {
                if let Mutation::ModifierInjection {
                    stat, value, scope, target, source: src, skill_filter: flt, ..
                } = m {
                    eprintln!("[rust pre-adjust mod] stat={stat} value={value:?} scope={scope} target={target} source={src:?} filter={flt:?}");
                }
            }
        }
        journal = adjust_damage_in_journal(journal, &ctx);

        // 12. 回放
        let debug = std::env::var("ROCO_DEBUG_DMG").is_ok();
        if debug {
            eprintln!("[rust main replay] team={} self_idx={}", p.team, p.self_idx);
        }
        let mut r = Replayer::new(state, p.team, p.self_idx, p.opp_idx, Some(p.self_skill_idx), rng);
        r.replay(&journal);
        // py：_apply_energy_change 记录【实际】能量增量（受上限截断），
        // _fire_mutation_events 用它设置 ctx.energy_delta_self
        let energy_actuals = std::mem::take(&mut r.energy_actuals);
        drop(r);
        // py：ctx 的阶段/异常表为活引用——主回放后刷新（post_skill/
        // post_damage 等读取时可见回放产生的变化）
        Self::refresh_live_effects(state, p.team, p.self_idx, p.opp_idx, &mut ctx);

        // 13. 计数器注册
        if debug {
            let n_cr = journal
                .iter()
                .filter(|m| matches!(m, Mutation::CounterRegister { .. }))
                .count();
            eprintln!(
                "[rust journal] team={} self={} skill_name_len={} len={} counters={}",
                p.team,
                p.self_idx,
                p.skill_name.chars().count(),
                journal.len(),
                n_cr
            );
        }
        self.register_counters_from_journal(state, &journal, p.team, p.self_idx, p.self_skill_idx);

        // 14. 技能历史
        if !p.skill_name.is_empty() {
            let sid = state.players[own_pi].team[p.self_idx].skills
                .get(p.self_skill_idx)
                .map(|b| b.skill_id)
                .unwrap_or(0);
            state
                .skill_history
                .entry(sid)
                .or_default()
                .push((p.skill_name.clone(), vm_effects.clone(), J::Null));
        }

        // 15. 星陨印记（非幻攻击）
        let is_attack = bs_is_attack(state, own_pi, p.self_idx, p.self_skill_idx);
        let skill_element = ss_element(state, own_pi, p.self_idx, p.self_skill_idx);
        if is_attack && skill_element != "幻" {
            let opp_team = if p.team == "A" { "B" } else { "A" };
            trigger_starfall(state, opp_team, p.opp_idx, p.self_idx, p.self_skill_idx);
        }

        // 16. post_skill（just_acted_self）
        ctx.just_acted_self = true;
        let _ = &mut opts;
        self.fire_post_event(state, "post_skill", &ctx, p.team, p.self_idx, p.opp_idx, Some(p.self_skill_idx), rng);

        // 17. mutation 驱动事件
        self.fire_mutation_events(state, &ctx, &journal, p.team, p.self_idx, p.opp_idx, Some(p.self_skill_idx), &energy_actuals, rng);

        journal
    }

    // ── pre 事件 ──

    pub fn fire_pre_event(
        &mut self,
        state: &mut BattleState,
        trigger: &str,
        ctx: &Ctx,
        team: &'static str,
        sprite_idx: usize,
    ) -> Vec<Mutation> {
        let _ = state;
        let mut mutations = Vec::new();
        // py _fire_pre_event：{ownerless ∪ owned-by-(team, sprite_idx)}，
        // 有 owner 的观察者只为其所有者触发（如 血型吸引 只应在自己出手时生效）
        let candidates: Vec<(Option<(&'static str, usize)>, J, Vec<J>)> = self
            .registry
            .candidates(trigger, None)
            .into_iter()
            .map(|o| (o.owner, o.cond.clone(), o.then.clone()))
            .collect();
        for (obs_owner, cond, then) in candidates {
            if let Some(own) = obs_owner {
                if own != (team, sprite_idx) {
                    continue;
                }
            }
            let ev = crate::cond::eval_one(ctx, &cond);
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                let owner_s = obs_owner
                    .map(|(t, i)| format!("({},{})", t, i))
                    .unwrap_or_default();
                eprintln!(
                    "[rust pre-fire] trig={trigger} owner={owner_s} fire_team={} idx={} cond={:?} -> {:?}",
                    team, sprite_idx, cond, ev,
                );
            }
            if ev.unwrap_or(false) {
                mutations.extend(vm_exec::process_effects(ctx, &then));
            }
        }
        mutations
    }

    // ── post 事件 ──

    pub fn fire_post_event(
        &mut self,
        state: &mut BattleState,
        trigger: &str,
        ctx: &Ctx,
        team: &'static str,
        self_idx: usize,
        opp_idx: usize,
        self_skill_idx: Option<usize>,
        rng: &mut PyRandom,
    ) {
        let owner: Option<(&'static str, usize)> = Some((team, self_idx));
        let candidates: Vec<Observer> = self
            .registry
            .candidates(trigger, None)
            .into_iter()
            .cloned()
            .collect();
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            let total = self.registry.observers.len();
            let post = self
                .registry
                .observers
                .iter()
                .filter(|o| o.listen.iter().any(|t| t == trigger))
                .count();
            eprintln!(
                "[rust candidates] trigger={trigger} total={total} matching={post} cand={}",
                candidates.len()
            );
            if trigger == "post_entry" {
                for o in self.registry.observers.iter() {
                    eprintln!(
                        "[rust registry] owner={:?} src_len={} listen={:?} scope={}",
                        o.owner,
                        o.source.chars().count(),
                        o.listen,
                        o.scope
                    );
                }
            }
        }
        // 技能实例 id（视角交换后仍需指向原 acting 精灵的技能：
        // py 的 replayer._self_skill 是对象引用）
        let acting_skill_id: Option<u64> = {
            let pi = if team == "A" { 0 } else { 1 };
            self_skill_idx
                .and_then(|i| state.players[pi].team[self_idx].skills.get(i))
                .map(|b| b.skill_id)
        };
        for mut obs in candidates {
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                let dmg45 = obs.then.iter().any(|e| {
                    e.get("op").and_then(|x| x.as_str()) == Some("power_mod")
                        && e.get("delta").and_then(|x| x.as_i64()) == Some(45)
                });
                if dmg45 || trigger == "post_enemy_leave" || trigger == "post_entry"
                    || obs.source == "珊瑚木" || obs.source == "共鸣" {
                    eprintln!(
                        "[rust fire] trigger={} owner={:?} self=({}, {}) cond={} cand={}",
                        trigger,
                        obs.owner,
                        team,
                        self_idx,
                        obs.cond,
                        obs.eval_cond(ctx).is_ok_and(|b| b),
                    );
                }
            }
            // owner 过滤
            if let Some(own) = obs.owner {
                if trigger == "post_ko" {
                    if own != (team, self_idx) && own != (crate::battle_state::BattleState::opponent_of(team), opp_idx) {
                        continue;
                    }
                } else if SINGLE_OWNER_TRIGGERS.contains(&trigger) {
                    if own != (team, self_idx) {
                        continue;
                    }
                }
            }
            // post_damage/post_ko 且 owner 为防守方 → 视角交换
            let swap = matches!(trigger, "post_damage" | "post_ko")
                && obs.owner.is_some()
                && obs.owner.unwrap() == (crate::battle_state::BattleState::opponent_of(team), opp_idx);
            let eff_ctx;
            let (r_team, r_self, r_opp, r_swapped) = if swap {
                eff_ctx = ctx.swapped_view();
                // py：仅交换 self/opp 精灵，team 保持原 acting 方
                (team, opp_idx, self_idx, true)
            } else {
                eff_ctx = ctx.clone();
                (team, self_idx, opp_idx, false)
            };
            let mut eff_ctx = eff_ctx;
            if swap {
                // py battle.py 380-384：交换后翻转 damage_taken_of
                if eff_ctx.event.damage_taken_of == "sprite_opp" {
                    eff_ctx.event.damage_taken_of = "sprite_self".into();
                } else if eff_ctx.event.damage_taken_of == "sprite_self" {
                    eff_ctx.event.damage_taken_of = "sprite_opp".into();
                }
            }
            let cond_ok = obs.eval_cond(&eff_ctx).unwrap_or(false);
            if cond_ok {
                if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                    eprintln!(
                        "[rust fire replay] trigger={} r_team={} r_self={} obs_source={}",
                        trigger, r_team, r_self, obs.source
                    );
                }
                let journal = vm_exec::process_effects(&eff_ctx, &obs.then);
                let mut r = Replayer::new_with_skill_id(
                    state, r_team, r_self, r_opp, acting_skill_id, rng,
                );
                r.swapped = r_swapped;
                r.leaving_loc =
                    self.leaving_loc.map(|(pi, si)| crate::replayer::Loc(pi, si));
                r.replay(&journal);
                obs.hit_count += 1;
                if state.pending_escape.is_some() {
                    break;
                }
            }
        }
    }

    // ── mutation 驱动事件 ──

    pub fn fire_mutation_events(
        &mut self,
        state: &mut BattleState,
        ctx: &Ctx,
        journal: &[Mutation],
        team: &'static str,
        self_idx: usize,
        opp_idx: usize,
        self_skill_idx: Option<usize>,
        energy_actuals: &[i64],
        rng: &mut PyRandom,
    ) {
        let mut fired: BTreeSet<String> = BTreeSet::new();
        let mut n_energy = 0usize;
        let mut ctx = ctx.clone();
        for m in journal {
            // py 活引用语义：每次触发前刷新阶段/异常表
            Self::refresh_live_effects(state, team, self_idx, opp_idx, &mut ctx);
            let mut trigger: Option<String> = None;
            match m {
                Mutation::Damage { target, amount, .. } => {
                    trigger = Some("post_damage".into());
                    ctx.damage_taken_this_turn = *amount;
                    ctx.event.damage_taken_of = if target == "sprite_self" {
                        "sprite_self".into()
                    } else {
                        "sprite_opp".into()
                    };
                }
                Mutation::EnergyChange { target, delta } => {
                    let target_of = if target == "sprite_self" { "sprite_self" } else { "sprite_opp" };
                    ctx.event.energy_changed_of = target_of.into();
                    ctx.event.skills_energy_changed_of = target_of.into();
                    // py：energy_delta 用【实际】增量（受能量上限截断）
                    let actual = energy_actuals.get(n_energy).copied().unwrap_or(*delta);
                    n_energy += 1;
                    ctx.energy_delta_self = if target_of == "sprite_self" { actual } else { 0 };
                    trigger = Some("post_energy_change".into());
                }
                Mutation::Heal { target, amount } => {
                    let target_of = if target == "sprite_self" { "sprite_self" } else { "sprite_opp" };
                    ctx.event.heal_of = target_of.into();
                    if target_of == "sprite_self" {
                        ctx.heal_delta_self = *amount;
                    } else {
                        ctx.heal_delta_opp = *amount;
                    }
                    trigger = Some("post_heal".into());
                }
                Mutation::AbnormalChange { name, target, delta, .. } => {
                    trigger = Some("post_abnormal_change".into());
                    ctx.event.abnormal_changed_name = name.clone();
                    ctx.event.abnormal_changed_target = if target == "sprite_self" {
                        "sprite_self".into()
                    } else {
                        "sprite_opp".into()
                    };
                    if *delta > 0 && !fired.contains("post_abnormal_apply") {
                        fired.insert("post_abnormal_apply".into());
                        ctx.event.abnormal_applied_name = name.clone();
                        ctx.event.abnormal_applied_target = ctx.event.abnormal_changed_target.clone();
                        self.fire_post_event(state, "post_abnormal_apply", &ctx, team, self_idx, opp_idx, self_skill_idx, rng);
                    }
                }
                Mutation::StatChange { .. } => {} // replayer 产的 StatChange is_positive 恒 False
                Mutation::ModifierInjection { target, mode, value, stat, .. } => {
                    let vnum = value.as_num().unwrap_or(0.0);
                    let positive = match mode.as_str() {
                        "add" => {
                            if stat == "energy_cost" {
                                vnum < 0.0
                            } else {
                                vnum > 0.0
                            }
                        }
                        "multiply" => vnum > 1.0,
                        _ => false,
                    };
                    if positive {
                        trigger = Some("post_positive_change".into());
                        ctx.event.positive_changed_of = if target == "sprite_self" {
                            "sprite_self".into()
                        } else {
                            "sprite_opp".into()
                        };
                    }
                }
                _ => {}
            }
            if let Some(t) = trigger {
                if !fired.contains(&t) {
                    fired.insert(t.clone());
                    self.fire_post_event(state, &t, &ctx, team, self_idx, opp_idx, self_skill_idx, rng);
                }
            }
        }
        // post_ko
        for m in journal {
            if let Mutation::Damage { target, .. } = m {
                let fainted = if target == "sprite_opp" {
                    state.players[if team == "A" { 1 } else { 0 }].team[opp_idx].is_fainted()
                } else {
                    state.players[if team == "A" { 0 } else { 1 }].team[self_idx].is_fainted()
                };
                if fainted {
                    if !fired.contains("post_ko") {
                        fired.insert("post_ko".into());
                        ctx.event.target_fainted = true;
                        self.fire_post_event(state, "post_ko", &ctx, team, self_idx, opp_idx, self_skill_idx, rng);
                    }
                    break;
                }
            }
        }
    }

    // ── journal 三变换 ──

    pub fn handle_replay(
        &mut self,
        state: &mut BattleState,
        ctx: &Ctx,
        journal: Vec<Mutation>,
        team: &'static str,
        self_sprite_skill_id: usize,
    ) -> Vec<Mutation> {
        let has_replay = journal
            .iter()
            .any(|m| matches!(m, Mutation::Replay { .. }));
        if !has_replay {
            return journal;
        }
        let mut extra: Vec<Mutation> = Vec::new();
        let mut kept: Vec<Mutation> = Vec::new();
        for m in journal {
            match m {
                Mutation::Replay { from_, skill_filter } => {
                    if from_ == "team_burst" {
                        if let Some(bursts) = state.burst_effects.get(team) {
                            for (_name, effects) in bursts {
                                extra.extend(vm_exec::execute(ctx, effects));
                            }
                        }
                    } else if from_ == "sprite_self" {
                        let own_pi = if team == "A" { 0 } else { 1 };
                        let sid = state.players[own_pi].team[self_sprite_skill_id]
                            .skills
                            .first()
                            .map(|b| b.skill_id)
                            .unwrap_or(0);
                        if let Some(history) = state.skill_history.get(&sid) {
                            for (_n, effects, _tags) in history {
                                extra.extend(vm_exec::execute(ctx, effects));
                            }
                        }
                        let _ = skill_filter;
                    }
                }
                other => kept.push(other),
            }
        }
        if extra.is_empty() {
            return kept;
        }
        let mut all = extra;
        all.extend(kept);
        all
    }

    pub fn handle_borrow(ctx: &Ctx, journal: Vec<Mutation>) -> Vec<Mutation> {
        let has_borrow = journal.iter().any(|m| matches!(m, Mutation::Borrow { .. }));
        if !has_borrow {
            return journal;
        }
        let mut out: Vec<Mutation> = Vec::new();
        for m in journal {
            match m {
                Mutation::Borrow { .. } => {
                    // 借用：以对方技能属性注入隐含 hit
                    let (btype, bpower, belement) = (
                        ctx.skill_type_opp.clone(),
                        ctx.power_opp,
                        ctx.element_opp.clone(),
                    );
                    if matches!(btype.as_str(), "物攻" | "魔攻" | "动态攻击") && bpower > 0 {
                        let (atk_base, def_base, atk_stage, def_stage) = if btype == "物攻" {
                            (
                                ctx.atk_self,
                                ctx.def_opp,
                                ctx.stat_stages_self.get("atk").copied().unwrap_or(0) as f64 * 0.1,
                                ctx.stat_stages_opp.get("def").copied().unwrap_or(0) as f64 * 0.1,
                            )
                        } else {
                            (
                                ctx.sp_atk_self,
                                ctx.sp_def_opp,
                                ctx.stat_stages_self.get("sp_atk").copied().unwrap_or(0) as f64 * 0.1,
                                ctx.stat_stages_opp.get("sp_def").copied().unwrap_or(0) as f64 * 0.1,
                            )
                        };
                        let amount = crate::damage::calc_damage(
                            bpower, atk_base, def_base, atk_stage, def_stage,
                            1.0, 1.0, 1.0, ctx.damage_reduction_opp, 1.0, 1.0, 0, 1.0,
                            ctx.combo_self, 0.0,
                        );
                        out.push(Mutation::Damage {
                            target: "sprite_opp".into(),
                            amount,
                            element: belement,
                            attack_type: btype,
                        });
                    }
                }
                other => out.push(other),
            }
        }
        out
    }

    pub fn handle_redirect(journal: Vec<Mutation>) -> Vec<Mutation> {
        let redirect_target = journal.iter().find_map(|m| match m {
            Mutation::Redirect { target } => Some(target.clone()),
            _ => None,
        });
        match redirect_target {
            None => journal,
            Some(rt) => {
                let mut out = Vec::new();
                for m in journal {
                    match m {
                        Mutation::Redirect { .. } => {}
                        Mutation::Damage { target, amount, element, attack_type }
                            if target != rt =>
                        {
                            out.push(Mutation::Damage {
                                target: rt.clone(),
                                amount,
                                element,
                                attack_type,
                            });
                        }
                        other => out.push(other),
                    }
                }
                out
            }
        }
    }

    // ── 计数器注册 ──

    pub fn register_counters_from_journal(
        &mut self,
        state: &mut BattleState,
        journal: &[Mutation],
        team: &'static str,
        self_idx: usize,
        self_skill_idx: usize,
    ) {
        for m in journal {
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                if let Mutation::CounterRegister { cond, then, scope, .. } = m {
                    eprintln!(
                        "[rust counter-seen] team={team} self={self_idx} cond={cond:?} scope={scope} then_len={}",
                        then.len()
                    );
                }
            }
            if let Mutation::CounterRegister {
                name,
                cond,
                then,
                scope,
                listen,
                threshold,
                reset_on_fire,
            } = m
            {
                let own_pi = if team == "A" { 0 } else { 1 };
                let skill_id = state.players[own_pi].team[self_idx]
                    .skills
                    .get(self_skill_idx)
                    .map(|b| b.skill_id);
                let mut triggers: Vec<String> = listen.clone().unwrap_or_default();
                if triggers.is_empty() {
                    if let Some(c) = cond {
                        let s = crate::cond::infer_triggers(c);
                        triggers = s.into_iter().collect();
                    }
                }
                // 去重：烘焙后的 then + cond + owner 相同则跳过
                let cond_val = cond.clone().unwrap_or(J::Null);
                let baked_then = crate::observers::prepare_then(then, "counter", scope, &triggers);
                let dup = self.registry.observers.iter().any(|o| {
                    o.owner == Some((team, self_idx))
                        && o.cond == cond_val
                        && o.then == baked_then
                        && o.owner_skill_id == skill_id
                });
                if dup {
                    if let Some(n) = name {
                        if !n.is_empty() {
                            state.counter_values.entry(n.clone()).or_insert(0);
                        }
                    }
                    continue;
                }
                let debug = std::env::var("ROCO_DEBUG_DMG").is_ok();
                let triggers_dbg = triggers.clone();
                self.registry.register(Observer {
                    cond: cond_val,
                    then: baked_then,
                    scope: scope.clone(),
                    name: name.clone().unwrap_or_default(),
                    source: "counter".into(),
                    listen: triggers,
                    threshold: *threshold,
                    reset_on_fire: *reset_on_fire,
                    owner: Some((team, self_idx)),
                    owner_skill_id: skill_id,
                    hit_count: 0,
                    source_baked: true,
                });
                if debug {
                    let src_name = state.players[own_pi].team[self_idx]
                        .skills
                        .get(self_skill_idx)
                        .map(|b| b.name())
                        .unwrap_or_default();
                    eprintln!(
                        "[rust counter-reg] owner=({team}, {self_idx}) skill_len={} listen={triggers_dbg:?} scope={scope}",
                        src_name.chars().count(),
                    );
                }
                if let Some(n) = name {
                    if !n.is_empty() {
                        state.counter_values.entry(n.clone()).or_insert(0);
                    }
                }
            }
        }
    }

    // ── 外部触发钩子（turn_start/post_entry/post_leave/ko/counter 等）──

    #[allow(clippy::too_many_arguments)]
    pub fn fire_trigger(
        &mut self,
        trigger: &str,
        state: &mut BattleState,
        ctx: &Ctx,
        team: &'static str,
        self_idx: usize,
        opp_idx: usize,
        self_skill_idx: Option<usize>,
        rng: &mut PyRandom,
    ) {
        if !self.registry.has_candidates(trigger) {
            return;
        }
        self.fire_post_event(state, trigger, ctx, team, self_idx, opp_idx, self_skill_idx, rng);
    }
}

fn opp_team_key(team: &str) -> &'static str {
    if team == "A" { "B" } else { "A" }
}

fn bs_is_attack(state: &BattleState, pi: usize, si: usize, skill_i: usize) -> bool {
    state.players[pi].team[si].skills
        .get(skill_i)
        .map(|b| b.is_attack())
        .unwrap_or(false)
}

fn ss_element(state: &BattleState, pi: usize, si: usize, skill_i: usize) -> String {
    state.players[pi].team[si].skills
        .get(skill_i)
        .map(|b| b.element())
        .unwrap_or_default()
}

/// 星陨印记触发（globals.trigger_starfall 镜像）。
fn trigger_starfall(state: &mut BattleState, team: &str, defender_idx: usize, attacker_idx: usize, skill_i: usize) {
    let opp_pi = if team == "A" { 0 } else { 1 };
    let own_pi = 1 - opp_pi;
    // 攻防属性跟随触发技能（py：get_atk_def_keys(attacker)）
    let trigger_type = state.players[own_pi].team[attacker_idx]
        .skills
        .get(skill_i)
        .map(|b| b.skill_type())
        .unwrap_or_default();
    let attacker = &state.players[own_pi].team[attacker_idx];
    let (atk_key, def_key) = match trigger_type.as_str() {
        "物攻" => ("atk", "def"),
        "魔攻" => ("sp_atk", "sp_def"),
        "动态攻击" => {
            if attacker.effective_stat("atk") >= attacker.effective_stat("sp_atk") {
                ("atk", "def")
            } else {
                ("sp_atk", "sp_def")
            }
        }
        _ => return,
    };
    // starfall_consume_ratio 修正（py：ModifierEffect attr）
    let consume_ratio: Option<f64> = state.players[own_pi].team[attacker_idx]
        .active_effects
        .iter()
        .find_map(|e| match &e.kind {
            crate::battle_state::EffectKind::Modifier { attr, value, .. }
                if attr == "starfall_consume_ratio" =>
            {
                Some(*value)
            }
            _ => None,
        });
    let Some(marks) = state.globals.mark_effects.get_mut(team) else { return };
    let Some(pos) = marks
        .iter()
        .position(|mk| mk.name == "星陨印记" && mk.stacks > 0)
    else {
        return;
    };
    let total_stacks = marks[pos].stacks;
    let consume = match consume_ratio {
        Some(r) => 1.max((total_stacks as f64 * r) as i64),
        None => total_stacks,
    };
    let consumed = consume.min(total_stacks);
    marks[pos].stacks -= consumed;
    if marks[pos].stacks <= 0 {
        marks.remove(pos);
    }
    if consumed <= 0 {
        return;
    }
    let power = total_stacks * total_stacks + 24 * total_stacks - 24;
    if power <= 0 {
        return;
    }
    let atk = state.players[own_pi].team[attacker_idx].effective_stat(atk_key);
    let def_sprite = &state.players[opp_pi].team[defender_idx];
    let defense = def_sprite.effective_stat(def_key).max(1);
    let def_elems = def_sprite.species.elements();
    let type_mult = statics::element_mult("幻", &def_elems);
    let damage_reduction = def_sprite
        .modifiers
        .get("damage_reduction")
        .copied()
        .unwrap_or(0.0)
        .clamp(0.0, 1.0);
    if damage_reduction >= 1.0 {
        return;
    }
    let raw = crate::damage::py_round(
        power as f64 * atk as f64 / defense as f64 * (37.0 / 41.0) * type_mult
            * (1.0 - damage_reduction),
    );
    let defender = &mut state.players[opp_pi].team[defender_idx];
    defender.take_damage(raw.max(1));
}

/// modifiers.py::apply_modifiers_to_journal（同技能伤害修正）。
pub fn adjust_damage_in_journal(journal: Vec<Mutation>, ctx: &Ctx) -> Vec<Mutation> {
    let has_damage = journal.iter().any(|m| matches!(m, Mutation::Damage { .. }));
    if !has_damage {
        return journal;
    }
    let entries: Vec<(&String, &Val, &String)> = journal
        .iter()
        .filter_map(|m| match m {
            Mutation::ModifierInjection { stat, value, mode, .. } => Some((stat, value, mode)),
            _ => None,
        })
        .collect();
    let has_mod = entries.iter().any(|(s, _, _)| {
        matches!(s.as_str(), "power" | "power_mult" | "damage_mult" | "damage_reduction" | "combo")
    });
    if !has_mod && ctx.combo_mult_self <= 0.0 {
        return journal;
    }

    // Pass 1: set 基线
    let mut power_mult_base = 1.0f64;
    let mut dr_base = ctx.damage_reduction_opp;
    let mut combo_set = 0i64;
    let mut power_base: Option<f64> = None;
    for (s, v, md) in &entries {
        if md.as_str() != "set" {
            continue;
        }
        if s.as_str() == "power_mult" {
            power_mult_base = v.as_num().unwrap_or(1.0);
        } else if s.as_str() == "damage_reduction" {
            dr_base = v.as_num().unwrap_or(0.0);
        } else if s.as_str() == "combo" {
            combo_set = v.as_num().unwrap_or(0.0) as i64;
        } else if s.as_str() == "power" && v.as_num().unwrap_or(0.0) != 0.0 {
            power_base = Some(v.as_num().unwrap_or(0.0));
        }
    }
    let mut power_mult = power_mult_base;
    let mut damage_reduction = dr_base;
    let mut damage_mult = 1.0f64;
    let mut combo_add = 0i64;
    let mut power_add = 0.0f64;

    // Pass 2: add/multiply
    for (s, v, md) in &entries {
        let n = v.as_num().unwrap_or(0.0);
        match s.as_str() {
            "power_mult" => match md.as_str() {
                "add" => power_mult += n,
                m if m != "set" => power_mult *= n,
                _ => {}
            },
            "damage_mult" => damage_mult *= n,
            "damage_reduction" => match md.as_str() {
                "add" => damage_reduction = (damage_reduction + n).min(1.0),
                "multiply" => damage_reduction = 1.0 - (1.0 - damage_reduction) * (1.0 - n),
                _ => {}
            },
            "combo" if md.as_str() == "add" => combo_add += n as i64,
            "power" => match md.as_str() {
                "add" => power_add += n,
                "multiply" => power_mult *= n,
                _ => {}
            },
            _ => {}
        }
    }
    if power_add > 0.0 && ctx.power_self > 0 {
        let effective = ctx.power_self as f64 + power_add;
        power_mult *= effective / ctx.power_self as f64;
    }
    let base_dr = ctx.damage_reduction_opp;
    let dr_delta = (damage_reduction - base_dr).max(0.0);

    let combo_base = (ctx.combo_self).max(1);
    let effective_combo = if combo_set > 0 {
        (combo_set + combo_add).max(1)
    } else {
        (combo_base + combo_add).max(1)
    };
    let effective_combo = if ctx.combo_mult_self > 0.0 {
        crate::damage::py_round(effective_combo as f64 * (1.0 + ctx.combo_mult_self)).max(1)
    } else {
        effective_combo
    };

    let mut out = Vec::with_capacity(journal.len());
    for m in journal {
        match m {
            Mutation::Damage { target, amount, element, attack_type } => {
                let mut amount = crate::damage::py_round(
                    amount as f64 * power_mult * damage_mult,
                );
                if effective_combo != combo_base && combo_base > 0 {
                    amount = crate::damage::py_round(
                        amount as f64 * effective_combo as f64 / combo_base as f64,
                    );
                }
                if dr_delta > 0.0 {
                    amount = crate::damage::py_round(amount as f64 * (1.0 - dr_delta));
                }
                if amount <= 0 {
                    out.push(Mutation::Damage {
                        target: target.clone(),
                        amount: 0,
                        element: element.clone(),
                        attack_type: attack_type.clone(),
                    });
                } else {
                    out.push(Mutation::Damage {
                        target: target.clone(),
                        amount: amount.max(1),
                        element: element.clone(),
                        attack_type: attack_type.clone(),
                    });
                }
            }
            other => out.push(other),
        }
    }
    out
}
