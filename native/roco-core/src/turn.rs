//! turn — 回合管线（移植自 sim/battle.py 四阶段 + battle_mechanics.py
//! 换宠/传动 + pipeline.py 回合开始 + resolver.py 回合末）。
//!
//! headless 语义：不产事件串（事件串在阶段5 完整路径补齐）。

use crate::battle_state::{BattleSpec, BattleState, BattleSkill, Effect, EffectKind};
use crate::engine::{ExecParams, VmEngine};
use crate::journal::Mutation;
use crate::observers::Observer;
use crate::resolve::Val;
use crate::rng::PyRandom;
use crate::rule_agent::{Action, ReplacementPolicy, RuleAgent, RuleReplPair};
use crate::statics;
use crate::vm_exec;
use serde_json::Value as J;
use std::collections::BTreeMap;

/// effect_factory.from_dict：JSON 效果 → EffectObject 副本（None = 跳过）。
fn effect_object_from_factory(d: &J, source: &str) -> Option<Effect> {
    let obj = d.as_object()?;
    let op = obj.get("op").and_then(|x| x.as_str())?;
    let scope = obj
        .get("scope")
        .and_then(|x| x.as_str())
        .unwrap_or("battlefield")
        .to_string();
    let ttl = obj.get("ttl").and_then(|x| x.as_i64()).unwrap_or(0);
    let name = obj.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();

    match op {
        "observer" => Some(Effect {
            name: if name.is_empty() { source.to_string() } else { name },
            source: source.to_string(),
            scope: obj
                .get("scope")
                .and_then(|x| x.as_str())
                .unwrap_or("persistent")
                .to_string(),
            ttl,
            cooldown: 0,
            kind: EffectKind::Observer {
                cond: obj.get("cond").cloned().unwrap_or(J::Null),
                then: obj.get("then").and_then(|x| x.as_array()).cloned().unwrap_or_default(),
                listen: match obj.get("listen") {
                    Some(J::String(s)) => vec![s.clone()],
                    Some(J::Array(a)) => a.iter().filter_map(|x| x.as_str().map(String::from)).collect(),
                    _ => vec![],
                },
                threshold: obj.get("threshold").and_then(|x| x.as_i64()).unwrap_or(1),
                reset_on_fire: obj.get("reset_on_fire").and_then(|x| x.as_bool()).unwrap_or(true),
            },
        }),
        "power_mod" | "mult_mod" | "stat_stage" => {
            let attr = obj
                .get("attr")
                .and_then(|x| x.as_str())
                .or_else(|| obj.get("stat").and_then(|x| x.as_str()))
                .unwrap_or("")
                .to_string();
            let value = obj
                .get("value")
                .and_then(|x| x.as_f64())
                .or_else(|| obj.get("delta").and_then(|x| x.as_f64()))
                .or_else(|| obj.get("steps").and_then(|x| x.as_f64()))
                .unwrap_or(0.0);
            let effective_name = if attr.starts_with("immune_") {
                name
            } else if !name.is_empty() {
                name
            } else {
                format!("{source}-{attr}")
            };
            Some(Effect {
                name: effective_name,
                source: source.to_string(),
                scope,
                ttl,
                cooldown: 0,
                kind: EffectKind::Modifier {
                    target: obj
                        .get("target")
                        .and_then(|x| x.as_str())
                        .unwrap_or("sprite_self")
                        .to_string(),
                    attr,
                    value,
                    mode: obj
                        .get("mode")
                        .and_then(|x| x.as_str())
                        .unwrap_or("add")
                        .to_string(),
                    skill_where: obj.get("skill_where").cloned(),
                },
            })
        }
        "abnormal" => {
            let tname = obj.get("name").and_then(|x| x.as_str()).unwrap_or("");
            let tpl = statics::abnormal_template(tname);
            Some(match tpl {
                Some(mut e) => {
                    e.name = if name.is_empty() { e.name.clone() } else { name };
                    e.source = source.to_string();
                    if let Some(s) = obj.get("scope").and_then(|x| x.as_str()) {
                        e.scope = s.to_string();
                    }
                    e.ttl = obj.get("ttl").and_then(|x| x.as_i64()).unwrap_or(e.ttl);
                    if let EffectKind::Abnormal { stacks, tick_damage_pct, tick_element, decay_on_tick, max_stacks, tick_per_stack } = &mut e.kind {
                        *stacks = obj.get("stacks").and_then(|x| x.as_i64()).unwrap_or(0);
                        if let Some(v) = obj.get("tick_damage_pct").and_then(|x| x.as_f64()) {
                            *tick_damage_pct = v;
                        }
                        if let Some(v) = obj.get("tick_element").and_then(|x| x.as_str()) {
                            *tick_element = v.to_string();
                        }
                        if let Some(v) = obj.get("decay_on_tick").and_then(|x| x.as_bool()) {
                            *decay_on_tick = v;
                        }
                        if let Some(v) = obj.get("max_stacks").and_then(|x| x.as_i64()) {
                            *max_stacks = v;
                        }
                        if let Some(v) = obj.get("tick_per_stack").and_then(|x| x.as_bool()) {
                            *tick_per_stack = v;
                        }
                    }
                    e
                }
                None => Effect {
                    name: if name.is_empty() { source.to_string() } else { name },
                    source: source.to_string(),
                    scope,
                    ttl,
                    cooldown: 0,
                    kind: EffectKind::Abnormal {
                        stacks: obj.get("stacks").and_then(|x| x.as_i64()).unwrap_or(0),
                        tick_damage_pct: 0.0,
                        tick_element: String::new(),
                        decay_on_tick: false,
                        max_stacks: 0,
                        tick_per_stack: true,
                    },
                },
            })
        }
        "mark" => {
            let tname = obj.get("name").and_then(|x| x.as_str()).unwrap_or("");
            let tpl = statics::mark_template(tname);
            Some(match tpl {
                Some(mut t) => {
                    t.name = if name.is_empty() { t.name.clone() } else { name };
                    t.source = source.to_string();
                    if let Some(s) = obj.get("scope").and_then(|x| x.as_str()) {
                        t.scope = s.to_string();
                    }
                    t.ttl = obj.get("ttl").and_then(|x| x.as_i64()).unwrap_or(t.ttl);
                    t.stacks = obj.get("stacks").and_then(|x| x.as_i64()).unwrap_or(0);
                    if let Some(v) = obj.get("category").and_then(|x| x.as_str()) {
                        t.category = v.to_string();
                    }
                    Effect {
                        name: t.name.clone(),
                        source: t.source.clone(),
                        scope: t.scope.clone(),
                        ttl: t.ttl,
                        cooldown: 0,
                        kind: EffectKind::Mark,
                    }
                }
                None => Effect {
                    name: if name.is_empty() { source.to_string() } else { name },
                    source: source.to_string(),
                    scope,
                    ttl,
                    cooldown: 0,
                    kind: EffectKind::Mark,
                },
            })
        }
        _ => None,
    }
}

pub const MAX_TURNS: i64 = 150;

const ELEMENTAL_BLOODLINES_RUST: &[&str] = &[
    "普通", "火", "水", "草", "电", "冰", "地", "石", "武", "虫", "翼", "萌",
    "毒", "幽", "恶", "幻", "光", "龙", "机械",
];

/// 对局驱动入口：双方 RuleAgent 自博弈。
pub fn run_battle(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom) -> Option<String> {
    // 构造期入场（Python Battle.__init__：仅对 index-0 首发 dispatch，
    // 换首发不补载 —— 已确认的 Python 怪癖，必须镜像）
    dispatch_entry(state, engine, rng, "A");
    dispatch_entry(state, engine, rng, "B");
    fire_post_entry(state, engine, rng, "A", state.players[0].active_index);
    fire_post_entry(state, engine, rng, "B", state.players[1].active_index);

    let agent_a = RuleAgent::new("A");
    let agent_b = RuleAgent::new("B");
    let lead_a = agent_a.choose_lead(state);
    let lead_b = agent_b.choose_lead(state);
    state.players[0].active_index = lead_a;
    state.players[1].active_index = lead_b;

    while state.winner.is_none() && state.turn < MAX_TURNS {
        execute_turn(state, engine, rng, &agent_a, &agent_b);
    }
    state.winner.clone()
}

/// 规范化逐回合状态摘要（与 Python 门驱动器同构）。
pub fn state_digest(state: &BattleState) -> J {
    use serde_json::json;
    let sprites_of = |pi: usize| -> Vec<J> {
        state.players[pi]
            .team
            .iter()
            .map(|s| {
                json!({
                    "name": s.name(),
                    "hp": s.current_hp,
                    "max_hp": s.max_hp,
                    "energy": s.energy,
                    "entry_turn": s.entry_turn,
                    "charging": s.charging,
                    "first_action": s.first_action,
                    "effects": s.active_effects.iter().map(|e| {
                        let n = match &e.kind {
                            EffectKind::StatBuff { steps, .. } => J::from(*steps),
                            EffectKind::Abnormal { stacks, .. } => J::from(*stacks),
                            _ => J::from(0),
                        };
                        vec![J::String(e.name.clone()), J::String(e.scope.clone()), n]
                    }).collect::<Vec<_>>(),
                    "modifiers": s.modifiers,
                    "skills": s.skills.iter().map(|b| serde_json::json!({
                        "name": b.name(),
                        "cooldown": b.cooldown,
                        "sealed": b.sealed,
                        "modifiers": b.modifiers,
                    })).collect::<Vec<_>>(),
                })
            })
            .collect()
    };
    json!({
        "turn": state.turn,
        "winner": state.winner,
        "weather": state.globals.weather,
        "weather_turns": state.globals.weather_turns,
        "players": [
            {
                "lives": state.players[0].lives,
                "active_index": state.players[0].active_index,
                "devotion": state.players[0].devotion,
                "sprites": sprites_of(0),
            },
            {
                "lives": state.players[1].lives,
                "active_index": state.players[1].active_index,
                "devotion": state.players[1].devotion,
                "sprites": sprites_of(1),
            },
        ],
        "marks": {
            "A": state.globals.mark_effects.get("A").map(|ms| {
                ms.iter().map(|m| vec![J::String(m.name.clone()), J::from(m.stacks)]).collect::<Vec<_>>()
            }).unwrap_or_default(),
            "B": state.globals.mark_effects.get("B").map(|ms| {
                ms.iter().map(|m| vec![J::String(m.name.clone()), J::from(m.stacks)]).collect::<Vec<_>>()
            }).unwrap_or_default(),
        },
        "team_counters": state.team_counters,
        "counter_values": state.counter_values,
    })
}

/// 自对弈 worker 入口：构造期入场，不做 RuleAgent 选首发
/// （py _play_one_rl_battle 不选首发，active=队伍首位）。
pub fn state_from_spec_selfplay(spec: &BattleSpec) -> (BattleState, VmEngine, PyRandom) {
    let mut state = BattleState::from_spec(spec);
    let mut engine = VmEngine::new(std::path::PathBuf::from("data"));
    let mut rng = PyRandom::seed_i64(spec.seed + 1);
    dispatch_entry(&mut state, &mut engine, &mut rng, "A");
    dispatch_entry(&mut state, &mut engine, &mut rng, "B");
    let a0 = state.players[0].active_index;
    fire_post_entry(&mut state, &mut engine, &mut rng, "A", a0);
    let b0 = state.players[1].active_index;
    fire_post_entry(&mut state, &mut engine, &mut rng, "B", b0);
    (state, engine, rng)
}

/// spec → 初始对局状态（构造期入场 + 双方 RuleAgent 定首发）。
/// 种子协议：spec.seed 生成阵容；对局内 RNG = seed+1。
/// 与 py（Battle 初始化 + choose_lead）同序，供整局对拍与 MCTS 对拍复用。
pub fn state_from_spec(spec: &BattleSpec) -> (BattleState, VmEngine, PyRandom) {
    let mut state = BattleState::from_spec(spec);
    let mut engine = VmEngine::new(std::path::PathBuf::from("data"));
    let mut rng = PyRandom::seed_i64(spec.seed + 1);
    // 构造期入场（turn=0，index 0 首发）
    dispatch_entry(&mut state, &mut engine, &mut rng, "A");
    dispatch_entry(&mut state, &mut engine, &mut rng, "B");
    let a0 = state.players[0].active_index;
    fire_post_entry(&mut state, &mut engine, &mut rng, "A", a0);
    let b0 = state.players[1].active_index;
    fire_post_entry(&mut state, &mut engine, &mut rng, "B", b0);

    let agent_a = RuleAgent::new("A");
    let agent_b = RuleAgent::new("B");
    // py 顺序：A 先定首发，B 针对 A 的首发选人
    let lead_a = agent_a.choose_lead(&state);
    state.players[0].active_index = lead_a;
    let lead_b = agent_b.choose_lead(&state);
    state.players[1].active_index = lead_b;
    (state, engine, rng)
}

/// pyo3 对拍入口：从 spec 构建 + 双方 RuleAgent 完整对局 → 逐回合摘要。
pub fn run_battle_from_spec(spec: &BattleSpec) -> (Vec<J>, Option<String>) {
    let (mut state, mut engine, mut rng) = state_from_spec(spec);
    let mut digests = Vec::new();
    let agent_a = RuleAgent::new("A");
    let agent_b = RuleAgent::new("B");
    digests.push(state_digest(&state));

    while state.winner.is_none() && state.turn < MAX_TURNS {
        execute_turn(&mut state, &mut engine, &mut rng, &agent_a, &agent_b);
        digests.push(state_digest(&state));
    }
    (digests, state.winner.clone())
}

/// run_battle_from_spec 的掩码采集版：与 py 探针同点（每次 execute_turn
/// 之前 = digest[i] 状态）采集双方 17 维合法动作掩码，供阶段4 对拍。
/// run_battle_from_spec 的编码采集版：与 py 探针同点（digest[i] 状态）对
/// 指定视角做 encode_battle_state，供阶段4-5 编码器对拍。
pub fn run_battle_from_spec_encoded(
    spec: &BattleSpec,
    perspective: usize,
    mask_opp_bench: bool,
) -> (Vec<J>, Vec<crate::encoder::EncodedState>, Option<String>) {
    let (mut state, mut engine, mut rng) = state_from_spec(spec);
    let mut digests = Vec::new();
    let mut encodings = Vec::new();
    let agent_a = RuleAgent::new("A");
    let agent_b = RuleAgent::new("B");
    digests.push(state_digest(&state));
    encodings.push(crate::encoder::encode_battle_state(&state, perspective, mask_opp_bench));
    while state.winner.is_none() && state.turn < MAX_TURNS {
        execute_turn(&mut state, &mut engine, &mut rng, &agent_a, &agent_b);
        digests.push(state_digest(&state));
        encodings.push(crate::encoder::encode_battle_state(&state, perspective, mask_opp_bench));
    }
    (digests, encodings, state.winner.clone())
}

pub fn run_battle_from_spec_masks(
    spec: &BattleSpec,
) -> (Vec<J>, Vec<[[f32; 17]; 2]>, Option<String>) {
    let (mut state, mut engine, mut rng) = state_from_spec(spec);
    let mut digests = Vec::new();
    let agent_a = RuleAgent::new("A");
    let agent_b = RuleAgent::new("B");
    digests.push(state_digest(&state));

    let mut masks: Vec<[[f32; 17]; 2]> = Vec::new();
    // masks[i] 与 digests[i] 同状态（该回合决策点、执行前的掩码）
    while state.winner.is_none() && state.turn < MAX_TURNS {
        masks.push([
            crate::mcts_actions::valid_actions_mask(&state, "A"),
            crate::mcts_actions::valid_actions_mask(&state, "B"),
        ]);
        execute_turn(&mut state, &mut engine, &mut rng, &agent_a, &agent_b);
        digests.push(state_digest(&state));
    }
    (digests, masks, state.winner.clone())
}

pub fn execute_turn(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    agent_a: &RuleAgent,
    agent_b: &RuleAgent,
) {
    state.turn += 1;
    per_turn_cleanup(state);
    phase_turn_start(state, engine, rng);
    // 注意：py _execute_turn_core 在阶段之间没有 winner 短路——
    // winner 只停外层 while 循环；turn_end 阶段无条件执行。

    // 行动选择（含道具循环 ≤8）
    let mut action_a = select_action(state, engine, rng, agent_a, "A");
    let mut action_b = select_action(state, engine, rng, agent_b, "B");
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        let b0 = &state.players[1].team[0];
        eprintln!(
            "[rust actions] turn={} A={:?}{}{:?} B={:?}{}{:?} | B0 hp={}/{} ratio={:.3} bloodline_len={}",
            state.turn,
            action_a.kind,
            action_a.skill_index.map(|i| format!(":{i}")).unwrap_or_default(),
            action_a.switch_index.map(|i| format!(":{i}")).unwrap_or_default(),
            action_b.kind,
            action_b.skill_index.map(|i| format!(":{i}")).unwrap_or_default(),
            action_b.switch_index.map(|i| format!(":{i}")).unwrap_or_default(),
            b0.current_hp,
            b0.max_hp,
            if b0.max_hp > 0 { b0.current_hp as f64 / b0.max_hp as f64 } else { 0.0 },
            b0.bloodline.chars().count(),
        );
    }

    let mut repl = RuleReplPair { a: agent_a, b: agent_b };
    phase_resolve(state, engine, rng, &mut repl, action_a, action_b);
    phase_turn_end(state, engine, rng, &mut repl);
}

/// MCTS 仿真步进（py execute_turn_headless + fixed_action_a/b）：
/// 双方动作给定，力竭换人走 `repl`（网络策略头 / 首只存活兜底）。
///
/// 与 execute_turn 的差异仅在于动作来源：py 侧 fixed 路径下 A/B 的动作
/// 直接使用，不再经过 agent.choose_action 与道具循环（battle.py:868-875），
/// 因此非法/道具动作在结算阶段按 py 语义退化为 no-op（battle.py:1275）。
pub fn execute_turn_fixed(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    action_a: Action,
    action_b: Action,
    repl: &mut dyn ReplacementPolicy,
) {
    state.turn += 1;
    per_turn_cleanup(state);
    phase_turn_start(state, engine, rng);
    phase_resolve(state, engine, rng, repl, action_a, action_b);
    phase_turn_end(state, engine, rng, repl);
}

/// 阶段5a（自对弈循环）：回合开始段——py execute_turn 里 agent 的搜索/
/// 编码发生在本段之后（turn 已自增、清理与回合开始效果已结算）。
pub fn begin_turn(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom) {
    state.turn += 1;
    per_turn_cleanup(state);
    phase_turn_start(state, engine, rng);
}

/// 阶段5a：回合结算段（begin_turn 之后、双侧决策完成后调用）。
pub fn resolve_turn(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    repl: &mut dyn ReplacementPolicy,
    action_a: Action,
    action_b: Action,
) {
    phase_resolve(state, engine, rng, repl, action_a, action_b);
    phase_turn_end(state, engine, rng, repl);
}

/// py execute_turn_headless(fixed_action_a=...) + agent_b=RuleAgent：
/// A 侧动作给定，B 侧由 agent 现选（含道具循环）——非网络对手的搜索路径。
pub fn execute_turn_a_fixed_b_agent(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    action_a: Action,
    agent_b: &RuleAgent,
    repl: &mut dyn ReplacementPolicy,
) {
    state.turn += 1;
    per_turn_cleanup(state);
    phase_turn_start(state, engine, rng);
    let action_b = select_action(state, engine, rng, agent_b, "B");
    phase_resolve(state, engine, rng, repl, action_a, action_b);
    phase_turn_end(state, engine, rng, repl);
}

/// 每回合开始清理（battle.py 817-847）：interrupted、每回合键弹出、
/// 直改重放、永久技能修正恢复。
fn per_turn_cleanup(state: &mut BattleState) {
    const PER_TURN_KEYS: &[&str] = &[
        "power", "power_mult", "damage_mult", "damage_reduction",
        "energy_cost", "energy_cost_mult", "priority", "combo_set",
    ];
    const SKILL_PER_TURN_KEYS: &[&str] = &[
        "power", "power_mult", "damage_mult", "damage_reduction",
        "energy_cost", "energy_cost_mult", "priority", "combo_set",
        "combo", "combo_mult",
    ];
    for pi in 0..2 {
        let ai = state.players[pi].active_index;
        for slot in 0..state.players[pi].team.len() {
            let sprite = &mut state.players[pi].team[slot];
            sprite.interrupted = false;
            for key in PER_TURN_KEYS {
                sprite.modifiers.remove(*key);
            }
            for skill in sprite.skills.iter_mut() {
                for key in SKILL_PER_TURN_KEYS {
                    skill.modifiers.remove(*key);
                }
            }
        }
    }
    // 直改重放（trait_loader.reapply_all_direct_mods）
    let mark_mod_a = mark_energy_mod(state, "A");
    let mark_mod_b = mark_energy_mod(state, "B");
    reapply_all_direct_mods(state, mark_mod_a, mark_mod_b);
    // 永久技能修正恢复（battle.py 845-847：双方全部精灵逐一恢复）
    for pi in 0..2 {
        load_permanent_skill_mods(state, pi);
    }
}

fn mark_energy_mod(state: &BattleState, team: &str) -> i64 {
    state
        .globals
        .mark_effects
        .get(team)
        .map(|marks| {
            marks
                .iter()
                .filter(|m| m.energy_mod != 0)
                .map(|m| m.energy_mod * m.stacks)
                .sum()
        })
        .unwrap_or(0)
}

/// trait_loader.reapply_all_direct_mods。
fn reapply_all_direct_mods(state: &mut BattleState, mark_mod_a: i64, mark_mod_b: i64) {
    if state.direct_mod_sprite_ids.is_empty() {
        return;
    }
    let ids: Vec<(String, usize)> = state.direct_mod_sprite_ids.iter().cloned().collect();
    for (team, idx) in ids {
        if !state.direct_mod_sprite_ids.contains(&(team.clone(), idx)) {
            continue;
        }
        let pi = if team == "A" { 0 } else { 1 };
        let mark_mod = if team == "A" { mark_mod_a } else { mark_mod_b };
        // ttl 递减 + 过期移除（过期时同步移除对应显示效果：
        // py trait_loader.reapply_all_direct_mods → _remove_display_effect）
        let mut expired_keys: Vec<usize> = Vec::new();
        let mut expired_effects: Vec<J> = Vec::new();
        for (i, e) in state.players[pi].team[idx].trait_direct_effects.iter_mut().enumerate() {
            if let Some(obj) = e.as_object_mut() {
                let ttl = obj.get("ttl").and_then(|x| x.as_i64()).unwrap_or(0);
                if ttl > 0 {
                    obj.insert("ttl".into(), J::from(ttl - 1));
                    if ttl - 1 <= 0 {
                        expired_keys.push(i);
                        expired_effects.push(e.clone());
                    }
                }
            }
        }
        for i in expired_keys.into_iter().rev() {
            state.players[pi].team[idx].trait_direct_effects.remove(i);
        }
        for eff in &expired_effects {
            let attr = eff.get("attr").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let source = eff.get("source").and_then(|x| x.as_str()).unwrap_or("").to_string();
            if attr.is_empty() || source.is_empty() {
                continue;
            }
            let sprite = &mut state.players[pi].team[idx];
            sprite.active_effects.retain(|e| {
                !(e.is_stat_buff()
                    && matches!(&e.kind, EffectKind::StatBuff { stat_key: k, steps: 0, .. } if k == &attr)
                    && e.source == source)
            });
        }
        let effects = state.players[pi].team[idx].trait_direct_effects.clone();
        if effects.is_empty() {
            state.direct_mod_sprite_ids.remove(&(team.clone(), idx));
            continue;
        }
        apply_direct_mods(state, &team, idx, &effects, mark_mod);
    }
}

/// trait_loader._apply_direct_mods：power_mod 直改 → 匹配技能 modifiers。
fn apply_direct_mods(
    state: &mut BattleState,
    team: &str,
    idx: usize,
    effects: &[J],
    mark_energy_mod: i64,
) {
    let pi = if team == "A" { 0 } else { 1 };
    const SPRITE_LEVEL: &[&str] =
        &["max_energy", "starfall_consume_ratio", "immune_abnormal", "immune_stat_down"];
    const RATIO_BASE: &[&str] = &[
        "power_mult", "damage_mult", "energy_cost_mult",
        "heal_reverse", "ignore_resistance", "ignore_mods", "survive",
    ];
    // energy_cost 排最前
    let mut sorted_effects: Vec<&J> = effects.iter().collect();
    sorted_effects.sort_by_key(|e| {
        if e.get("attr").and_then(|x| x.as_str()) == Some("energy_cost") { 0 } else { 1 }
    });

    let mut tracked: BTreeMap<String, BTreeMap<String, f64>> = BTreeMap::new();
    for effect in sorted_effects {
        let op = effect.get("op").and_then(|x| x.as_str()).unwrap_or("");
        if op == "burst_grant" {
            apply_burst_grant_direct(state, pi, idx, effect);
            continue;
        }
        if op != "power_mod" {
            continue;
        }
        let attr = effect.get("attr").and_then(|x| x.as_str()).unwrap_or("");
        if SPRITE_LEVEL.contains(&attr) {
            continue;
        }
        // delta 为 dict（查询）时跳过（Python continue）
        let delta_raw = effect.get("delta");
        if delta_raw.map(|d| d.is_object()).unwrap_or(false) {
            continue;
        }
        let mut delta = delta_raw.and_then(|x| x.as_f64()).unwrap_or(0.0);
        let energy_mult = state.players[pi].team[idx]
            .modifiers
            .get("energy_cost_delta_mult")
            .copied()
            .unwrap_or(1.0);
        if attr == "energy_cost" {
            delta *= energy_mult;
        }
        let skill_where = effect.get("skill_where").cloned();
        let skill_filter = effect.get("skill_filter").and_then(|x| x.as_str()).map(String::from);
        let n = state.players[pi].team[idx].skills.len();
        for si in 0..n {
            let (bs_name, bs_energy_cost, bs_element, bs_skill_type, cur) = {
                let sprite = &state.players[pi].team[idx];
                let bs = &sprite.skills[si];
                (
                    bs.name(),
                    bs.energy_cost(),
                    bs.base.element.clone(),
                    bs.base.skill_type.clone(),
                    bs.modifiers.get(attr).copied(),
                )
            };
            if skill_where.is_some() || skill_filter.is_some() {
                let mut info = BTreeMap::new();
                info.insert("name".into(), Val::S(bs_name.clone()));
                info.insert(
                    "energy_cost".into(),
                    Val::I(((bs_energy_cost as f64) - mark_energy_mod as f64).max(0.0) as i64),
                );
                info.insert("element".into(), Val::S(bs_element));
                info.insert("skill_type".into(), Val::S(bs_skill_type.clone()));
                if !crate::replayer::eval_skill_where(skill_where.as_ref(), &info) {
                    continue;
                }
                if let Some(f) = &skill_filter {
                    if f != "all" && !crate::replayer::matches_skill_type(f, &bs_skill_type) {
                        continue;
                    }
                }
            }
            let default = if RATIO_BASE.contains(&attr) { 1.0 } else { 0.0 };
            let cur_v = cur.unwrap_or(default);
            let nv = if effect.get("mode").and_then(|x| x.as_str()) == Some("set") {
                delta
            } else {
                cur_v + delta
            };
            let sprite = &mut state.players[pi].team[idx];
            let bs = &mut sprite.skills[si];
            bs.modifiers.insert(attr.to_string(), nv);
            let t = tracked.entry(bs_name).or_default();
            if effect.get("mode").and_then(|x| x.as_str()) == Some("set") {
                t.insert(attr.to_string(), delta);
            } else {
                *t.entry(attr.to_string()).or_insert(0.0) += delta;
            }
        }
    }
    if !tracked.is_empty() {
        let sprite = &mut state.players[pi].team[idx];
        sprite.direct_mod_tracked = tracked;
    }
}

fn apply_burst_grant_direct(state: &mut BattleState, pi: usize, idx: usize, effect: &J) {
    let skill_where = effect.get("skill_where");
    let skill_filter = effect.get("skill_filter").and_then(|x| x.as_str()).map(String::from);
    let then_effects = effect.get("then").and_then(|x| x.as_array()).cloned().unwrap_or_default();
    if then_effects.is_empty() {
        return;
    }
    let source = effect.get("source").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let sprite = &mut state.players[pi].team[idx];
    for bs in sprite.skills.iter_mut() {
        if let Some(sw) = skill_where {
            let mut info = BTreeMap::new();
            info.insert("name".into(), Val::S(bs.name()));
            info.insert("energy_cost".into(), Val::I(bs.energy_cost()));
            info.insert("element".into(), Val::S(bs.base.element.clone()));
            info.insert("skill_type".into(), Val::S(bs.base.skill_type.clone()));
            if !crate::replayer::eval_skill_where(Some(sw), &info) {
                continue;
            }
        }
        if let Some(f) = &skill_filter {
            if f != "all" && !crate::replayer::matches_skill_type(f, &bs.base.skill_type) {
                continue;
            }
        }
        bs.burst_effects.retain(|e| {
            e.get("source").and_then(|x| x.as_str()).unwrap_or("") != source
        });
        bs.burst_effects.extend(then_effects.iter().cloned());
        bs.modifiers
            .insert("burst".into(), if !bs.burst_effects.is_empty() { 1.0 } else { 0.0 });
    }
}

/// battle.py::_load_permanent_skill_mods_for_sprite。
fn load_permanent_skill_mods(state: &mut BattleState, pi: usize) {
    // battle.py：对双方队伍的每一个精灵都恢复（非仅 active）
    for sprite in state.players[pi].team.iter_mut() {
        let keys: Vec<(String, f64)> = sprite
            .modifiers
            .iter()
            .filter(|(k, _)| k.starts_with("skill."))
            .map(|(k, v)| (k[6..].to_string(), *v))
            .collect();
        if keys.is_empty() {
            continue;
        }
        for (key, value) in keys {
            let (skill_name, stat) = match key.rsplit_once('.') {
                Some((sn, st)) if !sn.is_empty() && !st.is_empty() => {
                    (sn.to_string(), st.to_string())
                }
                _ => continue,
            };
            for bs in sprite.skills.iter_mut() {
                if bs.base.name == skill_name {
                    bs.modifiers.insert(stat.clone(), value);
                }
            }
        }
    }
}

fn select_action(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    agent: &RuleAgent,
    team: &'static str,
) -> Action {
    for _ in 0..8 {
        let action = agent.choose_action(state);
        if action.kind == "item" {
            resolve_item(state, engine, rng, team);
            continue;
        }
        return action;
    }
    Action { kind: "gather", skill_index: None, switch_index: None }
}

// ── 阶段 1：回合开始 ──

fn phase_turn_start(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom) {
    // 延迟效果结算
    for team in ["A", "B"] {
        let pi = if team == "A" { 0 } else { 1 };
        let ai = state.players[pi].active_index;
        if !state.players[pi].team[ai].is_fainted() {
            state.players[pi].team[ai].process_pending_effects();
        }
    }
    // trait turn_start（逐方触发 turn_start 观察者）
    for team in ["A", "B"] {
        let pi = if team == "A" { 0 } else { 1 };
        let ai = state.players[pi].active_index;
        if state.players[pi].team[ai].is_fainted() {
            continue;
        }
        if state.players[pi].team[ai].trait_suppressed {
            continue;
        }
        let opp_team = if team == "A" { "B" } else { "A" };
        let opp_pi = if team == "A" { 1 } else { 0 };
        let opp_ai = state.players[opp_pi].active_index;
        let ctx = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team: team_key(team),
                self_idx: ai,
                opp_idx: opp_ai,
                turn: state.turn,
                ..Default::default()
            },
        );
        engine.fire_trigger("turn_start", state, &ctx, team_key(team), ai, opp_ai, None, rng);
    }
    // 传动（双方）——turn_start 路径：传动后按 "turn_start" 重新投影
    for team in ["A", "B"] {
        apply_transmission(state, engine, rng, team, "turn_start");
    }
    // 不朽：力竭后 3 回合复活
    for pi in 0..2 {
        let team_key_s = if pi == 0 { "A" } else { "B" };
        for i in 0..state.players[pi].team.len() {
            let sprite = &mut state.players[pi].team[i];
            if !sprite.is_fainted() {
                continue;
            }
            let faint_turn = sprite
                .counters
                .get("_faint_turn")
                .copied()
                .unwrap_or(0);
            if faint_turn <= 0 || state.turn - faint_turn < 3 {
                continue;
            }
            let sprite = &mut state.players[pi].team[i];
            sprite.current_hp = sprite.max_hp.max(1);
            sprite.energy = (sprite.energy + 3).min(5);
            sprite.counters.insert("_faint_turn".into(), 0);
            let ai = state.players[pi].active_index;
            if state.players[pi].team[ai].is_fainted() && i != ai {
                state.players[pi].active_index = i;
                let ni = state.players[pi].active_index;
                let new = &mut state.players[pi].team[ni];
                new.clear_effects("battlefield");
                new.entry_turn = state.turn;
                new.first_action = true;
                new.inc_counter("times_entered", 1);
            }
        }
        let _ = team_key_s;
    }
}

fn team_key(t: &str) -> &'static str {
    if t == "A" { "A" } else { "B" }
}

// ── 阶段 3：行动结算 ──

#[allow(clippy::too_many_arguments)]
fn phase_resolve(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    repl: &mut dyn ReplacementPolicy,
    action_a: Action,
    action_b: Action,
) {
    let a_kind = action_a.kind;
    let b_kind = action_b.kind;

    // 双方换宠 → 随机先后（py：第二换受 is_finished 门——winner 或回数上限）
    if a_kind == "switch" && b_kind == "switch" {
        let a_first = rng.random() < 0.5;
        if a_first {
            resolve_switch(state, engine, rng, "A", action_a.switch_index);
            if state.winner.is_none() && state.turn < MAX_TURNS {
                resolve_switch(state, engine, rng, "B", action_b.switch_index);
            }
        } else {
            resolve_switch(state, engine, rng, "B", action_b.switch_index);
            if state.winner.is_none() && state.turn < MAX_TURNS {
                resolve_switch(state, engine, rng, "A", action_a.switch_index);
            }
        }
        return;
    }
    if a_kind == "switch" {
        resolve_switch(state, engine, rng, "A", action_a.switch_index);
        if state.winner.is_none() && state.turn < MAX_TURNS {
            let opp_pi = 1;
            let ai = state.players[opp_pi].active_index;
            if !state.players[opp_pi].team[ai].is_fainted() {
                resolve_after_switch(state, engine, rng, repl, "A", "B", action_b);
            }
        }
        return;
    }
    if b_kind == "switch" {
        resolve_switch(state, engine, rng, "B", action_b.switch_index);
        if state.winner.is_none() && state.turn < MAX_TURNS {
            let opp_pi = 0;
            let ai = state.players[opp_pi].active_index;
            if !state.players[opp_pi].team[ai].is_fainted() {
                resolve_after_switch(state, engine, rng, repl, "B", "A", action_a);
            }
        }
        return;
    }
    resolve_both_skills(state, engine, rng, repl, action_a, action_b);
}

#[allow(clippy::too_many_arguments)]
fn resolve_both_skills(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    repl: &mut dyn ReplacementPolicy,
    action_a: Action,
    action_b: Action,
) {
    let skill_a = get_skill(state, "A", &action_a);
    let skill_b = get_skill(state, "B", &action_b);
    // 回合开始时的在场精灵下标（py s_a/s_b 对象引用；post_counter 用）
    let a_idx0 = state.players[0].active_index;
    let b_idx0 = state.players[1].active_index;

    let mut counter_a = false;
    let mut counter_b = false;
    if let (Some(sa), Some(sb)) = (&skill_a, &skill_b) {
        if sa.cooldown <= 0 {
            counter_a = resolve_counter(sb, sa);
        }
        if sb.cooldown <= 0 {
            counter_b = resolve_counter(sa, sb);
        }
    }
    let countered = counter_a || counter_b;
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        let info = |s: &Option<BattleSkill>| match s {
            Some(b) => format!(
                "id={} cd={} atk={} def={} sta={} counter_len={}",
                b.skill_id, b.cooldown, b.is_attack(), b.is_defense(), b.is_status(),
                b.counter().chars().count(),
            ),
            None => "-".into(),
        };
        eprintln!(
            "[rust counter] turn={} A=[{}] B=[{}] counter_a={} counter_b={}",
            state.turn, info(&skill_a), info(&skill_b), counter_a, counter_b,
        );
    }

    if countered {
        // py 1079-1081：A 行动后 B 侧执行的前提是「B 的在场精灵仍是行动前那一只」
        // ——先手击杀后替补登场会改变 active，此时 B 本回合动作作废
        // （spec_0081 sim3：月亮砣被追打击倒 → 燃薪虫替补，py 不行动，
        //  rust 曾漏掉这层同体判定而让替补打出了纤维化）。
        let b_active_before = state.players[1].active_index;
        execute_skill_vm(
            state, engine, rng, "A", &action_a,
            counter_b,
            if counter_a { skill_b.clone() } else { None },
            if counter_b { skill_b.clone() } else { None },
            true, false, None,
        );
        check_faint_interrupt(state, engine, rng, repl, "A");
        check_faint_interrupt(state, engine, rng, repl, "B");
        if state.winner.is_none() && state.turn < MAX_TURNS {
            let b_active = state.players[1].active_index;
            let b_sprite = &state.players[1].team[b_active];
            if !b_sprite.is_fainted() && b_active == b_active_before {
                execute_skill_vm(
                    state, engine, rng, "B", &action_b,
                    counter_a,
                    if counter_b { skill_a.clone() } else { None },
                    if counter_a { skill_a.clone() } else { None },
                    true, false, None,
                );
                check_faint_interrupt(state, engine, rng, repl, "A");
                check_faint_interrupt(state, engine, rng, repl, "B");
            }
        }
        // py 1094/1099：fire_trigger("post_counter", …) 【不传 self_skill】——
        // 观察者 then 里的 power_mod(target=skill_off_N) 因此落到 sprite._modifiers
        // 而非技能 _modifiers（spec_0175 叠势「应对成功本技能连击+2」：
        // py 写 sprite.combo，rust 曾写 skill.combo + skill.<名>.combo 持久键）
        if counter_a {
            fire_post_counter(state, engine, rng, "A", a_idx0, b_idx0, None);
        }
        if counter_b {
            fire_post_counter(state, engine, rng, "B", b_idx0, a_idx0, None);
        }
        return;
    }
    // 优先级 → 速度 → 随机
    let priority_of = |state: &BattleState, team: &str, action: &Action| -> i64 {
        if action.kind == "gather" {
            return 0;
        }
        get_skill(state, team, action)
            .map(|sk| sk.priority())
            .unwrap_or(0)
            + priority_mod_of(state, team)
    };
    let priority_a = priority_of(state, "A", &action_a);
    let priority_b = priority_of(state, "B", &action_b);

    let (first_team, first_action, second_team, second_action) = if priority_a > priority_b {
        ("A", action_a, "B", action_b)
    } else if priority_b > priority_a {
        ("B", action_b, "A", action_a)
    } else {
        let speed = |state: &BattleState, team: &str| -> i64 {
            let pi = if team == "A" { 0 } else { 1 };
            let sprite = state.players[pi].active();
            let penalty: i64 = state
                .globals
                .mark_effects
                .get(team)
                .map(|marks| {
                    marks
                        .iter()
                        .filter(|m| m.speed_penalty != 0)
                        .map(|m| m.speed_penalty * m.stacks)
                        .sum()
                })
                .unwrap_or(0);
            sprite.effective_stat("speed") - penalty
        };
        let speed_a = speed(state, "A");
        let speed_b = speed(state, "B");
        if speed_a > speed_b {
            ("A", action_a, "B", action_b)
        } else if speed_b > speed_a {
            ("B", action_b, "A", action_a)
        } else if rng.random() < 0.5 {
            ("A", action_a, "B", action_b)
        } else {
            ("B", action_b, "A", action_a)
        }
    };

    let second_pi = if second_team == "A" { 0 } else { 1 };
    let second_sprite_before = state.players[second_pi].active_index;

    // py：opp_skill_for_first = 对手（后手）的技能；反之亦然
    let opp_skill_for_first = if first_team == "A" { skill_b.clone() } else { skill_a.clone() };
    let opp_skill_for_second = if second_team == "A" { skill_b.clone() } else { skill_a.clone() };
    execute_skill_vm(
        state, engine, rng, first_team, &first_action,
        false, None, None, true, false, opp_skill_for_first,
    );
    // py 1150-1151：先手后双方各查一次
    check_faint_interrupt(state, engine, rng, repl, first_team);
    check_faint_interrupt(state, engine, rng, repl, second_team);

    // py 1159-1162：is_finished（winner 或回数上限）或后手已换/已倒 → 跳过；
    // 后手执行后仅查后手方
    let mut ran_second = false;
    if state.winner.is_none() && state.turn < MAX_TURNS {
        let second_now = state.players[second_pi].active_index;
        let second_fainted = state.players[second_pi].team[second_now].is_fainted();
        if !second_fainted && second_now == second_sprite_before {
            ran_second = true;
            execute_skill_vm(
                state, engine, rng, second_team, &second_action,
                false, None, None, false, false, opp_skill_for_second,
            );
            check_faint_interrupt(state, engine, rng, repl, second_team);
        }
    }
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        eprintln!(
            "[rust resolve] turn={} first={} first_act={}/{:?} second={} second_act={}/{:?} \
             sec_before={} sec_now={} ran_second={} winner={:?}",
            state.turn,
            first_team,
            first_action.kind,
            first_action.skill_index,
            second_team,
            second_action.kind,
            second_action.skill_index,
            second_sprite_before,
            state.players[second_pi].active_index,
            ran_second,
            state.winner,
        );
    }
}

fn resolve_after_switch(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    repl: &mut dyn ReplacementPolicy,
    switch_team: &'static str,
    opp_team: &'static str,
    opp_action: Action,
) {
    let switch_pi = if switch_team == "A" { 0 } else { 1 };
    let new_fainted = state.players[switch_pi].active().is_fainted();
    if new_fainted {
        return;
    }
    // 迅捷技能
    let active = state.players[switch_pi].active();
    let mut swift_idx: Option<usize> = None;
    for (i, bs) in active.skills.iter().enumerate() {
        if bs.modifiers.get("swift").copied().unwrap_or(0.0) != 0.0
            && !bs.sealed
            && bs.cooldown <= 0
        {
            swift_idx = Some(i);
            break;
        }
    }
    match swift_idx {
        None => {
            execute_skill_vm(
                state, engine, rng, opp_team, &opp_action,
                false, None, None, true, true, None,
            );
        }
        Some(si) => {
            let swift_action =
                Action { kind: "skill", skill_index: Some(si), switch_index: None };
            execute_skill_vm(
                state, engine, rng, switch_team, &swift_action,
                false, None, None, true, false, None,
            );
            check_faint_interrupt(state, engine, rng, repl, switch_team);
            check_faint_interrupt(state, engine, rng, repl, opp_team);
            if state.winner.is_none() {
                let opp_active = state.players[if opp_team == "A" { 0 } else { 1 }].active_index;
                let opp_fainted = state.players[if opp_team == "A" { 0 } else { 1 }].team[opp_active].is_fainted();
                if !opp_fainted {
                    execute_skill_vm(
                        state, engine, rng, opp_team, &opp_action,
                        false, None, None, false, true, None,
                    );
                }
            }
        }
    }
}

// ── 技能执行（含全部门）──

#[allow(clippy::too_many_arguments)]
fn execute_skill_vm(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    team: &'static str,
    action: &Action,
    _is_countered: bool,
    _countered_skill: Option<BattleSkill>,
    countering_skill: Option<BattleSkill>,
    is_first: bool,
    opponent_switched: bool,
    _opp_skill: Option<BattleSkill>,
) {
    let pi = if team == "A" { 0 } else { 1 };
    let opp_pi = 1 - pi;
    let user_idx = state.players[pi].active_index;
    let target_idx = state.players[opp_pi].active_index;

    if state.players[pi].team[user_idx].is_fainted() {
        return;
    }
    // py _execute_skill_vm 1245-1249：蓄力中一切行动被拦（含技能——因此
    // 蓄力释放分支实际不可达；仅换宠/力竭清蓄力。属 py 既有行为，须镜像）
    if state.players[pi].team[user_idx].charging {
        return;
    }

    if action.kind == "gather" {
        let gained = state.players[pi].team[user_idx].gain_energy(5);
        let user = &mut state.players[pi].team[user_idx];
        user.first_action = false;
        user.first_action_battle = false;
        user.inc_counter("times_gathered", 1);
        let opp_team_idx = opp_pi;
        *state.team_counters.entry(if opp_team_idx == 0 { "A".into() } else { "B".into() })
            .or_default()
            .entry("enemy_gather".into())
            .or_insert(0) += 1;
        let _ = gained;
        // post_energy_change（囤积等）
        let opp_ai = state.players[opp_pi].active_index;
        if gained > 0 && engine.registry.has_candidates("post_energy_change") {
            let mut ctx = crate::snapshot::build_ctx(
                state,
                &crate::snapshot::BuildCtxOpts {
                    team,
                    self_idx: user_idx,
                    opp_idx: opp_ai,
                    turn: state.turn,
                    ..Default::default()
                },
            );
            ctx.event.energy_changed_of = "sprite_self".into();
            ctx.energy_delta_self = gained;
            engine.fire_trigger("post_energy_change", state, &ctx, team, user_idx, opp_ai, None, rng);
        }
        return;
    }

    if action.kind != "skill" {
        return;
    }
    let Some(skill_idx) = action.skill_index else { return };
    let Some(bs) = state.players[pi].team[user_idx].skills.get(skill_idx).cloned() else {
        return;
    };

    // 冷却门
    if bs.cooldown > 0 {
        return;
    }

    // 蓄力门
    match gate_charge(state, pi, user_idx, &bs, skill_idx) {
        GateCharge::EnterCharge => {
            let opp_ai = state.players[opp_pi].active_index;
            let ctx = crate::snapshot::build_ctx(
                state,
                &crate::snapshot::BuildCtxOpts {
                    team,
                    self_idx: user_idx,
                    opp_idx: opp_ai,
                    turn: state.turn,
                    ..Default::default()
                },
            );
            engine.fire_trigger("post_charge", state, &ctx, team, user_idx, opp_ai, None, rng);
            return;
        }
        GateCharge::Blocked => return,
        GateCharge::Pass => {}
    }

    // on_next pending modifiers 消耗
    {
        let skill_type = bs.base.skill_type.clone();
        let user = &mut state.players[pi].team[user_idx];
        if !user.pending_modifiers.is_empty() {
            user.consume_pending_modifiers(&skill_type);
        }
    }

    // 奉献消耗（py battle.py 1341-1386：仅 use_devotion 技能触发；
    // 触发时消耗全部层数并清空）
    let mut devotion_triggered = false;
    let mut devotion_saved: Vec<(String, f64)> = Vec::new();
    let use_devotion = crate::data::skills::skill_by_name(&bs.name())
        .map(|s| s.use_devotion)
        .unwrap_or(false);
    let devotion_stacks: Vec<(String, i64)> = state.players[pi]
        .devotion
        .iter()
        .map(|(k, v)| (k.clone(), *v))
        .filter(|(_, v)| use_devotion && *v > 0)
        .collect();
    if !devotion_stacks.is_empty() {
        let user = &mut state.players[pi].team[user_idx];
        for key in ["combo", "power", "life_drain", "energy_cost"] {
            if let Some(v) = user.modifiers.get(key).copied() {
                devotion_saved.push((key.to_string(), v));
            }
        }
        let mut abnormal_mods: Vec<Mutation> = Vec::new();
        for (dname, dcount) in &devotion_stacks {
            if let Some(dtype) = crate::statics::devotion_effect(dname) {
                if let Some(c) = dtype.get("combo").and_then(|x| x.as_i64()) {
                    let cur = user.modifiers.get("combo").copied().unwrap_or(0.0);
                    user.modifiers.insert("combo".into(), cur + (c * dcount) as f64);
                }
                if let Some(c) = dtype.get("energy_cost").and_then(|x| x.as_i64()) {
                    let cur = user.modifiers.get("energy_cost").copied().unwrap_or(0.0);
                    user.modifiers.insert("energy_cost".into(), cur + (c * dcount) as f64);
                }
                if let Some(c) = dtype.get("power").and_then(|x| x.as_i64()) {
                    let cur = user.modifiers.get("power").copied().unwrap_or(0.0);
                    user.modifiers.insert("power".into(), cur + (c * dcount) as f64);
                }
                if let Some(c) = dtype.get("life_drain").and_then(|x| x.as_f64()) {
                    let cur = user.modifiers.get("life_drain").copied().unwrap_or(0.0);
                    user.modifiers.insert("life_drain".into(), cur + c * *dcount as f64);
                }
                if let Some(ab) = dtype.get("abnormal") {
                    let name = ab.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    let stacks = ab.get("stacks").and_then(|x| x.as_i64()).unwrap_or(0);
                    abnormal_mods.push(Mutation::AbnormalChange {
                        target: "sprite_opp".into(),
                        name,
                        delta: stacks * dcount,
                        scope: "battlefield".into(),
                    });
                }
            }
        }
        if !abnormal_mods.is_empty() {
            let mut r = crate::replayer::Replayer::new(
                state, team, user_idx, target_idx, Some(skill_idx), rng,
            );
            r.replay(&abnormal_mods);
        }
        state.players[pi].devotion.clear();
        devotion_triggered = true;
    }

    // 迸发
    if state.players[pi].team[user_idx].first_action && !bs.burst_effects.is_empty() {
        let burst_effects = bs.burst_effects.clone();
        let opp_ai = state.players[opp_pi].active_index;
        let ctx = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team,
                self_idx: user_idx,
                opp_idx: opp_ai,
                turn: state.turn,
                ..Default::default()
            },
        );
        let burst_journal = vm_exec::execute(&ctx, &burst_effects);
        let mut r = crate::replayer::Replayer::new(
            state, team, user_idx, target_idx, Some(skill_idx), rng,
        );
        r.replay(&burst_journal);
    }

    // 能量支付
    let cost = skill_energy_cost(state, team, user_idx, &bs, skill_idx);
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        eprintln!(
            "[rust energy] turn={} team={} name_len={} cost={cost} e={}",
            state.turn,
            team,
            bs.name().chars().count(),
            state.players[pi].team[user_idx].energy
        );
    }
    if cost > 0 {
        let user = &mut state.players[pi].team[user_idx];
        if user.energy >= cost {
            user.lose_energy(cost);
        } else {
            let blood_price = user.modifiers.get("blood_price").copied().unwrap_or(0.0);
            if blood_price > 0.0 {
                let deficit = cost - user.energy;
                let hp_cost = crate::damage::py_round(user.max_hp as f64 * blood_price * deficit as f64);
                if user.current_hp > hp_cost {
                    user.lose_energy(user.energy);
                    user.take_damage(hp_cost);
                } else {
                    return;
                }
            } else {
                return;
            }
        }
    }
    state.players[pi].team[user_idx].modifiers.remove("energy_cost");

    // 技能耗能后的 post_energy_change（py battle.py 1432-1442）
    if cost > 0 && engine.registry.has_candidates("post_energy_change") {
        let opp_ai2 = state.players[opp_pi].active_index;
        let mut ctx = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team,
                self_idx: user_idx,
                opp_idx: opp_ai2,
                turn: state.turn,
                ..Default::default()
            },
        );
        ctx.event.energy_changed_of = "sprite_self".into();
        engine.fire_trigger("post_energy_change", state, &ctx, team, user_idx, opp_ai2, None, rng);
    }

    // 打断门
    if state.players[pi].team[user_idx].interrupted {
        if let Some(b) = state.players[pi].team[user_idx].skills.get_mut(skill_idx) {
            b.nullified = true;
        }
        return;
    }

    {
        let user = &mut state.players[pi].team[user_idx];
        user.inc_counter(&format!("skill_used:{}", bs.name()), 1);
        user.inc_counter("skills_used", 1);
    }

    // 应对方直改效果注入（以对方视角执行非 when 效果）。
    // py 1469 经 execute_effects 执行【编译后效果】——含攻击技能的隐式 hit
    // （惊吓盒子应对：反伤 67 由此而来），故此处也要带技能头注入隐式 hit。
    if _is_countered {
        if let Some(countering) = &countering_skill {
            let opp_team_key = if team == "A" { "B" } else { "A" };
            let opp_team_idx = opp_pi;
            // py 过滤的是【编译后 IR】的 `hasattr(e,'when') and e.when`：编译后
            // 带 when 字段的只有 CountOp（counter 注册，见 ir_skill.py:356）；
            // WhenBlock 的字段名是 cond，因此 `{"when":..,"then":..,"else":..}`
            // 【不能】被排除——它在注入 ctx 里求值（counter_succeeded=False 时走
            // else 分支），spec_0037 精神扰乱 +1 能耗即由此而来。
            let mut direct: Vec<J> = countering
                .base
                .effects
                .iter()
                .filter(|e| {
                    let obj = e.as_object();
                    let is_count = obj
                        .and_then(|o| o.get("op"))
                        .and_then(|v| v.as_str())
                        .map(|s| s == "count")
                        .unwrap_or(false);
                    let has_when = obj.map(|o| o.contains_key("when")).unwrap_or(false);
                    !(is_count && has_when)
                })
                .cloned()
                .collect();
            // py 的 _get_effects(counter_record) 返回编译后效果——含攻击技能
            // 编译期注入的隐式 hit（应对反伤的来源）。rust 的 base.effects
            // 没有它，需显式补上。
            let st = countering.base.skill_type.as_str();
            if matches!(st, "物攻" | "魔攻" | "动态攻击") && countering.base.power > 0 {
                let mut hit = serde_json::Map::new();
                hit.insert("op".into(), J::String("hit".into()));
                hit.insert("power".into(), J::from(countering.base.power));
                hit.insert("type".into(), J::String(countering.base.skill_type.clone()));
                if !countering.base.element.is_empty() {
                    hit.insert("element".into(), J::String(countering.base.element.clone()));
                }
                hit.insert("combo".into(), J::from(countering.base.combo));
                direct.push(J::Object(hit));
            }
            if !direct.is_empty() {
                let opp_ai = state.players[pi].active_index; // target=user
                // py 以 counter_record（CompiledSkill）作 self_skill、battle_skill=None
                // 构建 ctx：combo_self/power_self/element_self 全取自【该技能记录】，
                // 而非场上精灵的当前技能槽。连击技能应对反伤数值（spec_0140：
                // 连续爪击 base combo=2 → 反伤 61 而非 30）即由此而来。
                let ctx = crate::snapshot::build_ctx(
                    state,
                    &crate::snapshot::BuildCtxOpts {
                        team: opp_team_key,
                        self_idx: target_idx,
                        opp_idx: opp_ai,
                        turn: state.turn,
                        self_skill_record: Some(crate::snapshot::SelfSkillRecord {
                            power: countering.base.power,
                            combo: countering.base.combo,
                            energy_cost: countering.base.energy_cost,
                            element: countering.base.element.clone(),
                            skill_type: countering.base.skill_type.clone(),
                        }),
                        ..Default::default()
                    },
                );
                let header = serde_json::json!({
                    "skill_type": countering.base.skill_type,
                    "power": countering.base.power,
                    "element": countering.base.element,
                    "combo": countering.base.combo,
                });
                let counter_journal =
                    vm_exec::execute_with_skill_header(&ctx, &direct, Some(&header));
                if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                    eprintln!(
                        "[rust counter-ctx] atk_self={} stages_atk={:?} buffs={:?}",
                        ctx.atk_self,
                        ctx.stat_stages_self.get("atk"),
                        state.players[opp_team_idx].team[target_idx]
                            .active_effects
                            .iter()
                            .filter(|e| e.is_stat_buff())
                            .map(|e| (e.name.clone(), e.scope.clone(), e.steps()))
                            .collect::<Vec<_>>()
                    );
                }
                let mut r = crate::replayer::Replayer::new(
                    state, opp_team_key, target_idx, user_idx, None, rng,
                );
                r.replay(&counter_journal);
                if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                    eprintln!(
                        "[rust counter inject] journal={} dr={:?} target=(team={}, idx={})",
                        counter_journal.len(),
                        state.players[opp_team_idx].team[target_idx]
                            .modifiers
                            .get("damage_reduction"),
                        opp_team_key,
                        target_idx,
                    );
                }
            }
            let _ = opp_team_idx;
        }
    }

    // 17 步管线
    // 对手当前技能下标（py：opp_skill = countered_skill or countering_skill
    // or opp_skill）——ctx.skill_type_opp / opp_is_attack 等条件依赖
    let opp_skill_idx = _countered_skill
        .as_ref()
        .or(countering_skill.as_ref())
        .or(_opp_skill.as_ref())
        .and_then(|sk| {
            state.players[opp_pi].team[target_idx]
                .skills
                .iter()
                .position(|b| b.skill_id == sk.skill_id)
        });
    let params = ExecParams {
        team,
        self_idx: user_idx,
        opp_idx: target_idx,
        self_skill_idx: skill_idx,
        opp_skill_idx,
        effects: skill_effects_of(state, pi, user_idx, skill_idx),
        turn: state.turn,
        is_first,
        opp_switched: opponent_switched,
        was_countered: _is_countered,
        counter_succeeded: _countered_skill.is_some(),
        skill_index: skill_idx as i64,
        devotion_triggered,
        damage_taken_this_turn: 0,
        prev_skill_type: String::new(),
        prev_damage_taken_self: false,
        prev_damage_taken_opp: false,
        skill_name: bs.name(),
        skill: skill_header_of(state, pi, user_idx, skill_idx),
    };
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        eprintln!(
            "[rust exec-vm] turn={} team={} skill={} opp_switched={} first={}",
            state.turn, team, bs.name(), opponent_switched, is_first,
        );
    }
    let journal = engine.execute_skill(state, rng, &params);

    // 恢复奉献 modifiers
    if devotion_triggered {
        let user = &mut state.players[pi].team[user_idx];
        for (key, v) in &devotion_saved {
            user.modifiers.insert(key.clone(), *v);
        }
        for key in ["combo", "life_drain", "power", "energy_cost"] {
            if !devotion_saved.iter().any(|(k, _)| k == key) {
                user.modifiers.remove(key);
            }
        }
    }

    // Escape 处理（py battle.py 1509-1529：urgent → 立即随机换；否则挂起）
    for m in &journal {
        if let Mutation::Escape { inherit, urgent, .. } = m {
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                eprintln!("[rust escape-immediate] turn={} team={} user={} urgent={}",
                    state.turn, team, user_idx, urgent);
            }
            if *urgent {
                handle_escape(state, engine, rng, team, user_idx, *inherit, true);
            } else {
                let user_name = state.players[pi].team[user_idx].name();
                state.pending_escape = Some(crate::battle_state::PendingEscape {
                    team: team.to_string(),
                    sprite_idx: user_idx,
                    user_name,
                    inherit: *inherit,
                    urgent: false,
                });
            }
            break;
        }
    }

    // 后处理
    {
        let user = &mut state.players[pi].team[user_idx];
        user.first_action = false;
        user.first_action_battle = false;
        let remaining = user.modifiers.get("_burst_extended").copied().unwrap_or(0.0);
        if remaining > 0.0 {
            user.first_action = true;
            user.modifiers.insert("_burst_extended".into(), remaining - 1.0);
        }
        user.remove_effect("charged", "state");
    }

    // 防御技能冷却
    if bs.base.skill_type == "防御" {
        if let Some(b) = state.players[pi].team[user_idx].skills.get_mut(skill_idx) {
            b.cooldown = 2;
        }
    }

    // counters
    if !bs.base.element.is_empty() {
        let c = state.team_counters.entry(team.to_string()).or_default();
        *c.entry(format!("element:{}", bs.base.element)).or_insert(0) += 1;
    }
    let st = bs.base.skill_type.as_str();
    if st == "防御" {
        let c = state.team_counters.entry(team.to_string()).or_default();
        *c.entry("defense_skill".into()).or_insert(0) += 1;
    } else if !matches!(st, "物攻" | "魔攻" | "动态攻击") {
        let c = state.team_counters.entry(team.to_string()).or_default();
        *c.entry("status_skill".into()).or_insert(0) += 1;
    }
}

fn skill_effects_of(state: &BattleState, pi: usize, si: usize, skill_i: usize) -> Vec<J> {
    // Rust 解释器直接吃原始 JSON；Replaced-by 场景取替换技能的效果
    if let Some(bs) = state.players[pi].team[si].skills.get(skill_i) {
        return bs.replaced_by.as_ref().map(|r| r.effects.clone()).unwrap_or_else(|| bs.base.effects.clone());
    }
    vec![]
}

fn skill_header_of(state: &BattleState, pi: usize, si: usize, skill_i: usize) -> Option<J> {
    // InjectHitPass 需要的技能级字段（base 或 replaced_by）
    let bs = state.players[pi].team[si].skills.get(skill_i)?;
    let src = bs.replaced_by.as_ref().map(|r| {
        serde_json::json!({
            "skill_type": r.skill_type,
            "power": r.power,
            "element": r.element,
            "combo": r.combo,
        })
    }).unwrap_or_else(|| {
        serde_json::json!({
            "skill_type": bs.base.skill_type,
            "power": bs.base.power,
            "element": bs.base.element,
            "combo": bs.base.combo,
        })
    });
    Some(src)
}

fn bs_is_defense_of(state: &BattleState, pi: usize, si: usize, skill_i: usize) -> bool {
    state.players[pi].team[si].skills.get(skill_i).map(|b| b.is_defense()).unwrap_or(false)
}

fn gate_charge(
    state: &mut BattleState,
    pi: usize,
    user_idx: usize,
    bs: &BattleSkill,
    skill_idx: usize,
) -> GateCharge {
    let effects_iter = bs
        .base
        .effects
        .iter()
        .chain(bs.replaced_by.iter().flat_map(|r| r.effects.iter()));
    let mut has_charge_iter = effects_iter;
    let has_charge = has_charge_iter.any(json_has_charge);

    let user = &state.players[pi].team[user_idx];
    let is_charging = user.charging;
    let charged_idx = user.charged_skill_ref.and_then(|sid| {
        user.skills.iter().position(|b| b.skill_id == sid)
    });

    if is_charging && has_charge && charged_idx == Some(skill_idx) {
        // 释放蓄力
        let user = &mut state.players[pi].team[user_idx];
        user.charging = false;
        user.charged_skill_ref = None;
        user.remove_effect("charging", "state");
        user.add_effect(Effect {
            name: "charged".into(),
            source: "charge".into(),
            scope: "battlefield".into(),
            ttl: 0,
            cooldown: 0,
            kind: EffectKind::State {
                state_type: "charged".into(),
                params: J::Null,
            },
        });
        return GateCharge::Pass;
    }
    if is_charging {
        let usable_while = bs.base.usable_while_charging;
        let any_skill = user
            .modifiers
            .get("charge_any_skill")
            .copied()
            .unwrap_or(0.0)
            > 0.0;
        if usable_while || any_skill {
            let user = &mut state.players[pi].team[user_idx];
            user.charging = false;
            user.charged_skill_ref = None;
            user.remove_effect("charging", "state");
            return GateCharge::Pass;
        }
        return GateCharge::Blocked;
    }
    if has_charge {
        let pre_charged = user
            .modifiers
            .get("pre_charged")
            .copied()
            .unwrap_or(0.0);
        if pre_charged > 0.0 {
            let user = &mut state.players[pi].team[user_idx];
            let left = pre_charged - 1.0;
            if left <= 0.0 {
                user.modifiers.remove("pre_charged");
            } else {
                user.modifiers.insert("pre_charged".into(), left);
            }
            user.add_effect(Effect {
                name: "charged".into(),
                source: "charge".into(),
                scope: "battlefield".into(),
                ttl: 0,
                cooldown: 0,
                kind: EffectKind::State {
                    state_type: "charged".into(),
                    params: J::Null,
                },
            });
            return GateCharge::Pass;
        }
        let user = &mut state.players[pi].team[user_idx];
        user.charging = true;
        user.charged_skill_ref = Some(bs.skill_id);
        user.charged_skill_index = skill_idx as i64;
        user.add_effect(Effect {
            name: "charging".into(),
            source: "charge".into(),
            scope: "persistent".into(),
            ttl: 0,
            cooldown: 0,
            kind: EffectKind::State {
                state_type: "charging".into(),
                params: J::Null,
            },
        });
        return GateCharge::EnterCharge;
    }
    GateCharge::Pass
}

fn json_has_charge(e: &J) -> bool {
    match e {
        J::Object(o) => {
            if o.get("op").and_then(|x| x.as_str()) == Some("charge") {
                return true;
            }
            o.values().any(json_has_charge)
        }
        J::Array(a) => a.iter().any(json_has_charge),
        _ => false,
    }
}

enum GateCharge {
    EnterCharge,
    Blocked,
    Pass,
}

fn get_skill(state: &BattleState, team: &str, action: &Action) -> Option<BattleSkill> {
    if action.kind != "skill" {
        return None;
    }
    let i = action.skill_index?;
    let pi = if team == "A" { 0 } else { 1 };
    state.players[pi].active().skills.get(i).cloned()
}

fn priority_mod_of(state: &BattleState, team: &str) -> i64 {
    let pi = if team == "A" { 0 } else { 1 };
    let sprite = state.players[pi].active();
    sprite.sum_steps("priority")
}

fn resolve_counter(atk: &BattleSkill, def: &BattleSkill) -> bool {
    match def.counter().as_str() {
        "攻击" => atk.is_attack(),
        "防御" => atk.is_defense(),
        "状态" => atk.is_status(),
        _ => false,
    }
}

pub(crate) fn skill_energy_cost(
    state: &BattleState,
    team: &str,
    user_idx: usize,
    skill: &BattleSkill,
    skill_index: usize,
) -> i64 {
    let pi = if team == "A" { 0 } else { 1 };
    let user = &state.players[pi].team[user_idx];
    let mut cost = skill.energy_cost();
    let ec_mod = user.modifiers.get("energy_cost").copied().unwrap_or(0.0);
    if ec_mod != 0.0 {
        cost += crate::damage::py_round(ec_mod);
    }
    let ec_mult = user.modifiers.get("energy_cost_mult").copied().unwrap_or(0.0);
    if ec_mult != 0.0 {
        cost = crate::damage::py_round(cost as f64 * (1.0 + ec_mult));
    }
    // 轴承支撑（相邻同名技能）
    for offset in [-1i64, 1i64] {
        let ni = skill_index as i64 + offset;
        if ni >= 0 && (ni as usize) < user.skills.len()
            && user.skills[ni as usize].base.name == "轴承支撑"
        {
            cost -= 1;
            break;
        }
    }
    let mark_mod: i64 = state
        .globals
        .mark_effects
        .get(team)
        .map(|marks| {
            marks
                .iter()
                .filter(|mk| mk.energy_mod != 0)
                .map(|mk| mk.energy_mod * mk.stacks)
                .sum()
        })
        .unwrap_or(0);
    cost -= mark_mod;
    cost
}

// ── 换宠 / 力竭 ──

fn resolve_switch(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    team: &'static str,
    switch_index: Option<usize>,
) {
    let Some(new_index) = switch_index else { return };
    let pi = if team == "A" { 0 } else { 1 };
    let old_idx = state.players[pi].active_index;
    if new_index >= state.players[pi].team.len() {
        return;
    }
    if state.players[pi].team[new_index].is_fainted() {
        return;
    }
    // 换宠打断蓄力
    state.players[pi].team[old_idx].charging = false;
    state.players[pi].team[old_idx].charged_skill_ref = None;

    state.players[pi].active_index = new_index;
    enter_sprite(state, engine, rng, team, new_index);
    // team counter: enemy switch（搜刮 等 pre-entry accumulator）
    let opp_team_key = if team == "A" { "B" } else { "A" };
    let counters = state.team_counters.entry(opp_team_key.to_string()).or_default();
    *counters.entry("enemy_switch".into()).or_insert(0) += 1;
    // post_leave（old 视角）
    let opp_pi = 1 - pi;
    let opp_ai = state.players[opp_pi].active_index;
    let ctx_leave = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx: old_idx,
            opp_idx: opp_ai,
            turn: state.turn,
            self_switched: true,
            ..Default::default()
        },
    );
    // 临时自交换视角：post_leave 的 owner 是 old
    engine.fire_trigger("post_leave", state, &ctx_leave, team, old_idx, opp_ai, None, rng);
    apply_pending_entry(state, team, new_index);
    fire_post_entry(state, engine, rng, team, new_index);
    apply_transmission(state, engine, rng, team, "post_entry");
    if !state.players[opp_pi].team[opp_ai].is_fainted() {
        let opp_team = if team == "A" { "B" } else { "A" };
        let ctx_enemy = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team: opp_team,
                self_idx: opp_ai,
                opp_idx: new_index,
                turn: state.turn,
                opp_switched: true,
                ..Default::default()
            },
        );
        // py：post_enemy_leave 传 leaving_sprite=old（source=sprite_opp 的继承用）
        engine.leaving_loc = Some((pi, old_idx));
        engine.fire_trigger("post_enemy_leave", state, &ctx_enemy, opp_team, opp_ai, new_index, None, rng);
        engine.leaving_loc = None;
    }
    dispatch_leave(state, engine, team, old_idx, false);
}

fn enter_sprite(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom, team: &'static str, new_index: usize) {
    let pi = if team == "A" { 0 } else { 1 };
    let opp_pi = 1 - pi;
    let opp_ai = state.players[opp_pi].active_index;
    // 印记入场效果（棘刺/降灵）
    let (dmg, lost) = {
        let new = &state.players[pi].team[new_index];
        let opp_team = if team == "A" { "B" } else { "A" };
        let mut d = 0i64;
        let mut l = 0i64;
        if let Some(marks) = state.globals.mark_effects.get(opp_team) {
            for mk in marks {
                if mk.switch_damage_pct != 0.0 {
                    d += crate::damage::py_round(
                        new.max_hp as f64 * mk.switch_damage_pct * mk.stacks as f64,
                    )
                    .max(0);
                }
                if mk.switch_energy_loss != 0 {
                    l += mk.switch_energy_loss * mk.stacks;
                }
            }
        }
        (d, l)
    };
    {
        let new = &mut state.players[pi].team[new_index];
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            eprintln!(
                "[rust enter] turn={} team={} idx={} name_len={} old_entry={}",
                state.turn, team, new_index, new.name().chars().count(), new.entry_turn,
            );
        }
        if dmg > 0 {
            new.take_damage(dmg);
        }
        if lost > 0 {
            new.lose_energy(lost);
        }
        new.clear_effects("battlefield");
        new.entry_turn = state.turn;
        new.first_action = true;
        new.inc_counter("times_entered", 1);
    }
    dispatch_entry(state, engine, rng, team);
}

fn fire_post_entry(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom, team: &'static str, idx: usize) {
    let pi = if team == "A" { 0 } else { 1 };
    let opp_pi = 1 - pi;
    let opp_ai = state.players[opp_pi].active_index;
    let ctx = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx: idx,
            opp_idx: opp_ai,
            turn: state.turn,
            ..Default::default()
        },
    );
    engine.fire_trigger("post_entry", state, &ctx, team, idx, opp_ai, None, rng);
}

fn apply_pending_entry(state: &mut BattleState, team: &str, idx: usize) {
    let pi = if team == "A" { 0 } else { 1 };
    let pending = state.pending_effects.entry(team.to_string()).or_default();
    if pending.is_empty() {
        return;
    }
    let pending = std::mem::take(pending);
    // 转 Effect 并应用
    for payload in pending {
        if let Ok(eff) = serde_json::from_value::<Effect>(payload) {
            state.players[pi].team[idx].add_effect(eff);
        }
    }
}

#[track_caller]
fn dispatch_entry(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom, team: &'static str) {
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        let pi = if team == "A" { 0 } else { 1 };
        let ai = state.players[pi].active_index;
        let loc = std::panic::Location::caller();
        eprintln!("[rust dispatch_entry] team={team} idx={ai} caller={}", loc.file());
        eprintln!("[rust dispatch_entry]   line={}", loc.line());
    }
    let pi = if team == "A" { 0 } else { 1 };
    let ai = state.players[pi].active_index;
    apply_pending_entry(state, team, ai);
    {
        let sprite = &mut state.players[pi].team[ai];
        if sprite.entry_turn == 0 {
            sprite.entry_turn = state.turn;
        }
    }
    // trait 加载（observers + 直改）
    load_traits(state, engine, rng, team, ai);
    // post_energy_change 初始触发
    {
        let opp_pi = 1 - pi;
        let opp_ai = state.players[opp_pi].active_index;
        if engine.registry.has_candidates("post_energy_change") {
            let mut ctx = crate::snapshot::build_ctx(
                state,
                &crate::snapshot::BuildCtxOpts {
                    team,
                    self_idx: ai,
                    opp_idx: opp_ai,
                    turn: state.turn,
                    ..Default::default()
                },
            );
            ctx.event.energy_changed_of = "sprite_self".into();
            engine.fire_trigger("post_energy_change", state, &ctx, team, ai, opp_ai, None, rng);
        }
    }
}

fn dispatch_leave(state: &mut BattleState, engine: &mut VmEngine, team: &'static str, idx: usize, is_faint: bool) {
    let pi = if team == "A" { 0 } else { 1 };
    let reason = if is_faint { "faint" } else { "leave" };
    {
        let sprite = &mut state.players[pi].team[idx];
        for bs in sprite.skills.iter_mut() {
            bs.mech_energy_reduction = 0;
        }
        sprite.clear_effects("battlefield");
        sprite.clear_effects("turn");
    }
    unload_traits(state, engine, team, idx, reason);
}

/// trait_loader.unload_for_sprite：注销观察者 + 移除直改登记 + 按
/// should_clear(reason) 清理效果对象（faint 清 persistent/battlefield）。
fn unload_traits(state: &mut BattleState, engine: &mut VmEngine, team: &'static str, idx: usize, reason: &str) {
    let pi = if team == "A" { 0 } else { 1 };
    engine.registry.unregister_by_owner((team, idx), reason);
    state.direct_mod_sprite_ids.remove(&(team.to_string(), idx));
    let sprite = &mut state.players[pi].team[idx];
    // _remove_direct_mods：从技能 modifiers 减回 tracked 的累计 delta
    if !sprite.direct_mod_tracked.is_empty() {
        let tracked = std::mem::take(&mut sprite.direct_mod_tracked);
        for bs in sprite.skills.iter_mut() {
            if let Some(attrs) = tracked.get(&bs.name()) {
                for (attr, delta) in attrs {
                    let cur = bs.modifiers.get(attr).copied().unwrap_or(0.0);
                    bs.modifiers.insert(attr.clone(), cur - delta);
                }
            }
        }
    }
    // should_clear(reason) 过滤
    sprite.active_effects.retain(|e| !e.should_clear(reason));
}

fn check_faint_interrupt(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    repl: &mut dyn ReplacementPolicy,
    team: &'static str,
) {
    let pi = if team == "A" { 0 } else { 1 };
    let ai = state.players[pi].active_index;
    if !state.players[pi].team[ai].is_fainted() {
        return;
    }
    let old = ai;
    let replacement = repl.choose_replacement(state, team);
    if replacement < 0 || replacement as usize >= state.players[pi].team.len() {
        state.players[pi].lives -= 1;
        state.winner = Some(if team == "A" { "B".into() } else { "A".into() });
        return;
    }
    let ri = replacement as usize;
    if state.players[pi].team[ri].is_fainted() {
        state.players[pi].lives -= 1;
        state.winner = Some(if team == "A" { "B".into() } else { "A".into() });
        return;
    }
    state.players[pi].active_index = ri;
    enter_sprite(state, engine, rng, team, ri);
    // post_ko（old 视角）
    let opp_pi = 1 - pi;
    let opp_ai = state.players[opp_pi].active_index;
    let ctx_ko = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx: old,
            opp_idx: opp_ai,
            turn: state.turn,
            target_fainted: true,
            self_switched: true,
            ..Default::default()
        },
    );
    engine.fire_trigger("post_ko", state, &ctx_ko, team, old, opp_ai, None, rng);
    state.players[pi].lives -= 1;
    if state.players[pi].lives <= 0 {
        state.winner = Some(if team == "A" { "B".into() } else { "A".into() });
        return;
    }
    let ctx_entry = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx: ri,
            opp_idx: opp_ai,
            turn: state.turn,
            ..Default::default()
        },
    );
    let ctx_leave = ctx_ko;
    engine.fire_trigger("post_leave", state, &ctx_leave, team, old, opp_ai, None, rng);
    apply_pending_entry(state, team, ri);
    engine.fire_trigger("post_entry", state, &ctx_entry, team, ri, opp_ai, None, rng);
    apply_transmission(state, engine, rng, team, "post_entry");
    if !state.players[opp_pi].team[opp_ai].is_fainted() {
        let opp_team = if team == "A" { "B" } else { "A" };
        let ctx_enemy = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team: opp_team,
                self_idx: opp_ai,
                opp_idx: ri,
                turn: state.turn,
                opp_switched: true,
                ..Default::default()
            },
        );
        engine.leaving_loc = Some((pi, old));
        engine.fire_trigger("post_enemy_leave", state, &ctx_enemy, opp_team, opp_ai, ri, None, rng);
        engine.leaving_loc = None;
    }
    dispatch_leave(state, engine, team, old, true);
}

fn handle_escape(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    team: &'static str,
    user_idx: usize,
    inherit: bool,
    urgent: bool,
) {
    let pi = if team == "A" { 0 } else { 1 };
    let replacement: i64 = if urgent {
        let bench: Vec<usize> = state.players[pi]
            .team
            .iter()
            .enumerate()
            .filter(|(i, s)| !s.is_fainted() && *i != user_idx)
            .map(|(i, _)| i)
            .collect();
        let r = if bench.is_empty() { -1 } else { *rng.choice(&bench) as i64 };
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            eprintln!("[rust escape] turn={} team={} user={} active={} bench={:?} -> {}",
                state.turn, team, user_idx, state.players[pi].active_index, bench, r);
        }
        r
    } else {
        -1
    };
    if replacement < 0 {
        return;
    }
    let ri = replacement as usize;
    let inherited: Vec<Effect> = if inherit {
        state.players[pi].team[user_idx]
            .active_effects
            .iter()
            .filter(|e| e.is_stat_buff() && e.steps() > 0)
            .cloned()
            .collect()
    } else {
        vec![]
    };
    state.players[pi].active_index = ri;
    {
        let new_sprite = &mut state.players[pi].team[ri];
        if std::env::var("ROCO_DEBUG_DMG").is_ok() {
            eprintln!(
                "[rust entry-set escape] turn={} team={} ri={} old_entry={}",
                state.turn, team, ri, new_sprite.entry_turn,
            );
        }
        new_sprite.clear_effects("battlefield");
        new_sprite.entry_turn = state.turn;
        new_sprite.first_action = true;
        new_sprite.inc_counter("times_entered", 1);
        for e in inherited {
            new_sprite.add_effect(e);
        }
    }
    dispatch_leave(state, engine, team, user_idx, false);
    dispatch_entry(state, engine, rng, team);
    // Observer: post_leave + post_entry（镜像 battle_mechanics 脱离路径）
    let opp_pi = 1 - pi;
    let opp_ai = state.players[opp_pi].active_index;
    let ctx_leave = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx: user_idx,
            opp_idx: opp_ai,
            turn: state.turn,
            self_switched: true,
            ..Default::default()
        },
    );
    engine.fire_trigger("post_leave", state, &ctx_leave, team, user_idx, opp_ai, None, rng);
    apply_pending_entry(state, team, ri);
    let ctx_entry = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx: ri,
            opp_idx: opp_ai,
            turn: state.turn,
            ..Default::default()
        },
    );
    engine.fire_trigger("post_entry", state, &ctx_entry, team, ri, opp_ai, None, rng);
    apply_transmission(state, engine, rng, team, "post_entry");
}

// ── 传动 ──

/// py `_reapply_position_modifiers(trigger, team, sprite, opp)`：传动后把
/// 位置型特性的 skill_at_N 修饰符重新投影到当前槽位（向心力/翼轴等）。
/// 只回放 target.startswith("skill_at_") 的 ModifierInjection。
fn reapply_position_modifiers(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    trigger: &str,
    team: &'static str,
    sprite_idx: usize,
    opp_idx: usize,
) {
    if !engine.registry.has_candidates(trigger) {
        return;
    }
    let candidates: Vec<Observer> = engine
        .registry
        .candidates(trigger, Some((team, sprite_idx)))
        .into_iter()
        .cloned()
        .collect();
    if candidates.is_empty() {
        return;
    }
    let ctx = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx: sprite_idx,
            opp_idx,
            turn: state.turn,
            ..Default::default()
        },
    );
    let mut journal: Vec<Mutation> = Vec::new();
    for obs in &candidates {
        if obs.eval_cond(&ctx).unwrap_or(false) {
            for m in vm_exec::process_effects(&ctx, &obs.then) {
                if let Mutation::ModifierInjection { target, .. } = &m {
                    if target.starts_with("skill_at_") {
                        journal.push(m);
                    }
                }
            }
        }
    }
    if journal.is_empty() {
        return;
    }
    let mut r = crate::replayer::Replayer::new(
        state, team, sprite_idx, opp_idx, None, rng,
    );
    r.replay(&journal);
}

fn apply_transmission(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    team: &'static str,
    reapply_trigger: &str,
) {
    let pi = if team == "A" { 0 } else { 1 };
    let ai = state.players[pi].active_index;
    let opp_pi = 1 - pi;
    let opp_ai = state.players[opp_pi].active_index;
    let n = state.players[pi].team[ai].skills.len();
    if n < 2 {
        return;
    }
    let max_lv = state.players[pi].team[ai]
        .skills
        .iter()
        .map(|bs| bs.transmission.max(0))
        .max()
        .unwrap_or(0);
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        eprintln!(
            "[rust transmission] turn={} team={} active={} max_lv={} trans=[{}]",
            state.turn, team, ai, max_lv,
            state.players[pi].team[ai]
                .skills
                .iter()
                .map(|b| b.transmission.to_string())
                .collect::<Vec<_>>()
                .join(","),
        );
    }
    if max_lv <= 0 {
        return;
    }
    let mechanical = {
        let sprite = state.players[pi].active();
        !sprite.trait_suppressed
            && (sprite.species.ability_id == 20159 || sprite.species.ability == "机械变式")
    };
    let mut changed_any = false;
    for _pass in 0..max_lv {
        let skills = &mut state.players[pi].team[ai].skills;
        // 虚拟数组（排除主轴 -1）
        let mut active_map: Vec<usize> = Vec::new();
        for (i, bs) in skills.iter().enumerate() {
            if bs.transmission != -1 {
                active_map.push(i);
            }
        }
        let m = active_map.len();
        if m < 2 {
            continue;
        }
        // 收集块
        let mut blocks: Vec<(usize, usize)> = Vec::new();
        let mut i = 0;
        while i < m {
            let lv = skills[active_map[i]].transmission;
            if lv <= _pass {
                i += 1;
                continue;
            }
            let block_start = i;
            let mut block_end = i;
            while block_end + 1 < m && skills[active_map[block_end + 1]].transmission > _pass {
                block_end += 1;
            }
            blocks.push((block_start, block_end));
            i = block_end + 1;
        }
        // 合并循环边界块
        if blocks.len() >= 2 {
            let (f_start, f_end) = blocks[0];
            let (l_start, l_end) = *blocks.last().unwrap();
            if f_start == 0 && l_end == m - 1 {
                blocks[0] = (l_start, f_end);
                blocks.pop();
            }
        }
        if blocks.is_empty() {
            continue;
        }
        // 旋转：虚拟数组上的「技能值」轮转（active_map 固定；py 同序：
        // temp = list(active) 于块循环前快照一次）
        let mut active_skills: Vec<BattleSkill> = active_map
            .iter()
            .map(|&oi| state.players[pi].team[ai].skills[oi].clone())
            .collect();
        let temp = active_skills.clone();
        for (block_start, block_end) in &blocks {
            let displaced = (block_end + 1) % m;
            if block_start <= block_end {
                for pos in *block_start..=*block_end {
                    active_skills[(pos + 1) % m] = temp[pos].clone();
                }
            } else {
                for pos in *block_start..m {
                    active_skills[(pos + 1) % m] = temp[pos].clone();
                }
                for pos in 0..=*block_end {
                    active_skills[(pos + 1) % m] = temp[pos].clone();
                }
            }
            if displaced != *block_start {
                active_skills[*block_start] = temp[displaced].clone();
            }
        }
        // 写回原始槽位
        let skill_ids_before: Vec<u64> =
            state.players[pi].team[ai].skills.iter().map(|b| b.skill_id).collect();
        for (vi, &oi) in active_map.iter().enumerate() {
            state.players[pi].team[ai].skills[oi] = active_skills[vi].clone();
        }
        // 机械变式：位置发生变化的技能本回合能耗-1（py：pre_pos 对比）
        // py 第六步：moved_skills 按新下标序收集；机械变式减耗；随后
        // 对每个移动的技能 fire skill_position_changed 观察者
        // （齿轮扭矩类「位置变化时威力永久+20」）
        let mut moved_ids: Vec<u64> = Vec::new();
        let mut changed = false;
        {
            let sprite = &mut state.players[pi].team[ai];
            for (i, bs) in sprite.skills.iter_mut().enumerate() {
                let old_pos = skill_ids_before.iter().position(|id| *id == bs.skill_id);
                if old_pos != Some(i) {
                    moved_ids.push(bs.skill_id);
                    changed = true;
                    if mechanical {
                        bs.mech_energy_reduction -= 1;
                    }
                }
            }
        }
        if !moved_ids.is_empty() {
            for sid in &moved_ids {
                fire_skill_position_changed(state, engine, rng, team, ai, opp_ai, *sid);
            }
        }
        changed_any = changed_any || changed;
    }
    // py：reapply 在所有 pass 结束后由调用方执行【一次】
    // （pipeline / _apply_entry_transmission），不在每轮 pass 内——
    // pass 间重放 skill_at_ 注入会改写后续 pass 的传动分块
    // （翼轴 drive → 位置0 传动=1，spec_0065 t3 根因）
    if changed_any {
        reapply_position_modifiers(state, engine, rng, reapply_trigger, team, ai, opp_ai);
    }
}

/// py `battle.py fire_skill_position_changed`：传动后对单个移动技能
/// 触发其自身绑定的 skill_position_changed 观察者（齿轮扭矩类）。
/// 候选 = {无主 ∪ 该精灵拥有}（candidates 语义），
/// 过滤：cond 树含 skill_position_changed；owner_skill_id 绑定到本技能。
fn fire_skill_position_changed(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    team: &'static str,
    sprite_idx: usize,
    opp_idx: usize,
    moved_skill_id: u64,
) {
    if !engine.registry.has_candidates("post_skill") {
        return;
    }
    let candidates: Vec<crate::observers::Observer> = engine
        .registry
        .candidates("post_skill", Some((team, sprite_idx)))
        .into_iter()
        .cloned()
        .collect();
    if candidates.is_empty() {
        return;
    }
    let pi = if team == "A" { 0 } else { 1 };
    let pos = state.players[pi].team[sprite_idx]
        .skills
        .iter()
        .position(|b| b.skill_id == moved_skill_id);
    let opts = crate::snapshot::BuildCtxOpts {
        team,
        self_idx: sprite_idx,
        opp_idx,
        turn: state.turn,
        skill_position_changed: true,
        self_skill_idx: pos,
        skill_index: pos.map(|p| p as i64).unwrap_or(0),
        ..Default::default()
    };
    let ctx = crate::snapshot::build_ctx(state, &opts);
    for obs in &candidates {
        if !cond_contains(&obs.cond, "skill_position_changed") {
            continue;
        }
        if let Some(osid) = obs.owner_skill_id {
            if osid != moved_skill_id {
                continue;
            }
        }
        if obs.eval_cond(&ctx).unwrap_or(false) {
            let journal: Vec<Mutation> = vm_exec::process_effects(&ctx, &obs.then);
            let mut r = crate::replayer::Replayer::new(
                state, team, sprite_idx, opp_idx, pos, rng,
            );
            r.trait_sourcing = true;
            r.replay(&journal);
        }
    }
}

/// py battle.py `_cond_contains`：条件树是否引用指定 cond 名。
fn cond_contains(cond: &J, name: &str) -> bool {
    let Some(obj) = cond.as_object() else { return false };
    let key = obj.get("cond").and_then(|x| x.as_str()).unwrap_or("");
    if key == name {
        return true;
    }
    match key {
        "and" | "or" => obj
            .get("conditions")
            .and_then(|x| x.as_array())
            .map(|a| a.iter().any(|c| cond_contains(c, name)))
            .unwrap_or(false),
        "not" => cond_contains(obj.get("condition").unwrap_or(&J::Null), name),
        _ => false,
    }
}

// ── 延时效果 ──

fn execute_scheduled(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom, phase: &str) {
    let due: Vec<crate::battle_state::ScheduledEffect> = state
        .scheduled_effects
        .iter()
        .filter(|s| s.turn <= state.turn && s.phase == phase)
        .cloned()
        .collect();
    if due.is_empty() {
        return;
    }
    for sched in due {
        state.scheduled_effects.retain(|s| {
            !(s.turn == sched.turn && s.phase == sched.phase && s.effects == sched.effects)
        });
        let team = match &sched.ctx_snapshot {
            J::Object(o) => o
                .get("team")
                .and_then(|x| x.as_str())
                .unwrap_or("A")
                .to_string(),
            _ => "A".to_string(),
        };
        let team_s: &'static str = if team == "B" { "B" } else { "A" };
        let pi = if team_s == "A" { 0 } else { 1 };
        let ai = state.players[pi].active_index;
        let opp_pi = 1 - pi;
        let opp_ai = state.players[opp_pi].active_index;
        if sched.effects.is_empty() {
            continue;
        }
        let ctx = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team: team_s,
                self_idx: ai,
                opp_idx: opp_ai,
                turn: state.turn,
                ..Default::default()
            },
        );
        let journal = vm_exec_process(&ctx, &sched.effects);
        let mut r = crate::replayer::Replayer::new(state, team_s, ai, opp_ai, None, rng);
        r.replay(&journal);
    }
}

fn vm_exec_process(ctx: &crate::vm_ctx::Ctx, effects: &[J]) -> Vec<Mutation> {
    crate::vm_exec::process_effects(ctx, effects)
}

// ── 阶段 4：回合结束 ──

fn phase_turn_end(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    repl: &mut dyn ReplacementPolicy,
) {
    execute_scheduled(state, engine, rng, "end");

    // turn scope 清理 + TTL
    for pi in 0..2 {
        for sprite in state.players[pi].team.iter_mut() {
            if sprite.is_fainted() {
                continue;
            }
            if !sprite.active_effects.is_empty() || !sprite.mod_scopes.is_empty() {
                sprite.clear_effects("turn");
                sprite.decrement_ttl();
            }
        }
    }

    // 借用还原（借用替换技能槽）
    if !state.borrowed_restore.is_empty() {
        let borrowed = std::mem::take(&mut state.borrowed_restore);
        for (key, mut original) in borrowed {
            let (team, slot) = parse_restore_key(&key);
            let pi = if team == "A" { 0 } else { 1 };
            let ai = state.players[pi].active_index;
            if slot < state.players[pi].team[ai].skills.len() {
                let bs = &mut state.players[pi].team[ai].skills[slot];
                bs.replaced_by = None;
                let _ = original;
            }
        }
    }

    // 愿力还原（一回合后换回原技能；仅当 active 未力竭）
    if !state.wish_restore.is_empty() {
        let wish = std::mem::take(&mut state.wish_restore);
        for (key, original) in wish {
            let (team, slot) = parse_restore_key(&key);
            let pi = if team == "A" { 0 } else { 1 };
            let ai = state.players[pi].active_index;
            if !state.players[pi].team[ai].is_fainted()
                && slot < state.players[pi].team[ai].skills.len()
            {
                state.players[pi].team[ai].skills[slot] = original;
            }
        }
    }

    // 返场结算（py _resolve_return：清 battlefield 效果 + 完整入场管线——
    // dispatch_entry + post_entry 观察者 + 入场传动，缺一不可：
    // 轴承支撑类 sprite_entered 观察者的 power 写入与传动旋转都挂在这）
    for pi in 0..2 {
        let team_s: &'static str = if pi == 0 { "A" } else { "B" };
        let ai = state.players[pi].active_index;
        let pending = state.players[pi].team[ai].pending_return;
        if !state.players[pi].team[ai].is_fainted() && pending {
            state.players[pi].team[ai].pending_return = false;
            state.players[pi].team[ai].extra_skill_use = true;
            let sprite = &mut state.players[pi].team[ai];
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                eprintln!(
                    "[rust entry-set return] turn={} team={} ai={} old_entry={}",
                    state.turn, pi, ai, sprite.entry_turn,
                );
            }
            sprite.clear_effects("battlefield");
            sprite.entry_turn = state.turn;
            sprite.first_action = true;
            sprite.inc_counter("times_entered", 1);
            let opp_pi = 1 - pi;
            let opp_ai = state.players[opp_pi].active_index;
            let _ = opp_ai;
            dispatch_entry(state, engine, rng, team_s);
            fire_post_entry(state, engine, rng, team_s, ai);
            apply_transmission(state, engine, rng, team_s, "post_entry");
        }
    }

    // py battle.py 1797-1801：sprites dict 在 turn_end 结算【之前】按存活在场
    // 精灵快照（只排除此刻已力竭者）。结算（异常 tick/暴风雪/印记）过程中
    // 力竭的精灵仍留在列表里，其 turn_end / post_abnormal_tick 观察者照常
    // 触发——spec_0019 sim2 的 毒蘑菇 turn_end 偷能量缺口即此根因；
    // 同时也解释了 spec_0182（结算前已力竭者不参与 extra_turn 判定）。
    let sprites: Vec<(usize, &'static str, usize)> = (0..2)
        .filter_map(|pi| {
            let ai = state.players[pi].active_index;
            if state.players[pi].team[ai].is_fainted() {
                None
            } else {
                Some((pi, if pi == 0 { "A" } else { "B" }, ai))
            }
        })
        .collect();

    // resolver.turn_end（tick + 冷却 + 暴风雪 + 天气 + 印记）
    turn_end_settlement(state, engine, rng, false);

    // extra_turn 判定同样基于结算前的 sprites 快照（py 1806-1809）
    let extra_turn = sprites.iter().any(|&(pi, _, ai)| {
        state.players[pi].team[ai]
            .modifiers
            .get("extra_turn_end")
            .copied()
            .unwrap_or(0.0)
            > 0.0
    });
    if extra_turn {
        turn_end_settlement(state, engine, rng, true);
    }

    // post_abnormal_tick（双方视角，灼烧/中毒；遍历结算前的快照列表）
    for &(pi, team_s, ai) in &sprites {
        let opp_pi = 1 - pi;
        let opp_ai = state.players[opp_pi].active_index;
        if !engine.registry.has_candidates("post_abnormal_tick") {
            continue;
        }
        let names: Vec<String> = state.players[pi].team[ai]
            .active_effects
            .iter()
            .filter(|e| e.is_abnormal() && matches!(e.name.as_str(), "灼烧" | "中毒"))
            .map(|e| e.name.clone())
            .collect();
        for name in names {
            let dmg = state.players[pi].team[ai]
                .last_abnormal_dmg
                .get(&name)
                .copied()
                .unwrap_or(0);
            let ctx_self = crate::snapshot::build_ctx(
                state,
                &crate::snapshot::BuildCtxOpts {
                    team: team_s,
                    self_idx: ai,
                    opp_idx: opp_ai,
                    turn: state.turn,
                    ..Default::default()
                },
            );
            let mut c = ctx_self.clone();
            c.event.last_tick_abnormal = name.clone();
            c.event.last_tick_target = "sprite_self".into();
            c.last_tick_damage_self = dmg;
            engine.fire_trigger("post_abnormal_tick", state, &c, team_s, ai, opp_ai, None, rng);
            let ctx_opp = crate::snapshot::build_ctx(
                state,
                &crate::snapshot::BuildCtxOpts {
                    team: if pi == 0 { "B" } else { "A" },
                    self_idx: opp_ai,
                    opp_idx: ai,
                    turn: state.turn,
                    ..Default::default()
                },
            );
            let mut c2 = ctx_opp;
            c2.event.last_tick_abnormal = name;
            c2.event.last_tick_target = "sprite_opp".into();
            c2.last_tick_damage_opp = dmg;
            engine.fire_trigger("post_abnormal_tick", state, &c2, if pi == 0 { "B" } else { "A" }, opp_ai, ai, None, rng);
        }
    }

    // turn_end 观察者（遍历结算前的快照列表——py 不在此处重查力竭）
    for &(pi, team_s, ai) in &sprites {
        let opp_pi = 1 - pi;
        let opp_ai = state.players[opp_pi].active_index;
        // py 1836：`has_candidates("turn_end", id(sprite))` 为假直接 continue——
        // urgent pending escape 的结算在这个门【之内】，双方都无 turn_end
        // 候选时 pending 不结算（spec_0164 sim19：掩护的应对注入又挂了一份
        // urgent pending，rust 曾无门结算 → 多脱离一次/多抽一次随机）
        if !engine.registry.has_candidates_for("turn_end", (team_s, ai)) {
            continue;
        }
        let mut ctx = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team: team_s,
                self_idx: ai,
                opp_idx: opp_ai,
                turn: state.turn,
                ..Default::default()
            },
        );
        ctx.event.turn_end = true;
        engine.fire_trigger("turn_end", state, &ctx, team_s, ai, opp_ai, None, rng);
        // urgent escape 即时结算（py _resolve_pending_escape_if_urgent：
        // 仅 urgent；pending 【无条件清除】后再校验逃离者是否仍在场——
        // 不匹配也丢弃，否则滞留的 urgent pending 会在逃离者恰好回到
        // 该槽位时二次触发（spec_0196 t11 根因）
        if let Some(pe) = state.pending_escape.clone() {
            if pe.urgent {
                state.pending_escape = None;
                let eteam_s: &'static str = if pe.team == "A" { "A" } else { "B" };
                let epi = if eteam_s == "A" { 0 } else { 1 };
                let cur = state.players[epi].active_index;
                // py battle_mechanics.py:458-461：取【当前在场精灵】与 pending 里的
                // user_name 比对（名字，非下标）——立即脱离已换人后，滞留的
                // urgent pending 在此被丢弃（spec_0164 sim19 多脱离一次的根因）
                if state.players[epi].team[cur].name() == pe.user_name {
                    handle_escape(state, engine, rng, eteam_s, cur, pe.inherit, true);
                }
            }
        }
    }

    // 星地善良：回合末己方能量=0 → 板凳星地善良替换上场
    // （py battle.py 1851-1898：turn_end_bench_check hook 无注册回调 →
    //   仅扫描板凳 ability_id==20158；无该特性则不换）
    for pi in 0..2 {
        let team_s: &'static str = if pi == 0 { "A" } else { "B" };
        let ai = state.players[pi].active_index;
        if state.players[pi].team[ai].is_fainted() {
            continue;
        }
        if state.players[pi].team[ai].energy > 0 {
            continue;
        }
        let swap_index = state.players[pi]
            .team
            .iter()
            .enumerate()
            .find(|(i, s)| {
                *i != ai
                    && !s.is_fainted()
                    && !s.trait_suppressed
                    && s.species.ability_id == 20158
            })
            .map(|(i, _)| i);
        let Some(ri) = swap_index else { continue };
        if ri == ai {
            continue;
        }
        state.players[pi].active_index = ri;
        {
            let new_sprite = &mut state.players[pi].team[ri];
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                eprintln!(
                    "[rust entry-set bench] turn={} team={} ri={} old_entry={}",
                    state.turn, team_s, ri, new_sprite.entry_turn,
                );
            }
            new_sprite.clear_effects("battlefield");
            new_sprite.entry_turn = state.turn;
            new_sprite.first_action = true;
            new_sprite.inc_counter("times_entered", 1);
        }
        dispatch_leave(state, engine, team_s, ai, false);
        dispatch_entry(state, engine, rng, team_s);
        // Observer: post_leave + post_entry
        let opp_pi = 1 - pi;
        let opp_ai = state.players[opp_pi].active_index;
        let ctx_leave = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team: team_s,
                self_idx: ai,
                opp_idx: opp_ai,
                turn: state.turn,
                self_switched: true,
                ..Default::default()
            },
        );
        engine.fire_trigger("post_leave", state, &ctx_leave, team_s, ai, opp_ai, None, rng);
        apply_pending_entry(state, team_s, ri);
        let ctx_entry = crate::snapshot::build_ctx(
            state,
            &crate::snapshot::BuildCtxOpts {
                team: team_s,
                self_idx: ri,
                opp_idx: opp_ai,
                turn: state.turn,
                ..Default::default()
            },
        );
        engine.fire_trigger("post_entry", state, &ctx_entry, team_s, ri, opp_ai, None, rng);
        apply_transmission(state, engine, rng, team_s, "post_entry");
    }

    // 冻结斩杀
    for pi in 0..2 {
        let ai = state.players[pi].active_index;
        let sprite = &mut state.players[pi].team[ai];
        if !sprite.is_fainted() && sprite.get_stacks("冻结") > 0 {
            sprite.check_freeze_death();
        }
    }

    // 回合结束力竭检查（battle.py 1911-1914：对双方各调一次）
    check_faint_interrupt(state, engine, rng, repl, "A");
    check_faint_interrupt(state, engine, rng, repl, "B");
}

/// resolver.turn_end（双向光速 extra 由调用方控制重复调用）。
fn turn_end_settlement(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom, _extra: bool) {
    if std::env::var("ROCO_DEBUG_DMG").is_ok() {
        eprintln!("[rust tick] settlement entered, turn={}", state.turn);
    }
    // tick 伤害
    let mut cinder_grass_active: Option<bool> = None;
    for pi in 0..2 {
        let team_s: &'static str = if pi == 0 { "A" } else { "B" };
        let ai = state.players[pi].active_index;
        if state.players[pi].team[ai].is_fainted() {
            continue;
        }
        let names: Vec<String> = state.players[pi].team[ai]
            .active_effects
            .iter()
            .filter(|e| e.is_abnormal())
            .map(|e| e.name.clone())
            .collect();
        for name in names {
            let (stacks, pct, elem, decay, per_stack, max_stacks) = {
                let sprite = &state.players[pi].team[ai];
                let e = sprite
                    .active_effects
                    .iter()
                    .find(|e| e.is_abnormal() && e.name == name)
                    .unwrap();
                match &e.kind {
                    EffectKind::Abnormal {
                        stacks,
                        tick_damage_pct,
                        tick_element,
                        decay_on_tick,
                        max_stacks,
                        tick_per_stack,
                    } => (*stacks, *tick_damage_pct, tick_element.clone(), *decay_on_tick, *tick_per_stack, *max_stacks),
                    _ => (0, 0.0, String::new(), false, true, 0),
                }
            };
            if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                eprintln!("[rust tick] name={name} stacks={stacks} pct={pct}");
            }
            if stacks <= 0 || pct <= 0.0 {
                continue;
            }
            let (actual, self_koed) = {
                let sprite = &mut state.players[pi].team[ai];
                let raw = if per_stack {
                    crate::damage::py_round(sprite.max_hp as f64 * pct * stacks as f64).max(1)
                } else {
                    crate::damage::py_round(sprite.max_hp as f64 * pct).max(1)
                };
                let attrs = sprite.species.elements();
                let mult = statics::element_mult(&elem, &attrs);
                let dmg = crate::damage::py_round(raw as f64 * mult).max(1);
                let actual = sprite.take_damage(dmg);
                if std::env::var("ROCO_DEBUG_DMG").is_ok() {
                    eprintln!(
                        "[rust tick-dmg] name={name} hp_max={} per_stack={per_stack} raw={raw} elem={elem} mult={mult} dmg={dmg} actual={actual}",
                        sprite.max_hp
                    );
                }
                sprite.last_abnormal_dmg.insert(name.clone(), actual);
                (actual, sprite.is_fainted())
            };

            if decay {
                if name == "灼烧" && cinder_grass_active.is_none() {
                    cinder_grass_active = Some(state.players.iter().enumerate().any(|(ppi, p)| {
                        p.team.iter().enumerate().any(|(sidx, s)| {
                            if ppi == pi && sidx == ai && self_koed {
                                return false;
                            }
                            !s.is_fainted()
                                && s.modifiers.get("_cinder_grass").copied().unwrap_or(0.0) != 0.0
                        })
                    }));
                }
                if name == "灼烧" && cinder_grass_active == Some(true) {
                    let growth = stacks / 2;
                    let new_stacks = stacks + growth;
                    state.players[pi].team[ai].update_stacks(&name, new_stacks);
                } else {
                    let new_stacks = stacks / 2;
                    state.players[pi].team[ai].update_stacks(&name, new_stacks);
                }
            }
            let _ = max_stacks;
        }
        // 冷却递减
        let sprite = &mut state.players[pi].team[ai];
        for bs in sprite.skills.iter_mut() {
            if bs.cooldown > 0 {
                bs.cooldown -= 1;
            }
        }
    }

    // 暴风雪（snow）
    if state.globals.weather == "snow" {
        for pi in 0..2 {
            let ai = state.players[pi].active_index;
            let fainted = state.players[pi].team[ai].is_fainted();
            if fainted {
                continue;
            }
            if let Some(mut tpl) = statics::abnormal_template("冻结") {
                if let EffectKind::Abnormal { stacks, .. } = &mut tpl.kind {
                    *stacks = 2;
                }
                tpl.source = "暴风雪".into();
                state.players[pi].team[ai].add_effect(tpl);
            }
        }
    }
    // 天气递减
    if state.globals.weather_turns > 0 {
        state.globals.weather_turns -= 1;
        if state.globals.weather_turns == 0 {
            state.globals.weather = String::new();
        }
    }
    // 印记回合末效果（能量/伤害）
    for pi in 0..2 {
        let team_s = if pi == 0 { "A" } else { "B" };
        let ai = state.players[pi].active_index;
        if state.players[pi].team[ai].is_fainted() {
            continue;
        }
        let ops: Vec<(i64, f64, String)> = state.globals.mark_effects
            .get(team_s)
            .map(|marks| {
                marks
                    .iter()
                    .filter(|m| m.turn_end_energy != 0 || m.turn_end_damage_pct != 0.0)
                    .map(|m| (m.turn_end_energy, m.turn_end_damage_pct, m.name.clone()))
                    .zip(std::iter::repeat(m_stacks_of(state, team_s)))
                    .map(|((te, td, n), _)| (te, td, n))
                    .collect()
            })
            .unwrap_or_default();
        let _ = ops;
        // 直接内联处理
        let marks = state.globals.mark_effects.get(team_s).cloned().unwrap_or_default();
        let sprite = &mut state.players[pi].team[ai];
        for mk in &marks {
            if mk.turn_end_energy != 0 {
                sprite.gain_energy(mk.turn_end_energy * mk.stacks);
            }
            if mk.turn_end_damage_pct != 0.0 {
                let dmg = crate::damage::py_round(
                    sprite.max_hp as f64 * mk.turn_end_damage_pct * mk.stacks as f64,
                )
                .max(1);
                sprite.take_damage(dmg);
            }
        }
        let _ = rng;
        let _ = engine;
    }
}

fn parse_restore_key(key: &str) -> (&'static str, usize) {
    let (t, s) = key.split_once(':').unwrap_or(("A", "0"));
    let team: &'static str = if t == "B" { "B" } else { "A" };
    (team, s.parse().unwrap_or(0))
}

fn m_stacks_of(_state: &BattleState, _team: &str) -> i64 {
    0
}

/// 应对成功 → post_counter 观察者 + team counter。
/// `self_idx`/`opp_idx` 为【回合开始时】的在场精灵下标（py 用 s_a/s_b
/// 对象引用——中途脱离换人后仍指向原精灵，spec_0196 t6 思维之盾误触发
/// 的根因）；`self_skill_id` 同理为应对技能的实例 id。
#[allow(clippy::too_many_arguments)]
fn fire_post_counter(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    team: &'static str,
    self_idx: usize,
    opp_idx: usize,
    self_skill_id: Option<u64>,
) {
    let pi = if team == "A" { 0 } else { 1 };
    let opp_pi = 1 - pi;
    let self_skill_idx = self_skill_id.and_then(|id| {
        state.players[pi].team[self_idx]
            .skills
            .iter()
            .position(|b| b.skill_id == id)
    });
    let counters = state.team_counters.entry(team.to_string()).or_default();
    *counters.entry("counter_success".into()).or_insert(0) += 1;
    // py battle.py 1093：counter_succeeded=True + self_skill=skill
    let mut ctx = crate::snapshot::build_ctx(
        state,
        &crate::snapshot::BuildCtxOpts {
            team,
            self_idx,
            opp_idx,
            self_skill_idx,
            turn: state.turn,
            ..Default::default()
        },
    );
    ctx.event.counter_succeeded = true;
    engine.fire_trigger("post_counter", state, &ctx, team, self_idx, opp_idx, self_skill_idx, rng);
}

/// 道具使用（_resolve_item）：进化之力 / 愿力。
pub(crate) fn resolve_item(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom, team: &'static str) -> String {
    let pi = if team == "A" { 0 } else { 1 };
    let turn = state.turn;
    let can_use = state.players[pi]
        .item
        .as_ref()
        .map(|it| it.can_use(turn))
        .unwrap_or(false);
    if !can_use {
        return String::new();
    }
    let item_name = state.players[pi].item.as_ref().unwrap().name.clone();
    let sprite = state.players[pi].active();
    let bloodline = sprite.bloodline.clone();
    if item_name == "进化之力" && bloodline != "首领" {
        return String::new();
    }
    if item_name == "愿力" && !ELEMENTAL_BLOODLINES_RUST.contains(&bloodline.as_str()) {
        return String::new();
    }
    let ai = state.players[pi].active_index;
    if item_name == "进化之力" {
        let leader = state.players[pi].team[ai].leader_form.clone();
        let Some(lf) = leader else { return String::new() };
        {
            let item = state.players[pi].item.as_mut().unwrap();
            item.uses += 1;
            item.last_use_turn = turn;
        }
        let sprite = &mut state.players[pi].team[ai];
        let hp_ratio = if sprite.max_hp > 0 {
            sprite.current_hp as f64 / sprite.max_hp as f64
        } else {
            0.0
        };
        sprite.species = lf.species;
        sprite.initial_stats = lf.initial_stats.clone();
        sprite.max_hp = lf.max_hp;
        sprite.current_hp = crate::damage::py_round(lf.max_hp as f64 * hp_ratio).max(1);
        sprite.bloodline_skills = sprite.species.bloodline_skills.clone();
        sprite.first_action = true;
        sprite.moe_chain.clear();
        sprite.moe_position = 0;
        sprite.moe_origin = None;
        sprite.moe_origin_skills.clear();
        sprite.remove_effect("萌化", "abnormal");
        sprite.entry_turn = turn;
        load_traits(state, engine, rng, team, ai);
        fire_post_entry(state, engine, rng, team, ai);
        return "进化之力".into();
    }
    if item_name == "愿力" {
        let bl_skill = state.players[pi].team[ai].bloodline_skill.clone();
        let Some(mut new_skill) = bl_skill else { return String::new() };
        let has_slot = !state.players[pi].team[ai].skills.is_empty();
        if !has_slot {
            return String::new();
        }
        {
            let item = state.players[pi].item.as_mut().unwrap();
            item.uses += 1;
            item.last_use_turn = turn;
        }
        new_skill.id = 0;
        let old = state.players[pi].team[ai].skills[0].clone();
        let key = format!("{}:0", team);
        state.wish_restore.insert(key, old);
        let mut bs_new = BattleSkill::from_base(new_skill);
        bs_new.is_temporary = true;
        state.players[pi].team[ai].skills[0] = bs_new;
        return "愿力".into();
    }
    item_name
}

/// trait_loader._remove_direct_mods：按 direct_mod_tracked 回退旧的直改值。
fn remove_direct_mods(state: &mut BattleState, pi: usize, idx: usize) {
    let tracked = std::mem::take(&mut state.players[pi].team[idx].direct_mod_tracked);
    if tracked.is_empty() {
        return;
    }
    for bs in state.players[pi].team[idx].skills.iter_mut() {
        let name = bs.base.name.clone();
        if let Some(attrs) = tracked.get(&name) {
            for (attr, delta) in attrs {
                let cur = bs.modifiers.get(attr).copied().unwrap_or(0.0);
                bs.modifiers.insert(attr.clone(), cur - delta);
            }
        }
    }
}

/// trait 加载：ability → data/traits/{ability}.json → observer 注册 + 直改。
fn load_traits(state: &mut BattleState, engine: &mut VmEngine, rng: &mut PyRandom, team: &'static str, idx: usize) {    let pi = if team == "A" { 0 } else { 1 };
    let ability = state.players[pi].team[idx].species.ability.clone();
    if ability.is_empty() {
        return;
    }
    let path = engine.data_dir.join("traits").join(format!("{}.json", ability));
    let Ok(text) = std::fs::read_to_string(&path) else { return };
    let Ok(data) = serde_json::from_str::<J>(&text) else { return };
    let Some(effects) = data.get("effects").and_then(|x| x.as_array()) else { return };
    let trait_source = data
        .get("name")
        .and_then(|x| x.as_str())
        .unwrap_or(&ability)
        .to_string();

    engine.registry.unregister_by_owner((team, idx), "reload");
    // 去重：移除同 source 的 ObserverEffect/ModifierEffect（trait_loader 语义）
    {
        let sprite = &mut state.players[pi].team[idx];
        sprite.active_effects.retain(|e| {
            !(matches!(e.kind, EffectKind::Observer { .. } | EffectKind::Modifier { .. })
                && e.source == trait_source)
        });
    }

    let mut direct: Vec<J> = Vec::new();
    let mut active_effects_new: Vec<Effect> = Vec::new();
    for eff in effects {
        let op = eff.get("op").and_then(|x| x.as_str()).unwrap_or("");
        // effect_factory.from_dict：全部效果生成 EffectObject 副本
        if let Some(eff_obj) = effect_object_from_factory(eff, &trait_source) {
            active_effects_new.push(eff_obj);
        }
        match op {
            "observer" => {
                let Some(cond) = eff.get("cond") else { continue };
                let listen: Vec<String> = match eff.get("listen") {
                    Some(J::String(s)) => vec![s.clone()],
                    Some(J::Array(a)) => a
                        .iter()
                        .filter_map(|x| x.as_str().map(String::from))
                        .collect(),
                    _ => {
                        let s = crate::cond::infer_triggers(cond);
                        s.into_iter().collect()
                    }
                };
                let counter = eff.get("counter");
                let (name, threshold, reset_on_fire) = match counter.filter(|c| c.is_object()) {
                    Some(c) => (
                        c.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                        c.get("threshold").and_then(|x| x.as_i64()).unwrap_or(1),
                        c.get("reset").and_then(|x| x.as_bool()).unwrap_or(true),
                    ),
                    None => (String::new(), 1, true),
                };
                let then = eff.get("then").and_then(|x| x.as_array()).cloned().unwrap_or_default();
                engine.registry.register(Observer {
                    cond: cond.clone(),
                    then,
                    scope: eff
                        .get("scope")
                        .and_then(|x| x.as_str())
                        .unwrap_or("persistent")
                        .to_string(),
                    name,
                    source: trait_source.clone(),
                    listen,
                    threshold,
                    reset_on_fire,
                    owner: Some((team, idx)),
                    owner_skill_id: None,
                    hit_count: 0,
                    source_baked: false,
                });
            }
            _ => direct.push(eff.clone()),
        }
    }
    // effect_factory 副本入 active_effects（与 trait_loader 一致）
    for eff_obj in active_effects_new {
        state.players[pi].team[idx].add_effect(eff_obj);
    }
    // 直改 mods：_apply_direct_mods 直接写入技能 modifiers + 登记
    if !direct.is_empty() {
        // py load_for_sprite(apply_state=True)：先移除旧直改值再重放
        remove_direct_mods(state, pi, idx);
        apply_direct_mods(state, team, idx, &direct, 0);
        state.players[pi].team[idx].trait_direct_effects = direct;
        state.direct_mod_sprite_ids.insert((team.to_string(), idx));
    } else {
        remove_direct_mods(state, pi, idx);
        state.players[pi].team[idx].trait_direct_effects.clear();
        state.direct_mod_sprite_ids.remove(&(team.to_string(), idx));
    }
}
