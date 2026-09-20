//! mcts — AlphaZero 风格 MCTS 搜索（移植自 backend/engine/ai/core/mcts.py）。
//!
//! 逐位对齐要点：
//! - PUCT 打分在 numpy 2（NEP 50，Python float 为弱标量）下是 **float32**：
//!   `c_puct * prior[a] * sqrt_n / (1 + child_n)` 与 `q + u` 都按 f32 求值
//!   （q 从 f64 降到 f32），实测见 native/tools/probe_np_arith.py。
//! - 根噪声：prior[a] = f32( f32((1-rn)*prior[a]) + rn*noise[i] )，
//!   其中噪声来自 numpy legacy dirichlet（np_random.rs 逐位镜像）。
//! - 回滚：每轮仿真前克隆 (BattleState, VmEngine)，仿真后整体还原。与 py
//!   save_mutable_state 一致地**不还原 RNG**（随机序列跨仿真单调推进）；
//!   对局 RNG 在搜索结束时整体复原（py random.setstate 语义）。
//! - 终端价值用 battle_outcome_a（outcome.py 镜像），与训练标签同源。

use crate::battle_state::{BattleState, PlayerState};
use crate::engine::VmEngine;
use crate::mcts_actions::{bench_to_team_index, valid_actions_mask, valid_from_mask, NUM_ACTIONS};
use crate::np_random::NpRandom;
use crate::rng::PyRandom;
use crate::rule_agent::{Action, FirstAliveRepl, ReplacementPolicy, RuleAgent};
use crate::turn::{execute_turn_a_fixed_b_agent, execute_turn_fixed, MAX_TURNS};

pub const DEFAULT_DRAW_MARGIN: f64 = 0.15;
pub const DEFAULT_SELFPLAY_MAX_TURNS: i64 = 60;

#[derive(Clone, Debug)]
pub struct SearchCfg {
    pub num_simulations: usize,
    pub c_puct: f64,
    pub root_noise: f64,
    pub max_turns: i64,
    /// 对手动作取 argmax（评估/训练默认）而非按策略采样
    pub opp_greedy: bool,
    /// 对手 agent 温度（力竭换人采样用；动作采样固定 0.0/1.0，见 py _step_battle）
    pub opp_temperature: f64,
    pub draw_margin: f64,
    pub gamma: f64,
    pub tanh_k: f64,
    /// py use_network_opponent = isinstance(opponent_agent, NetworkPolicyAgent)
    pub use_network_opponent: bool,
    /// 叶节点批量评估大小（py leaf_batch_size；>1 走批量路径）
    pub leaf_batch_size: usize,
}

impl Default for SearchCfg {
    fn default() -> Self {
        SearchCfg {
            num_simulations: 200,
            c_puct: 2.0,
            root_noise: 0.25,
            max_turns: DEFAULT_SELFPLAY_MAX_TURNS,
            opp_greedy: false,
            opp_temperature: 1.0,
            draw_margin: DEFAULT_DRAW_MARGIN,
            gamma: 1.0,
            tanh_k: 0.0,
            use_network_opponent: true,
            leaf_batch_size: 1,
        }
    }
}

/// 评估接口：给定局面（Rust 侧自带编码）+ 合法动作掩码 + 视角，
/// 返回 (value, 掩码归一化后的策略)。Python 侧实现负责走批量推理队列。
pub trait SearchEvaluator {
    fn evaluate(
        &mut self,
        state: &BattleState,
        mask: &[f32; NUM_ACTIONS],
        perspective: &str,
    ) -> (f64, [f32; NUM_ACTIONS]);

    /// 批量叶评估（py evaluate_batch）。默认逐个 evaluate——
    /// 与 py UniformStub.evaluate_batch（逐状态堆叠）语义一致；
    /// 真实网络后端在阶段4-6覆写为一次批量推理。
    fn evaluate_batch(
        &mut self,
        states: &[&BattleState],
        masks: &[[f32; NUM_ACTIONS]],
        perspective: &str,
    ) -> (Vec<f64>, Vec<[f32; NUM_ACTIONS]>) {
        let mut values = Vec::with_capacity(states.len());
        let mut priors = Vec::with_capacity(states.len());
        for (st, m) in states.iter().zip(masks.iter()) {
            let (v, p) = self.evaluate(st, m, perspective);
            values.push(v);
            priors.push(p);
        }
        (values, priors)
    }
}

// ═══════════════════════════════════════════════════════════════════
// 数值助手（numpy 语义镜像）
// ═══════════════════════════════════════════════════════════════════

/// numpy float32 求和（pairwise_sum 模板：n<8 顺序累加，否则 8 路累加器）。
/// 仅用于 float32 概率数组的 `.sum()`（temperature==1.0 采样路径）。
pub(crate) fn np_sum_f32(a: &[f32]) -> f32 {
    let n = a.len();
    if n < 8 {
        let mut res = 0f32;
        for &v in a {
            res += v;
        }
        return res;
    }
    let mut r = [0f32; 8];
    r.copy_from_slice(&a[..8]);
    let mut i = 8usize;
    let stop = n - (n % 8);
    while i < stop {
        for k in 0..8 {
            r[k] += a[i + k];
        }
        i += 8;
    }
    let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    while i < n {
        res += a[i];
        i += 1;
    }
    res
}

/// numpy float64 求和（同上模板）。
fn np_sum_f64(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0f64;
        for &v in a {
            res += v;
        }
        return res;
    }
    let mut r = [0f64; 8];
    r.copy_from_slice(&a[..8]);
    let mut i = 8usize;
    let stop = n - (n % 8);
    while i < stop {
        for k in 0..8 {
            r[k] += a[i + k];
        }
        i += 8;
    }
    let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    while i < n {
        res += a[i];
        i += 1;
    }
    res
}

pub fn gather_action() -> Action {
    Action { kind: "gather", skill_index: None, switch_index: None }
}

/// py mask / max(mask.sum(), 1.0)（float32 逐元素除法）。
pub fn normalize_mask(mask: &[f32; NUM_ACTIONS]) -> [f32; NUM_ACTIONS] {
    let s = np_sum_f32(mask);
    let denom: f32 = if s > 1.0 { s } else { 1.0 };
    let mut out = [0f32; NUM_ACTIONS];
    for i in 0..NUM_ACTIONS {
        out[i] = mask[i] / denom;
    }
    out
}

/// py _sample_action 精确镜像：
/// ① probs.sum()（f32 成对求和）<= 0 → -1；
/// ② greedy / temperature < 1e-8 → np.argmax（并列取最小下标）；
/// ③ 温度 ≠ 1.0 时 probs = probs ** (1.0/max(t,1e-8))（numpy f32 幂）；
/// ④ s = probs.sum()（f32 成对）→ probs /= s（f32 逐元素除）；
/// ⑤ np.random.choice(len, p=probs)：p 转 f64 → cdf = f64 顺序 cumsum →
///    cdf /= cdf[-1] → u = random_sample()（f64）→
///    idx = #{ i: cdf[i]/cdf[-1] <= u }（searchsorted right，末位钳位）。
pub fn policy_select_idx(
    probs: &[f32; NUM_ACTIONS],
    temperature: f64,
    greedy: bool,
    np_rng: &mut NpRandom,
) -> i64 {
    if np_sum_f32(probs) <= 0.0 {
        return -1;
    }
    if greedy || temperature < 1e-8 {
        let mut best = 0usize;
        for i in 1..NUM_ACTIONS {
            if probs[i] > probs[best] {
                best = i;
            }
        }
        return best as i64;
    }
    let mut p = [0f32; NUM_ACTIONS];
    if temperature == 1.0 {
        p.copy_from_slice(probs);
    } else {
        let inv = (1.0f64 / temperature.max(1e-8)) as f32;
        for (i, &x) in probs.iter().enumerate() {
            p[i] = x.powf(inv);
        }
    }
    let s = np_sum_f32(&p);
    if s <= 0.0 {
        return -1;
    }
    for x in p.iter_mut() {
        *x /= s;
    }
    let mut cdf = [0f64; NUM_ACTIONS];
    let mut acc = 0f64;
    for i in 0..NUM_ACTIONS {
        acc += p[i] as f64;
        cdf[i] = acc;
    }
    let total = cdf[NUM_ACTIONS - 1];
    let u: f64 = np_rng.random();
    let mut idx = 0usize;
    for i in 0..NUM_ACTIONS {
        if cdf[i] / total <= u {
            idx = i + 1;
        } else {
            break;
        }
    }
    if idx >= NUM_ACTIONS {
        idx = NUM_ACTIONS - 1;
    }
    if np_trace_enabled() {
        let nz: Vec<String> = probs
            .iter()
            .enumerate()
            .filter(|(_, &v)| v != 0.0)
            .map(|(i, &v)| format!("({i},{v:.6})"))
            .collect();
        eprintln!(
            "[rust-choice] u={u:.17e} idx={idx} total={total:.17e} nz=[{}]",
            nz.join(",")
        );
    }
    idx as i64
}

/// 调试开关：ROCO_NP_TRACE=1 时向 stderr 输出 np 随机事件。
pub(crate) fn np_trace_enabled() -> bool {
    use std::sync::OnceLock;
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("ROCO_NP_TRACE").is_ok())
}

/// py action_index_to_action（0-16 → Action；换宠映射失败返回 None）。
pub fn action_index_to_action(state: &BattleState, team: &str, action_idx: usize) -> Option<Action> {
    let pi = if team == "A" { 0 } else { 1 };
    if action_idx < 10 {
        return Some(Action { kind: "skill", skill_index: Some(action_idx), switch_index: None });
    }
    if action_idx < 15 {
        let bench_slot = action_idx - 10;
        let p = &state.players[pi];
        let team_idx = bench_to_team_index(p.active_index, bench_slot, p.team.len())?;
        return Some(Action { kind: "switch", skill_index: None, switch_index: Some(team_idx) });
    }
    match action_idx {
        15 => Some(gather_action()),
        16 => Some(Action { kind: "item", skill_index: None, switch_index: None }),
        _ => None,
    }
}

/// py _replacement_mask：板凳槽 10-14 的掩码 + 存活替补索引列表。
pub fn replacement_mask(state: &BattleState, team: &str) -> (Vec<usize>, [f32; NUM_ACTIONS]) {
    let pi = if team == "A" { 0 } else { 1 };
    let p = &state.players[pi];
    let mut alive: Vec<usize> = Vec::new();
    let mut mask = [0f32; NUM_ACTIONS];
    let mut bench_slot = 0usize;
    for (i, s) in p.team.iter().enumerate() {
        if i == p.active_index {
            continue;
        }
        if bench_slot < 5 {
            if !s.is_fainted() {
                alive.push(i);
                mask[10 + bench_slot] = 1.0;
            }
            bench_slot += 1;
        }
    }
    (alive, mask)
}

// ═══════════════════════════════════════════════════════════════════
// 终局价值（outcome.py 镜像）
// ═══════════════════════════════════════════════════════════════════

/// py team_battle_score：存活数 + 队伍 HP 比例 + 魔力 + 在场能量。
pub fn team_battle_score(p: &PlayerState) -> f64 {
    let alive = p.alive_sprites().len() as f64;
    let hp_cur: i64 = p.team.iter().map(|s| s.current_hp).sum();
    let hp_max: i64 = p.team.iter().map(|s| s.max_hp.max(1)).sum();
    let hp_ratio = if hp_max > 0 { hp_cur as f64 / hp_max as f64 } else { 0.0 };
    let active_energy = p.team.get(p.active_index).map(|s| s.energy as f64 / 10.0).unwrap_or(0.0);
    let lives = p.lives.max(0) as f64;
    alive * 1.0 + hp_ratio * 0.5 + lives * 0.25 + active_energy * 0.05
}

/// py battle_outcome_a：+1=A 胜，-1=B 胜，0=平（含平局分差阈值与回合衰减）。
pub fn battle_outcome_a(
    state: &BattleState,
    max_turns: i64,
    draw_margin: f64,
    gamma: f64,
    tanh_k: f64,
) -> f64 {
    let decay = |raw: f64| if gamma < 1.0 { raw * gamma.powf(state.turn as f64) } else { raw };
    match state.winner.as_deref() {
        Some("A") => decay(1.0),
        Some("B") => decay(-1.0),
        _ => {
            let margin = team_battle_score(&state.players[0]) - team_battle_score(&state.players[1]);
            let _ = max_turns; // py 仅用于 end_reason 前缀
            if margin.abs() < draw_margin {
                return 0.0;
            }
            let raw = if tanh_k > 0.0 {
                (tanh_k * margin).tanh()
            } else if margin > 0.0 {
                1.0
            } else {
                -1.0
            };
            decay(raw)
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// 树节点（arena 存储，避免自引用借用问题）
// ═══════════════════════════════════════════════════════════════════

struct Node {
    visit_count: u32,
    total_value: f64,
    prior: [f32; NUM_ACTIONS],
    children: [Option<usize>; NUM_ACTIONS],
    valid_actions: Vec<usize>,
    /// 本节点对手策略（py MCTSNode.opp_policy，省去 _step_battle 重复推理）
    opp_policy: Option<[f32; NUM_ACTIONS]>,
}

impl Node {
    fn blank() -> Self {
        Node {
            visit_count: 0,
            total_value: 0.0,
            prior: [0f32; NUM_ACTIONS],
            children: [None; NUM_ACTIONS],
            valid_actions: Vec::new(),
            opp_policy: None,
        }
    }

    fn with_prior(valid_actions: Vec<usize>, prior: [f32; NUM_ACTIONS]) -> Self {
        let mut n = Node::blank();
        n.valid_actions = valid_actions;
        n.prior = prior;
        n
    }

    fn value(&self) -> f64 {
        if self.visit_count == 0 {
            0.0
        } else {
            self.total_value / self.visit_count as f64
        }
    }

    fn has_children(&self) -> bool {
        !self.valid_actions.is_empty()
    }
}

struct Arena {
    nodes: Vec<Node>,
}

impl Arena {
    fn new() -> Self {
        Arena { nodes: Vec::new() }
    }
    fn push(&mut self, n: Node) -> usize {
        self.nodes.push(n);
        self.nodes.len() - 1
    }
}

/// 力竭换人：py _choose_policy_replacement（网络策略头 + 兜底）。
pub(crate) struct EvalRepl<'a> {
    evaluator: &'a mut dyn SearchEvaluator,
    np_rng: &'a mut NpRandom,
    temperature: f64,
    greedy: bool,
}

impl<'a> EvalRepl<'a> {
    pub(crate) fn new(
        evaluator: &'a mut dyn SearchEvaluator,
        np_rng: &'a mut NpRandom,
        temperature: f64,
        greedy: bool,
    ) -> Self {
        EvalRepl {
            evaluator,
            np_rng,
            temperature,
            greedy,
        }
    }
}

impl ReplacementPolicy for EvalRepl<'_> {
    fn choose_replacement(&mut self, state: &BattleState, team: &str) -> i64 {
        let (alive, mask) = replacement_mask(state, team);
        if alive.is_empty() {
            return -1;
        }
        let (_, probs) = self.evaluator.evaluate(state, &mask, team);
        let action_idx = policy_select_idx(&probs, self.temperature, self.greedy, self.np_rng);
        if (10..15).contains(&action_idx) {
            let pi = if team == "A" { 0 } else { 1 };
            let p = &state.players[pi];
            if let Some(t) = bench_to_team_index(p.active_index, (action_idx - 10) as usize, p.team.len())
            {
                if alive.contains(&t) {
                    return t as i64;
                }
            }
        }
        // 兜底：板凳槽中概率最大者，否则首只存活
        let pi = if team == "A" { 0 } else { 1 };
        let p = &state.players[pi];
        let mut best_idx: i64 = -1;
        let mut best_score = -1.0f64;
        let mut bench_slot = 0usize;
        for (i, s) in p.team.iter().enumerate() {
            if i == p.active_index {
                continue;
            }
            if bench_slot < 5 {
                if !s.is_fainted() && (probs[10 + bench_slot] as f64) > best_score {
                    best_score = probs[10 + bench_slot] as f64;
                    best_idx = i as i64;
                }
                bench_slot += 1;
            }
        }
        if best_idx >= 0 {
            best_idx
        } else {
            alive[0] as i64
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// 仿真步进
// ═══════════════════════════════════════════════════════════════════

/// py _step_battle：A 按 action_idx 行动，B 由 opp_policy 采样或 RuleAgent 现选。
/// 返回 false 表示 action_idx 无法转为有效动作（调用方跳过本次回退）。
#[allow(clippy::too_many_arguments)]
fn step_battle(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    opp_rule: Option<&RuleAgent>,
    cfg: &SearchCfg,
    action_idx: usize,
    opp_policy: Option<&[f32; NUM_ACTIONS]>,
    out_b: &mut i64,
) -> bool {
    let Some(action_a) = action_index_to_action(state, "A", action_idx) else {
        return false;
    };
    if let Some(pol) = opp_policy {
        let opp_idx = policy_select_idx(
            pol,
            if cfg.opp_greedy { 0.0 } else { 1.0 },
            cfg.opp_greedy,
            np_rng,
        );
        *out_b = opp_idx;
        let action_b = if opp_idx < 0 {
            gather_action()
        } else {
            action_index_to_action(state, "B", opp_idx as usize).unwrap_or_else(gather_action)
        };
        let mut repl = EvalRepl {
            evaluator: &mut *evaluator,
            np_rng: &mut *np_rng,
            temperature: cfg.opp_temperature,
            greedy: cfg.opp_greedy,
        };
        execute_turn_fixed(state, engine, rng, action_a, action_b, &mut repl);
        true
    } else {
        let Some(rule) = opp_rule else {
            // 非网络对手但未提供规则代理：按 py 兜底语义当作聚能（不应发生）
            let mut repl = FirstAliveRepl;
            execute_turn_fixed(state, engine, rng, action_a, gather_action(), &mut repl);
            return true;
        };
        let mut repl = FirstAliveRepl;
        execute_turn_a_fixed_b_agent(state, engine, rng, action_a, rule, &mut repl);
        true
    }
}

/// 单步记录（对拍定位用）：`{"b": 对手动作索引, "mti"/"np": 两条 RNG 游标,
/// "d": 状态摘要}`。RNG 游标让「动作一致但随机数消耗不同」与「逻辑分歧」
/// 可直接区分——前者 mti/np 先分叉，后者游标相同而 d 分叉。
fn step_record(
    state: &BattleState,
    rng: &PyRandom,
    np_rng: &NpRandom,
    opp_idx: i64,
) -> serde_json::Value {
    let mut rec = serde_json::Map::new();
    rec.insert("b".to_string(), serde_json::json!(opp_idx));
    rec.insert("mti".to_string(), serde_json::json!(rng.mti()));
    rec.insert("np".to_string(), serde_json::json!(np_rng.pos()));
    rec.insert("d".to_string(), crate::turn::state_digest(state));
    serde_json::Value::Object(rec)
}

// ═══════════════════════════════════════════════════════════════════
// 搜索主循环
// ═══════════════════════════════════════════════════════════════════

pub fn mcts_search(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    opp_rule: Option<&RuleAgent>,
    cfg: &SearchCfg,
) -> [f32; NUM_ACTIONS] {
    let mut trace = Vec::new();
    let (probs, _counts) = mcts_search_traced(
        state, engine, rng, np_rng, evaluator, opp_rule, cfg, &mut trace, None,
    );
    probs
}

/// mcts_search + 每轮仿真走过的动作轨迹（对拍定位用；trace[i] = 第 i 轮
/// 实际步进的动作索引序列，步进失败/立即终端则为空或截断序列）。
/// 返回 (动作概率, 根节点各动作访问次数)。
/// 搜索序号（ROCO_NP_TRACE/ROCO_SIM_DIGEST 对拍定位用）。
static SIM_TRACE_K: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

#[allow(clippy::too_many_arguments)]
pub fn mcts_search_traced(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    opp_rule: Option<&RuleAgent>,
    cfg: &SearchCfg,
    trace: &mut Vec<Vec<usize>>,
    digest_trace: Option<&mut Vec<Vec<serde_json::Value>>>,
) -> ([f32; NUM_ACTIONS], Vec<u32>) {
    // py random.setstate(real_rng_state)：搜索推进对局 RNG，返回前整体复原
    let rng_saved = rng.clone();
    // py mcts_search：battle._mcts_sim = True（仿真期间跳过 UI 显示效果）
    let mcts_sim_saved = state.mcts_sim;
    state.mcts_sim = true;
    let k = SIM_TRACE_K.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let digest_k = std::env::var("ROCO_SIM_DIGEST")
        .ok()
        .and_then(|v| v.parse::<usize>().ok());
    let want_digest = digest_k == Some(k);
    let mut local_dt: Vec<Vec<serde_json::Value>> = Vec::new();
    let dt: Option<&mut Vec<Vec<serde_json::Value>>> = if want_digest {
        Some(&mut local_dt)
    } else {
        digest_trace
    };
    let out = mcts_search_inner(
        state, engine, rng, np_rng, evaluator, opp_rule, cfg, trace, dt,
    );
    state.mcts_sim = mcts_sim_saved;
    *rng = rng_saved;
    if np_trace_enabled() {
        let flat: Vec<String> = trace
            .iter()
            .map(|p| p.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(","))
            .collect();
        eprintln!("[rust-sims] k={} paths={}", k, flat.join(";"));
    }
    if want_digest {
        for (i, steps) in local_dt.iter().enumerate() {
            for (j, d) in steps.iter().enumerate() {
                eprintln!("[rust-digest] k={} sim={} step={} {}", k, i, j, d);
            }
        }
    }
    out
}

#[allow(clippy::too_many_arguments)]
fn mcts_search_inner(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    opp_rule: Option<&RuleAgent>,
    cfg: &SearchCfg,
    trace: &mut Vec<Vec<usize>>,
    digest_trace: Option<&mut Vec<Vec<serde_json::Value>>>,
) -> ([f32; NUM_ACTIONS], Vec<u32>) {
    let mut digest_trace = digest_trace;
    let net_opp = cfg.use_network_opponent;

    let mask = valid_actions_mask(state, "A");
    let valid = valid_from_mask(&mask);
    if valid.is_empty() {
        return (normalize_mask(&mask), vec![0u32; NUM_ACTIONS]);
    }

    // ── 根节点先验（A 视角编码由 evaluator 内部完成） ──
    let mut prior: [f32; NUM_ACTIONS];
    let mut root_opp_prior: Option<[f32; NUM_ACTIONS]> = None;
    let mut opp_valid_empty = false;
    if net_opp {
        let opp_mask = valid_actions_mask(state, "B");
        let opp_valid = valid_from_mask(&opp_mask);
        let (_, p_a) = evaluator.evaluate(state, &mask, "A");
        prior = p_a;
        if !opp_valid.is_empty() {
            let (_, p_b) = evaluator.evaluate(state, &opp_mask, "B");
            root_opp_prior = Some(p_b);
        } else {
            opp_valid_empty = true;
            root_opp_prior = Some(normalize_mask(&opp_mask));
        }
    } else {
        let (_, p_a) = evaluator.evaluate(state, &mask, "A");
        prior = p_a;
    }

    // ── Dirichlet 根噪声 ──
    if cfg.root_noise > 0.0 {
        let alpha = vec![0.3f64; valid.len()];
        let noise = np_rng.dirichlet(&alpha);
        if np_trace_enabled() {
            eprintln!("[rust-dir] k={} n0={:.17e}", valid.len(), noise[0]);
        }
        let rn = cfg.root_noise;
        for (i, &a) in valid.iter().enumerate() {
            let t1 = (1.0 - rn) as f32 * prior[a];
            let mixed = t1 as f64 + rn * noise[i];
            prior[a] = mixed as f32;
        }
    }

    // ── 建树 ──
    let mut arena = Arena::new();
    let root = arena.push(Node::with_prior(valid.clone(), prior));
    for &a in &valid {
        let child = arena.push(Node::blank());
        arena.nodes[root].children[a] = Some(child);
    }
    if net_opp {
        if opp_valid_empty {
            let opp_mask = valid_actions_mask(state, "B");
            arena.nodes[root].opp_policy = Some(normalize_mask(&opp_mask));
        } else {
            arena.nodes[root].opp_policy = root_opp_prior;
        }
    }

    // ── 主循环：leaf_batch_size>1 走批量叶评估路径（py mcts_search 批量分支镜像），
    //    否则单仿真路径（py 默认分支） ──
    if cfg.leaf_batch_size > 1 {
        mcts_search_batched(
            state,
            engine,
            rng,
            np_rng,
            evaluator,
            opp_rule,
            cfg,
            &mut arena,
            root,
            net_opp,
            trace,
            digest_trace,
        );
    } else {
        for _ in 0..cfg.num_simulations {
            let saved_state = state.clone();
            let saved_engine = engine.clone();

            let mut sim_actions: Vec<usize> = Vec::new();
            let mut sim_digests: Vec<serde_json::Value> = Vec::new();
            let (leaf_node, path) = selection_descent(
                &mut arena,
                state,
                engine,
                rng,
                np_rng,
                evaluator,
                opp_rule,
                cfg,
                root,
                net_opp,
                digest_trace.is_some(),
                &mut sim_actions,
                &mut sim_digests,
            );

            let Some(node) = leaf_node else {
                // py step_ok=False：跳过本次仿真的回传
                trace.push(sim_actions);
                if let Some(dt) = digest_trace.as_deref_mut() {
                    dt.push(sim_digests);
                }
                *state = saved_state;
                state.invalidate_all_stat_caches();
                *engine = saved_engine;
                continue;
            };

            // Expansion & Evaluation
            let sim_mask = valid_actions_mask(state, "A");
            let sim_valid = valid_from_mask(&sim_mask);
            let leaf_value: f64;
            if !sim_valid.is_empty()
                && state.winner.is_none()
                && state.turn < MAX_TURNS
                && state.turn < cfg.max_turns
            {
                let (value, sim_prior) = evaluator.evaluate(state, &sim_mask, "A");
                leaf_value = value;
                let mut node_opp_policy: Option<[f32; NUM_ACTIONS]> = None;
                if net_opp {
                    let opp_mask = valid_actions_mask(state, "B");
                    let opp_valid = valid_from_mask(&opp_mask);
                    if !opp_valid.is_empty() {
                        let (_, p_b) = evaluator.evaluate(state, &opp_mask, "B");
                        node_opp_policy = Some(p_b);
                    } else {
                        node_opp_policy = Some(normalize_mask(&opp_mask));
                    }
                }
                arena.nodes[node].valid_actions = sim_valid;
                arena.nodes[node].prior = sim_prior;
                if net_opp {
                    arena.nodes[node].opp_policy = node_opp_policy;
                }
                let va = arena.nodes[node].valid_actions.clone();
                for a in va {
                    let child = arena.push(Node::blank());
                    arena.nodes[node].children[a] = Some(child);
                }
            } else {
                leaf_value = battle_outcome_a(state, cfg.max_turns, cfg.draw_margin, cfg.gamma, cfg.tanh_k);
            }

            // Backprop
            for &(parent, _) in path.iter().rev() {
                arena.nodes[parent].visit_count += 1;
                arena.nodes[parent].total_value += leaf_value;
            }
            arena.nodes[node].visit_count += 1;
            arena.nodes[node].total_value += leaf_value;

            // 回滚（py restore_mutable_state）：restore 尾部对全部精灵
            // _invalidate_stat_cache（battle.py:493-497）——克隆还原会把旧缓存
            // 一起带回来，必须显式清空（萌化换形态后旧值才会被重算，spec_0141）
            *state = saved_state;
            state.invalidate_all_stat_caches();
            *engine = saved_engine;
            trace.push(sim_actions);
            if let Some(dt) = digest_trace.as_deref_mut() {
                dt.push(sim_digests);
            }
        }
    }

    // ── 输出动作概率（∝ 根节点子访问次数） ──
    let mut counts = [0f32; NUM_ACTIONS];
    let mut raw_counts = vec![0u32; NUM_ACTIONS];
    for &a in &arena.nodes[root].valid_actions {
        if let Some(child) = arena.nodes[root].children[a] {
            counts[a] = arena.nodes[child].visit_count as f32;
            raw_counts[a] = arena.nodes[child].visit_count;
        }
    }
    let total = np_sum_f32(&counts);
    if total > 0.0 {
        let mut out = [0f32; NUM_ACTIONS];
        for i in 0..NUM_ACTIONS {
            out[i] = counts[i] / total;
        }
        return (out, raw_counts);
    }
    (normalize_mask(&mask), raw_counts)
}

/// selection 下降（单仿真/批量两路共用）：PUCT 选路 + step_battle 推进 +
/// 终端守卫，逐行镜像 py 主循环的 while node.has_children 段。
/// 返回 (叶节点, 路径)；叶节点为 None 表示 step_battle 失败（py step_ok=False）。
#[allow(clippy::too_many_arguments)]
fn selection_descent(
    arena: &mut Arena,
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    opp_rule: Option<&RuleAgent>,
    cfg: &SearchCfg,
    root: usize,
    net_opp: bool,
    want_digest: bool,
    sim_actions: &mut Vec<usize>,
    sim_digests: &mut Vec<serde_json::Value>,
) -> (Option<usize>, Vec<(usize, usize)>) {
    let mut node = root;
    let mut path: Vec<(usize, usize)> = Vec::new();

    while arena.nodes[node].has_children() {
        let mut best_a: i64 = -1;
        let mut best_score: f32 = -1e9f32;
        let sqrt_n = ((arena.nodes[node].visit_count + 1) as f64).sqrt();
        let n_valid = arena.nodes[node].valid_actions.len();
        for k in 0..n_valid {
            let a = arena.nodes[node].valid_actions[k];
            let Some(child) = arena.nodes[node].children[a] else { continue };
            let q = arena.nodes[child].value();
            let u = (cfg.c_puct as f32) * arena.nodes[node].prior[a] * (sqrt_n as f32)
                / ((1 + arena.nodes[child].visit_count) as f32);
            let score = (q as f32) + u;
            if score > best_score {
                best_score = score;
                best_a = a as i64;
            }
        }
        if best_a < 0 {
            break;
        }
        // 终端守卫（py：is_finished or active 力竭 or turn >= max_turns）
        if state.winner.is_some()
            || state.turn >= MAX_TURNS
            || state.players[0].team[state.players[0].active_index].is_fainted()
            || state.turn >= cfg.max_turns
        {
            break;
        }
        let ai = best_a as usize;
        let parent = node;
        path.push((parent, ai));
        sim_actions.push(ai);
        node = arena.nodes[parent].children[ai].expect("子节点空壳缺失");
        let opp_policy = if net_opp { arena.nodes[parent].opp_policy } else { None };
        let mut opp_idx: i64 = -1;
        if !step_battle(
            state,
            engine,
            rng,
            np_rng,
            evaluator,
            opp_rule,
            cfg,
            ai,
            opp_policy.as_ref(),
            &mut opp_idx,
        ) {
            return (None, path);
        }
        if want_digest {
            sim_digests.push(step_record(state, rng, np_rng, opp_idx));
        }
    }
    (Some(node), path)
}

/// 批量叶评估路径（py mcts_search 的 leaf_batch_size>1 分支逐行镜像）：
/// - 收集期：非终局叶只累加 visit_count（value/prior 留待整批回填），
///   终局叶立即完整回传（counts+value）；每叶收集后立即回滚；
/// - 整批收集完：一次 evaluate_batch（A 视角）+ 一次对手批量（B 视角），
///   再回填 prior/子壳/total_value——批内后选的叶子看不到批内先到的
///   评估结果，树形状与单仿真路径不同（py 语义即如此）。
#[allow(clippy::too_many_arguments)]
fn mcts_search_batched(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    opp_rule: Option<&RuleAgent>,
    cfg: &SearchCfg,
    arena: &mut Arena,
    root: usize,
    net_opp: bool,
    trace: &mut Vec<Vec<usize>>,
    digest_trace: Option<&mut Vec<Vec<serde_json::Value>>>,
) {
    let mut digest_trace = digest_trace;
    let batch_size = cfg.leaf_batch_size.max(1);
    let mut simulations_left = cfg.num_simulations;

    struct Pending {
        state: BattleState,
        sim_valid: Vec<usize>,
        node: usize,
        path: Vec<(usize, usize)>,
        /// Some = 有对手批次槽位；None = 对手无合法动作（收集期即写 opp_policy）
        opp_mask: Option<[f32; NUM_ACTIONS]>,
    }

    while simulations_left > 0 {
        let mut pending: Vec<Pending> = Vec::new();

        // ── 收集期（py 内层 while simulations_left>0 and len(pending)<batch_size）──
        while simulations_left > 0 && pending.len() < batch_size {
            simulations_left -= 1;
            let saved_state = state.clone();
            let saved_engine = engine.clone();

            let mut sim_actions: Vec<usize> = Vec::new();
            let mut sim_digests: Vec<serde_json::Value> = Vec::new();
            let (leaf_node, path) = selection_descent(
                arena,
                state,
                engine,
                rng,
                np_rng,
                evaluator,
                opp_rule,
                cfg,
                root,
                net_opp,
                digest_trace.is_some(),
                &mut sim_actions,
                &mut sim_digests,
            );

            if let Some(node) = leaf_node {
                let sim_mask = valid_actions_mask(state, "A");
                let sim_valid = valid_from_mask(&sim_mask);
                if !sim_valid.is_empty()
                    && state.winner.is_none()
                    && state.turn < MAX_TURNS
                    && state.turn < cfg.max_turns
                {
                    // py：收集期只加 visit_count（正序 path），value/prior 待批量
                    for &(parent, _) in &path {
                        arena.nodes[parent].visit_count += 1;
                    }
                    arena.nodes[node].visit_count += 1;
                    let mut opp_mask_opt: Option<[f32; NUM_ACTIONS]> = None;
                    if net_opp {
                        let opp_mask = valid_actions_mask(state, "B");
                        if valid_from_mask(&opp_mask).is_empty() {
                            arena.nodes[node].opp_policy = Some(normalize_mask(&opp_mask));
                        } else {
                            opp_mask_opt = Some(opp_mask);
                        }
                    }
                    pending.push(Pending {
                        state: state.clone(),
                        sim_valid,
                        node,
                        path,
                        opp_mask: opp_mask_opt,
                    });
                } else {
                    // 终局叶：立即完整回传（py battle_outcome_a + reversed(path)）
                    let v = battle_outcome_a(state, cfg.max_turns, cfg.draw_margin, cfg.gamma, cfg.tanh_k);
                    for &(parent, _) in path.iter().rev() {
                        arena.nodes[parent].visit_count += 1;
                        arena.nodes[parent].total_value += v;
                    }
                    arena.nodes[node].visit_count += 1;
                    arena.nodes[node].total_value += v;
                }
            }
            // py finally: restore——逐叶回滚；trace/digest 照记录顺序落盘
            *state = saved_state;
            state.invalidate_all_stat_caches();
            *engine = saved_engine;
            trace.push(sim_actions);
            if let Some(dt) = digest_trace.as_deref_mut() {
                dt.push(sim_digests);
            }
        }

        if pending.is_empty() {
            continue;
        }

        // ── 整批一次评估（py batch_eval + opponent evaluate_policy_batch）──
        let a_refs: Vec<&BattleState> = pending.iter().map(|p| &p.state).collect();
        let a_masks: Vec<[f32; NUM_ACTIONS]> = {
            let sim_masks: Vec<[f32; NUM_ACTIONS]> = pending
                .iter()
                .map(|p| valid_actions_mask(&p.state, "A"))
                .collect();
            sim_masks
        };
        let (values, priors) = evaluator.evaluate_batch(&a_refs, &a_masks, "A");

        let opp_slots: Vec<usize> = pending
            .iter()
            .enumerate()
            .filter(|(_, p)| p.opp_mask.is_some())
            .map(|(i, _)| i)
            .collect();
        let opp_priors: Option<(Vec<f64>, Vec<[f32; NUM_ACTIONS]>)> = if opp_slots.is_empty() {
            None
        } else {
            let refs: Vec<&BattleState> = opp_slots.iter().map(|&i| &pending[i].state).collect();
            let masks: Vec<[f32; NUM_ACTIONS]> = opp_slots
                .iter()
                .map(|&i| pending[i].opp_mask.expect("opp 槽位缺掩码"))
                .collect();
            Some(evaluator.evaluate_batch(&refs, &masks, "B"))
        };

        // ── 回填（py for node, path, sim_valid, leaf_idx, opp_idx in pending_meta）──
        let mut opp_k = 0usize;
        for (k, p) in pending.iter().enumerate() {
            let node_i = p.node;
            {
                let nd = &mut arena.nodes[node_i];
                nd.valid_actions = p.sim_valid.clone();
                nd.prior = priors[k];
                if p.opp_mask.is_some() {
                    if let Some((_, op)) = &opp_priors {
                        nd.opp_policy = Some(op[opp_k]);
                    }
                }
            }
            if p.opp_mask.is_some() {
                opp_k += 1;
            }
            // 子壳空架在回填期才铺（批内后选叶子因此不会降入先到叶子的子树）
            for &a in &p.sim_valid {
                let child = arena.push(Node::blank());
                arena.nodes[node_i].children[a] = Some(child);
            }
            let v = values[k];
            for &(parent, _) in p.path.iter().rev() {
                arena.nodes[parent].total_value += v;
            }
            arena.nodes[node_i].total_value += v;
        }
    }
}
