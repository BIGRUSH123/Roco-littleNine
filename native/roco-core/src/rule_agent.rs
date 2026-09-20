//! rule_agent — RuleAgent 移植（backend/sim/agent.py），确定性规则 AI。
//! PlayStyle 默认 balanced（aggression 0.5 / switch_hp_threshold 0.3 /
//! gather_energy_threshold 2）。

use crate::battle_state::BattleState;
use crate::damage::calc_damage;
use crate::statics;

pub struct Action {
    pub kind: &'static str, // "skill" | "gather" | "switch" | "item"
    pub skill_index: Option<usize>,
    pub switch_index: Option<usize>,
}

const AGGRESSION: f64 = 0.5;
const SWITCH_HP_THRESHOLD: f64 = 0.3;
const GATHER_ENERGY_THRESHOLD: i64 = 2;

const ELEMENTAL_BLOODLINES: &[&str] = &[
    "普通", "火", "水", "草", "电", "冰", "地", "石", "武", "虫", "翼", "萌",
    "毒", "幽", "恶", "幻", "光", "龙", "机械",
];

pub struct RuleAgent {
    pub team: &'static str,
}

/// 力竭换人策略：引擎在精灵倒下时询问该策略选谁上场。
///
/// - 对局驱动（RuleAgent）：按伤害评估选人（规则版）。
/// - MCTS 仿真：网络策略头（py `_choose_policy_replacement` 等价物）；
///   非网络对手时 py 走 `alive[0]` 兜底（`_PlayerSwappedAgent`/`_OppFixedAgent`）。
pub trait ReplacementPolicy {
    fn choose_replacement(&mut self, state: &BattleState, team: &str) -> i64;
}

impl ReplacementPolicy for RuleAgent {
    fn choose_replacement(&mut self, state: &BattleState, _team: &str) -> i64 {
        RuleAgent::choose_replacement(self, state)
    }
}

/// A/B 各自规则 AI 的组合策略（execute_turn 的双方各自 RuleAgent）。
pub struct RuleReplPair<'a> {
    pub a: &'a RuleAgent,
    pub b: &'a RuleAgent,
}

impl ReplacementPolicy for RuleReplPair<'_> {
    fn choose_replacement(&mut self, state: &BattleState, team: &str) -> i64 {
        if team == "A" {
            RuleAgent::choose_replacement(self.a, state)
        } else {
            RuleAgent::choose_replacement(self.b, state)
        }
    }
}

/// 首只存活兜底（py `alive[0]` / `find_replacement` 语义，无替补返回 -1）。
pub struct FirstAliveRepl;

impl ReplacementPolicy for FirstAliveRepl {
    fn choose_replacement(&mut self, state: &BattleState, team: &str) -> i64 {
        let pi = pi(team);
        let p = &state.players[pi];
        p.team
            .iter()
            .enumerate()
            .find(|(i, s)| !s.is_fainted() && *i != p.active_index)
            .map(|(i, _)| i as i64)
            .unwrap_or(-1)
    }
}

fn pi(team: &str) -> usize {
    if team == "A" { 0 } else { 1 }
}

/// resolver.calc_damage 的规则评估版（SkillUse.modifiers 为空）。
fn estimate_damage(
    state: &BattleState,
    atk_pi: usize,
    atk_si: usize,
    skill_i: usize,
    def_pi: usize,
    def_si: usize,
) -> i64 {
    let attacker = &state.players[atk_pi].team[atk_si];
    let defender = &state.players[def_pi].team[def_si];
    let Some(bs) = attacker.skills.get(skill_i) else { return 0 };
    if !bs.is_attack() {
        return 0;
    }
    // get_atk_def_keys
    let st = bs.skill_type();
    let (atk_key, def_key) = match st.as_str() {
        "物攻" => ("atk", "def"),
        "魔攻" => ("sp_atk", "sp_def"),
        "动态攻击" => {
            if attacker.effective_stat("atk") >= attacker.effective_stat("sp_atk") {
                ("atk", "def")
            } else {
                ("sp_atk", "sp_def")
            }
        }
        _ => return 0,
    };
    let atk_base = attacker.initial_stats.get(atk_key).copied().unwrap_or(0);
    let def_base = defender.initial_stats.get(def_key).copied().unwrap_or(0);
    if atk_base <= 0 || def_base <= 0 {
        return 0;
    }
    let atk_steps = attacker.sum_steps(atk_key);
    let def_steps = defender.sum_steps(def_key);
    let atk_stage = atk_steps as f64 / 10.0;
    let def_stage = def_steps as f64 / 10.0;

    let power_mod: i64 = attacker.sum_steps("power");
    let mut additive_power = power_mod * 10;
    // 印记威力加成（正向 + is_attack 条件）
    if let Some(marks) = state.globals.mark_effects.get(if atk_pi == 0 { "A" } else { "B" }) {
        for mk in marks {
            if mk.is_positive() && mk.power_bonus != 0 {
                if mk.condition == "is_attack" && !bs.is_attack() {
                    continue;
                }
                additive_power += mk.power_bonus * mk.stacks;
            }
        }
    }

    let elem = bs.element();
    let def_elems = defender.species.elements();
    let type_mult = if elem.is_empty() || def_elems.is_empty() {
        1.0
    } else {
        statics::element_mult(&elem, &def_elems)
    };
    let attrs = attacker.species.elements();
    let stab = if !elem.is_empty() && attrs.iter().any(|a| a == &elem) { 1.25 } else { 1.0 };
    let weather_mult = if state.globals.weather == "rain" && elem.contains('水') {
        1.5
    } else {
        1.0
    };

    let mark_bonus = mark_bonus_of(state, if atk_pi == 0 { "A" } else { "B" }, false).0;
    calc_damage(
        bs.power(),
        atk_base,
        def_base,
        atk_stage,
        def_stage,
        stab,
        type_mult,
        weather_mult,
        0.0, // damage_reduction（use.modifiers 空）
        1.0,
        1.0,
        additive_power,
        1.0,
        1, // multi_hit
        mark_bonus,
    )
}

// 印记伤害倍率 bonus（mark_damage_mult - 1.0）
fn mark_bonus_of(state: &BattleState, team: &str, is_first: bool) -> (f64, ()) {
    let mut mult = 1.0f64;
    if let Some(marks) = state.globals.mark_effects.get(team) {
        for mk in marks {
            if mk.damage_mult == 0.0 {
                continue;
            }
            let cond = mk.condition.as_str();
            if cond.is_empty()
                || (cond == "is_first" && is_first)
                || (cond == "not_first" && !is_first)
            {
                mult += mk.damage_mult * mk.stacks as f64;
            }
        }
    }
    (mult - 1.0, ())
}

impl RuleAgent {
    pub fn new(team: &'static str) -> Self {
        RuleAgent { team }
    }

    fn opp_pi(&self) -> usize {
        if self.team == "A" { 1 } else { 0 }
    }

    pub fn choose_lead(&self, state: &BattleState) -> usize {
        let p = &state.players[pi(self.team)];
        let opp_pi = self.opp_pi();
        // py：opponent = battle.get_opponent(team).active（对手 active，非 index 0）
        let opp_ai = state.players[opp_pi].active_index;
        let mut best_idx = 0usize;
        let mut best_score = -1.0f64;
        for (i, sprite) in p.team.iter().enumerate() {
            if sprite.is_fainted() {
                continue;
            }
            let mut max_dmg = 0i64;
            for sk_i in 0..sprite.skills.len() {
                let dmg = estimate_damage(state, pi(self.team), i, sk_i, opp_pi, opp_ai);
                max_dmg = max_dmg.max(dmg);
            }
            let score = max_dmg as f64 + sprite.current_hp as f64 * 0.05;
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                let nm = sprite.name();
                let opp_ai = state.players[opp_pi].active_index;
                eprintln!(
                    "[rust lead] team={} cand[{}]={} hp={} max_dmg={} score={:.1} (opp_active={})",
                    self.team, i, nm, sprite.current_hp, max_dmg, score, opp_ai,
                );
            }
            if score > best_score {
                best_score = score;
                best_idx = i;
            }
        }
        best_idx
    }

    pub fn choose_replacement(&self, state: &BattleState) -> i64 {
        let p = &state.players[pi(self.team)];
        let opp_pi = self.opp_pi();
        let alive: Vec<usize> = p
            .team
            .iter()
            .enumerate()
            .filter(|(i, s)| !s.is_fainted() && *i != p.active_index)
            .map(|(i, _)| i)
            .collect();
        let mut best_idx = -1i64;
        let mut best_score = -1.0f64;
        let opp_ai = state.players[opp_pi].active_index;
        for &idx in &alive {
            let sprite = &p.team[idx];
            let mut max_dmg = 0i64;
            for sk_i in 0..sprite.skills.len() {
                let dmg = estimate_damage(state, pi(self.team), idx, sk_i, opp_pi, opp_ai);
                max_dmg = max_dmg.max(dmg);
            }
            let score = max_dmg as f64 + sprite.current_hp as f64 * 0.1;
            if score > best_score {
                best_score = score;
                best_idx = idx as i64;
            }
        }
        if best_idx < 0 && !alive.is_empty() {
            return alive[0] as i64;
        }
        best_idx
    }

    pub fn choose_action(&self, state: &BattleState) -> Action {
        let p = &state.players[pi(self.team)];
        let s = p.active();

        // 已力竭 → 强制换宠
        if s.is_fainted() {
            return match p.find_replacement() {
                Some(r) => Action { kind: "switch", skill_index: None, switch_index: Some(r) },
                None => Action { kind: "gather", skill_index: None, switch_index: None },
            };
        }

        let turn = state.turn;
        // 道具使用
        if let Some(item) = &p.item {
            if item.can_use(turn) {
                if item.name == "进化之力" && turn <= 2 && s.bloodline == "首领" {
                    return Action { kind: "item", skill_index: None, switch_index: None };
                }
                if item.name == "愿力" {
                    let hp_ratio = if s.max_hp > 0 { s.current_hp as f64 / s.max_hp as f64 } else { 0.0 };
                    if hp_ratio < 0.5 && AGGRESSION > 0.4 && ELEMENTAL_BLOODLINES.contains(&s.bloodline.as_str()) {
                        return Action { kind: "item", skill_index: None, switch_index: None };
                    }
                }
            }
        }

        // 低 HP → 可能换宠
        let hp_ratio = if s.max_hp > 0 { s.current_hp as f64 / s.max_hp as f64 } else { 0.0 };
        if hp_ratio < SWITCH_HP_THRESHOLD {
            if let Some(r) = p.find_replacement() {
                return Action { kind: "switch", skill_index: None, switch_index: Some(r) };
            }
        }

        // 低能量 → 聚能（蓄力中除外）
        let has_charging = s.charging;
        if s.energy <= GATHER_ENERGY_THRESHOLD && !has_charging {
            return Action { kind: "gather", skill_index: None, switch_index: None };
        }

        // 蓄力中：强制释放蓄力技能
        if has_charging {
            // 自由蓄力特性（游弋/嫉妒）不做 trait 查询——Rust 侧按名字
            let free_charge = matches!(s.species.ability.as_str(), "游弋" | "嫉妒");
            let charged_idx = s.charged_skill_ref.and_then(|sid| {
                s.skills.iter().position(|bs| bs.skill_id == sid)
            });
            match charged_idx {
                Some(i) if !free_charge => {
                    let skill_ok = {
                        let bs = &s.skills[i];
                        !(bs.sealed || bs.replaced_by.is_some() || bs.is_temporary)
                    };
                    if skill_ok {
                        return Action { kind: "skill", skill_index: Some(i), switch_index: None };
                    }
                    // 蓄力技能不可用 → 换宠/聚能
                    return match p.find_replacement() {
                        Some(r) => Action { kind: "switch", skill_index: None, switch_index: Some(r) },
                        None => Action { kind: "gather", skill_index: None, switch_index: None },
                    };
                }
                Some(_) => { /* free_charge：任选 */ }
                None => {
                    return match p.find_replacement() {
                        Some(r) => Action { kind: "switch", skill_index: None, switch_index: Some(r) },
                        None => Action { kind: "gather", skill_index: None, switch_index: None },
                    };
                }
            }
        }

        let opp_pi = self.opp_pi();
        let opp_si = state.players[opp_pi].active_index;

        // 评分所有技能
        let mut best_idx: i64 = -1;
        let mut best_score = -1.0f64;
        for (i, skill) in s.skills.iter().enumerate() {
            if skill.cooldown > 0 || skill.sealed {
                continue;
            }
            if skill.energy_cost() > s.energy {
                continue;
            }
            let score = if skill.is_attack() {
                let dmg = estimate_damage(state, pi(self.team), p.active_index, i, opp_pi, opp_si);
                dmg as f64 * AGGRESSION
            } else {
                // defense/status：effects 为空（RISC 技能）→ 得分 0
                0.0
            };
            if score > best_score {
                best_score = score;
                best_idx = i as i64;
            }
        }

        if best_idx >= 0 {
            return Action { kind: "skill", skill_index: Some(best_idx as usize), switch_index: None };
        }

        if s.energy < 10 {
            return Action { kind: "gather", skill_index: None, switch_index: None };
        }
        match p.find_replacement() {
            Some(r) => Action { kind: "switch", skill_index: None, switch_index: Some(r) },
            None => Action { kind: "gather", skill_index: None, switch_index: None },
        }
    }
}
