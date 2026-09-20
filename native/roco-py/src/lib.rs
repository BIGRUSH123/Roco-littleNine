//! roco-py — roco-core 的 PyO3 绑定（扩展模块名 roco_engine）。
//! Python 侧经 backend/engine/native_loader.py 导入，失败自动回退纯 Python。
//! 带 `py_` 前缀的函数用于 Python↔Rust 差异对拍测试。

use pyo3::prelude::*;
use roco_core::damage::calc_damage;
use roco_core::rng::PyRandom;

#[pyfunction]
fn engine_info() -> String {
    "roco-engine native (Rust) v0.2.0".to_string()
}

#[pyfunction]
fn add(a: i64, b: i64) -> i64 {
    a + b
}

// ── RNG 对拍 ──

#[pyfunction]
fn py_random_stream(seed: i64, n: usize) -> Vec<f64> {
    let mut rng = PyRandom::seed_i64(seed);
    (0..n).map(|_| rng.random()).collect()
}

#[pyfunction]
fn py_choice_stream(seed: i64, items: Vec<String>, n: usize) -> Vec<String> {
    let mut rng = PyRandom::seed_i64(seed);
    (0..n)
        .map(|_| rng.choice(&items).clone())
        .collect()
}

/// population 用索引 0..n_pop 表示，避免跨 FFI 拷贝任意对象。
#[pyfunction]
fn py_sample_indices(seed: i64, n_pop: usize, k: usize, repeats: usize) -> Vec<Vec<i64>> {
    let mut rng = PyRandom::seed_i64(seed);
    let pop: Vec<i64> = (0..n_pop as i64).collect();
    (0..repeats).map(|_| rng.sample(&pop, k)).collect()
}

#[pyfunction]
fn py_randbelow_stream(seed: i64, n: usize, count: usize) -> Vec<i64> {
    let mut rng = PyRandom::seed_i64(seed);
    (0..count).map(|_| rng.randbelow(n) as i64).collect()
}

#[pyfunction]
fn py_shuffle_indices(seed: i64, n: usize) -> Vec<i64> {
    let mut rng = PyRandom::seed_i64(seed);
    let mut v: Vec<i64> = (0..n as i64).collect();
    rng.shuffle(&mut v);
    v
}

#[pyfunction]
fn py_genrand_u32_stream(seed: i64, n: usize) -> Vec<u32> {
    let mut rng = PyRandom::seed_i64(seed);
    (0..n).map(|_| rng.genrand_u32()).collect()
}

// ── resolve / formula 对拍 ──

/// ctx 以 JSON（serde 序列化的 vm_ctx::Ctx）传入，value 为任意 JSON 值。
#[pyfunction]
fn py_resolve(ctx_json: &str, value_json: &str) -> Option<String> {
    let ctx: roco_core::vm_ctx::Ctx = serde_json::from_str(ctx_json).ok()?;
    let value: serde_json::Value = serde_json::from_str(value_json).ok()?;
    roco_core::resolve::resolve(&ctx, &value).map(|v| v.to_json().to_string())
}

#[pyfunction]
fn py_resolve_formula(ctx_json: &str, expr: &str) -> Option<String> {
    let ctx: roco_core::vm_ctx::Ctx = serde_json::from_str(ctx_json).ok()?;
    let v = roco_core::formula::resolve_formula_string(&ctx, expr);
    Some(v.to_json().to_string())
}

// ── cond 求值对拍 ──

/// 三态返回："true" | "false" | "error"（error 对应 Python 异常 → 跳过观察者）。
#[pyfunction]
fn py_eval_cond(ctx_json: &str, cond_json: &str) -> String {
    let ctx: roco_core::vm_ctx::Ctx = match serde_json::from_str(ctx_json) {
        Ok(c) => c,
        Err(_) => return "error".into(),
    };
    let cond: serde_json::Value = match serde_json::from_str(cond_json) {
        Ok(c) => c,
        Err(_) => return "error".into(),
    };
    match roco_core::cond::eval_one(&ctx, &cond) {
        Ok(true) => "true".into(),
        Ok(false) => "false".into(),
        Err(_) => "error".into(),
    }
}

#[pyfunction]
fn py_infer_triggers(cond_json: &str) -> Vec<String> {
    let cond: serde_json::Value = serde_json::from_str(cond_json).unwrap_or(serde_json::Value::Null);
    let mut v: Vec<String> = roco_core::cond::infer_triggers(&cond).into_iter().collect();
    v.sort();
    v
}

// ── 效果执行对拍 ──

/// 执行原始 JSON 效果树，返回 Mutation 的 JSON 列表（逐个序列化）。
#[pyfunction]
fn py_execute(ctx_json: &str, effects_json: &str) -> Vec<String> {
    let ctx: roco_core::vm_ctx::Ctx = serde_json::from_str(ctx_json).unwrap();
    let effects: Vec<serde_json::Value> = serde_json::from_str(effects_json).unwrap();
    roco_core::vm_exec::execute(&ctx, &effects)
        .into_iter()
        .map(|m| serde_json::to_string(&m).unwrap())
        .collect()
}

// ── 整局对拍门 ──

/// 完整对局：spec JSON → 逐回合 digest 列表 + 胜者（JSON）。
#[pyfunction]
fn py_run_battle(spec_json: &str) -> String {
    use roco_core::battle_state::BattleSpec;
    let spec: BattleSpec = serde_json::from_str(spec_json).expect("spec 解析失败");
    let (digests, winner) = roco_core::turn::run_battle_from_spec(&spec);
    let winner = winner.unwrap_or_default();
    let obj = serde_json::json!({
        "winner": winner,
        "turns": digests,
    });
    obj.to_string()
}

/// 阶段4 掩码对拍：逐决策点（digests[i] 同状态）双方 17 维合法动作掩码。
#[pyfunction]
fn py_run_battle_masks(spec_json: &str) -> String {
    use roco_core::battle_state::BattleSpec;
    let spec: BattleSpec = serde_json::from_str(spec_json).expect("spec 解析失败");
    let (digests, masks, winner) = roco_core::turn::run_battle_from_spec_masks(&spec);
    let winner = winner.unwrap_or_default();
    let obj = serde_json::json!({
        "winner": winner,
        "turns": digests,
        "masks": masks.iter().map(|m| vec![m[0].to_vec(), m[1].to_vec()]).collect::<Vec<_>>(),
    });
    obj.to_string()
}

/// 阶段4-5 编码器对拍入口：RuleAgent 同轨迹对局，每回合（digest 同点）
/// 输出指定视角的 10 个编码数组。ast_tokens/ast_values 在 4-5b 前恒 0。
#[pyfunction]
fn py_run_battle_encoded(spec_json: &str, perspective: usize, mask_opp_bench: bool) -> String {
    use roco_core::battle_state::BattleSpec;
    let spec: BattleSpec = serde_json::from_str(spec_json).expect("spec 解析失败");
    let (digests, encodings, winner) =
        roco_core::turn::run_battle_from_spec_encoded(&spec, perspective, mask_opp_bench);
    let enc = encodings
        .iter()
        .map(|e| {
            // serde 对 >32 的定长数组无 Serialize impl，转嵌套 Vec
            serde_json::json!({
                "sprite_stats": e.sprite_stats.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
                "sprite_elements": e.sprite_elements.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
                "sprite_states": e.sprite_states.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
                "skill_stats": e.skill_stats.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
                "skill_elements": e.skill_elements.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
                "skill_states": e.skill_states.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
                "global_stats": e.global_stats.to_vec(),
                "global_elements": e.global_elements.to_vec(),
                "ast_tokens": e.ast_tokens.to_vec(),
                "ast_values": e.ast_values.to_vec(),
            })
        })
        .collect::<Vec<_>>();
    let winner = winner.unwrap_or_default();
    let obj = serde_json::json!({
        "winner": winner,
        "turns": digests,
        "encodings": enc,
    });
    obj.to_string()
}

/// 阶段5a 入口：rust 自对弈整局（py _play_one_rl_battle 主循环镜像）。
/// 种子协议：rng = np_rng = spec.seed + 1（与 mcts 惯例一致；py 侧对拍
/// 需 random.seed(seed+1) + np.random.seed(seed+1)）。
/// 返回 dict：states(list[dict of np]) / P(N,17) / M(N,17) / v(N) /
/// winner / turns / outcome_a。
#[pyfunction]
#[pyo3(signature = (spec_json, cfg_json, evaluator_a, evaluator_b=None))]
fn py_selfplay_game(
    py: pyo3::Python<'_>,
    spec_json: &str,
    cfg_json: &str,
    evaluator_a: pyo3::PyObject,
    evaluator_b: Option<pyo3::PyObject>,
) -> pyo3::PyResult<pyo3::PyObject> {
    use numpy::{PyArray1, PyArray2};
    use pyo3::types::{PyDict, PyList};
    use roco_core::battle_state::BattleSpec;
    use roco_core::mcts::SearchCfg;
    use roco_core::np_random::NpRandom;
    use roco_core::selfplay::{play_game, SelfplayCfg};

    let spec: BattleSpec = serde_json::from_str(spec_json).expect("spec 解析失败");
    let cfg_v: serde_json::Value = serde_json::from_str(cfg_json).expect("cfg 解析失败");
    let g = |k: &str| cfg_v.get(k).cloned();
    let mut search = SearchCfg::default();
    if let Some(v) = g("num_simulations").and_then(|v| v.as_u64()) {
        search.num_simulations = v as usize;
    }
    if let Some(v) = g("root_noise").and_then(|v| v.as_f64()) {
        search.root_noise = v;
    }
    if let Some(v) = g("max_turns").and_then(|v| v.as_i64()) {
        search.max_turns = v;
    }
    if let Some(v) = g("opp_greedy").and_then(|v| v.as_bool()) {
        search.opp_greedy = v;
    }
    if let Some(v) = g("leaf_batch_size").and_then(|v| v.as_u64()) {
        search.leaf_batch_size = v as usize;
    }
    if let Some(v) = g("draw_margin").and_then(|v| v.as_f64()) {
        search.draw_margin = v;
    }
    if let Some(v) = g("gamma").and_then(|v| v.as_f64()) {
        search.gamma = v;
    }
    if let Some(v) = g("tanh_k").and_then(|v| v.as_f64()) {
        search.tanh_k = v;
    }
    search.use_network_opponent = true;
    let temperature = g("temperature").and_then(|v| v.as_f64()).unwrap_or(1.0);
    let timeout_ms = g("timeout_ms").and_then(|v| v.as_u64());

    let (mut state, mut engine, mut rng) = roco_core::turn::state_from_spec_selfplay(&spec);
    let mut np_rng = NpRandom::seed_i64(spec.seed + 1);
    let mut ev = PyEvaluator {
        a: evaluator_a,
        b: evaluator_b,
    };
    let scfg = SelfplayCfg {
        search,
        temperature,
        timeout_ms,
    };
    let rec = roco_core::selfplay::play_game(
        &mut state,
        &mut engine,
        &mut rng,
        &mut np_rng,
        &mut ev,
        &scfg,
    );

    let states = pyo3::types::PyList::new(
        py,
        rec.states.iter().map(|e| encoded_to_pydict(py, e).unwrap()),
    )?;
    let p = PyArray2::from_vec2(
        py,
        &rec.probs.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
    )?;
    let m = PyArray2::from_vec2(
        py,
        &rec.masks.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
    )?;
    let outcome = rec.outcome_a as f32;
    let v: Vec<f32> = rec
        .side
        .iter()
        .map(|&s| if s == 0 { outcome } else { -outcome })
        .collect();
    let v_arr = PyArray1::from_slice(py, &v);

    let dict = PyDict::new(py);
    dict.set_item("states", states)?;
    dict.set_item("P", p)?;
    dict.set_item("M", m)?;
    dict.set_item("v", v_arr)?;
    dict.set_item("side", rec.side.iter().map(|s| *s as u32).collect::<Vec<_>>())?;
    dict.set_item(
        "digests",
        serde_json::to_string(&rec.digests).expect("digests 序列化失败"),
    )?;
    dict.set_item(
        "lives",
        vec![state.players[0].lives, state.players[1].lives],
    )?;
    dict.set_item(
        "active",
        vec![
            state.players[0].active_index as i64,
            state.players[1].active_index as i64,
        ],
    )?;
    dict.set_item(
        "moves",
        rec.moves.iter().map(|(s, i)| vec![*s as i64, *i]).collect::<Vec<_>>(),
    )?;
    dict.set_item("winner", rec.winner.unwrap_or_default())?;
    dict.set_item("turns", rec.turns)?;
    dict.set_item("outcome_a", rec.outcome_a)?;
    Ok(dict.into_any().unbind())
}

// ── MCTS 对拍（阶段4）──

/// 确定性桩评估器：value=0，policy = mask / max(sum, 1)（与 py 侧桩一致）。
/// 用于隔离树/PUCT/步进/RNG 的对拍——不含 encoder 与网络。
struct UniformStub;

impl roco_core::mcts::SearchEvaluator for UniformStub {
    fn evaluate(
        &mut self,
        _state: &roco_core::battle_state::BattleState,
        mask: &[f32; roco_core::mcts_actions::NUM_ACTIONS],
        _perspective: &str,
    ) -> (f64, [f32; roco_core::mcts_actions::NUM_ACTIONS]) {
        (0.0, roco_core::mcts::normalize_mask(mask))
    }
}

/// 固定动作逐回合复现（对拍定位用）：spec + 每轮仿真的动作序列
/// （`[[[a_idx, b_idx], ...], ...]`，b_idx<0 = 聚能）→ 每轮仿真每回合结束后的
/// 状态摘要。每轮仿真之间回滚 state/engine 但**不回滚 RNG**，与 MCTS
/// 的 save/restore 语义（py save_mutable_state 不含 RNG）一致——复现第 N 轮
/// 仿真时 RNG 才会落在同一位置。
#[pyfunction]
fn py_fixed_turns(spec_json: &str, actions_json: &str) -> String {
    use roco_core::battle_state::BattleSpec;
    use roco_core::mcts::action_index_to_action;
    use roco_core::rule_agent::FirstAliveRepl;
    let spec: BattleSpec = serde_json::from_str(spec_json).expect("spec 解析失败");
    // 兼容两种输入：单轮 [[a,b],...] 与多轮 [[[a,b],...],...]
    let raw: serde_json::Value = serde_json::from_str(actions_json).expect("actions 解析失败");
    let sims: Vec<Vec<Vec<i64>>> = raw
        .as_array()
        .map(|arr| {
            if arr.first().map(|v| v.is_array()).unwrap_or(false)
                && arr
                    .first()
                    .and_then(|v| v.as_array())
                    .and_then(|a| a.first())
                    .map(|v| v.is_number())
                    .unwrap_or(false)
            {
                vec![arr.iter().map(|v| {
                    v.as_array()
                        .map(|a| a.iter().map(|x| x.as_i64().unwrap_or(0)).collect())
                        .unwrap_or_default()
                }).collect()]
            } else {
                arr.iter()
                    .map(|sim| {
                        sim.as_array()
                            .map(|pairs| {
                                pairs
                                    .iter()
                                    .map(|p| {
                                        p.as_array()
                                            .map(|a| a.iter().map(|x| x.as_i64().unwrap_or(0)).collect())
                                            .unwrap_or_default()
                                    })
                                    .collect()
                            })
                            .unwrap_or_default()
                    })
                    .collect()
            }
        })
        .unwrap_or_default();
    let (mut state, mut engine, mut rng) = roco_core::turn::state_from_spec(&spec);
    let initial_state = state.clone();
    let initial_engine = engine.clone();
    let mut digests: Vec<Vec<serde_json::Value>> = Vec::new();
    for sim in &sims {
        state = initial_state.clone();
        state.invalidate_all_stat_caches();
        engine = initial_engine.clone();
        state.mcts_sim = true;
        let mut sim_digests: Vec<serde_json::Value> = Vec::new();
        for pair in sim {
            let a_idx = pair.first().copied().unwrap_or(0);
            let b_idx = pair.get(1).copied().unwrap_or(-1);
            let action_a = action_index_to_action(&state, "A", a_idx.max(0) as usize)
                .unwrap_or_else(roco_core::mcts::gather_action);
            let action_b = if b_idx < 0 {
                roco_core::mcts::gather_action()
            } else {
                action_index_to_action(&state, "B", b_idx as usize)
                    .unwrap_or_else(roco_core::mcts::gather_action)
            };
            let mut repl = FirstAliveRepl;
            roco_core::turn::execute_turn_fixed(
                &mut state,
                &mut engine,
                &mut rng,
                action_a,
                action_b,
                &mut repl,
            );
            sim_digests.push(roco_core::turn::state_digest(&state));
        }
        digests.push(sim_digests);
    }
    serde_json::json!({ "turns": digests, "rng_mti": rng.mti() }).to_string()
}

/// MCTS 桩对拍入口：spec + cfg(JSON) → 搜索结果 + 轨迹 + 双方 RNG 状态。
/// cfg 字段：num_simulations/c_puct/root_noise/max_turns/opp_greedy/opp_temperature/
/// use_network_opponent/leaf_batch_size。
#[pyfunction]
fn py_mcts_stub(spec_json: &str, cfg_json: &str) -> String {
    use roco_core::battle_state::BattleSpec;
    use roco_core::mcts::{mcts_search_traced, SearchCfg};
    use roco_core::np_random::NpRandom;

    let spec: BattleSpec = serde_json::from_str(spec_json).expect("spec 解析失败");
    let cfg_v: serde_json::Value = serde_json::from_str(cfg_json).expect("cfg 解析失败");
    let g = |k: &str| cfg_v.get(k).cloned();
    let mut cfg = SearchCfg::default();
    if let Some(v) = g("num_simulations").and_then(|v| v.as_u64()) {
        cfg.num_simulations = v as usize;
    }
    if let Some(v) = g("c_puct").and_then(|v| v.as_f64()) {
        cfg.c_puct = v;
    }
    if let Some(v) = g("root_noise").and_then(|v| v.as_f64()) {
        cfg.root_noise = v;
    }
    if let Some(v) = g("max_turns").and_then(|v| v.as_i64()) {
        cfg.max_turns = v;
    }
    if let Some(v) = g("opp_greedy").and_then(|v| v.as_bool()) {
        cfg.opp_greedy = v;
    }
    if let Some(v) = g("opp_temperature").and_then(|v| v.as_f64()) {
        cfg.opp_temperature = v;
    }
    if let Some(v) = g("use_network_opponent").and_then(|v| v.as_bool()) {
        cfg.use_network_opponent = v;
    }
    if let Some(v) = g("leaf_batch_size").and_then(|v| v.as_u64()) {
        cfg.leaf_batch_size = v as usize;
    }

    let want_digests = cfg_v.get("digest_trace").and_then(|v| v.as_bool()).unwrap_or(false);
    let (mut state, mut engine, mut rng) = roco_core::turn::state_from_spec(&spec);
    let mut np_rng = NpRandom::seed_i64(spec.seed + 1);
    let mut stub = UniformStub;
    let mut trace: Vec<Vec<usize>> = Vec::new();
    let mut digests: Vec<Vec<serde_json::Value>> = Vec::new();
    let (probs, counts) = mcts_search_traced(
        &mut state,
        &mut engine,
        &mut rng,
        &mut np_rng,
        &mut stub,
        None,
        &cfg,
        &mut trace,
        if want_digests { Some(&mut digests) } else { None },
    );
    let rng_head = rng.state_head(8);
    let obj = serde_json::json!({
        "probs_bits": probs.iter().map(|p| p.to_bits()).collect::<Vec<u32>>(),
        "counts": counts,
        "trace": trace,
        "digest_trace": digests,
        "np_head": np_rng.state_head(8),
        "np_pos": np_rng.pos(),
        "rng_head": rng_head,
        "rng_mti": rng.mti(),
        "turn": state.turn,
    });
    obj.to_string()
}

// ── 阶段4-6：真实评估器回调（Rust 编码 → py torch 批量推理） ──

use numpy::{PyArray1, PyArray2};

/// 编码结果 → py dict（键与 py encode_battle_state 一致，值为 numpy 数组）。
fn encoded_to_pydict<'py>(
    py: pyo3::Python<'py>,
    e: &roco_core::encoder::EncodedState,
) -> pyo3::PyResult<pyo3::Bound<'py, pyo3::types::PyDict>> {
    use pyo3::types::PyDict;
    let d = PyDict::new(py);
    d.set_item(
        "sprite_stats",
        PyArray2::from_vec2(py, &e.sprite_stats.iter().map(|r| r.to_vec()).collect::<Vec<_>>())?,
    )?;
    d.set_item(
        "sprite_elements",
        PyArray2::from_vec2(
            py,
            &e.sprite_elements.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
        )?,
    )?;
    d.set_item(
        "sprite_states",
        PyArray2::from_vec2(py, &e.sprite_states.iter().map(|r| r.to_vec()).collect::<Vec<_>>())?,
    )?;
    d.set_item(
        "skill_stats",
        PyArray2::from_vec2(py, &e.skill_stats.iter().map(|r| r.to_vec()).collect::<Vec<_>>())?,
    )?;
    d.set_item(
        "skill_elements",
        PyArray2::from_vec2(
            py,
            &e.skill_elements.iter().map(|r| r.to_vec()).collect::<Vec<_>>(),
        )?,
    )?;
    d.set_item(
        "skill_states",
        PyArray2::from_vec2(py, &e.skill_states.iter().map(|r| r.to_vec()).collect::<Vec<_>>())?,
    )?;
    d.set_item("global_stats", PyArray1::from_slice(py, &e.global_stats))?;
    d.set_item("global_elements", PyArray1::from_slice(py, &e.global_elements))?;
    d.set_item("ast_tokens", PyArray1::from_slice(py, &e.ast_tokens))?;
    d.set_item("ast_values", PyArray1::from_slice(py, &e.ast_values))?;
    Ok(d)
}

/// 真实评估器：包装 py 侧 `evaluate_batch(states, masks) -> (values, priors)`
/// 对象（TorchEvaluator / QueuePolicyEvaluator / BatchedInferenceServer 均
/// 为该签名——批量推理协议不变）。perspective "A"→evaluator_a（主模型）、
/// "B"→evaluator_b（对手模型，缺省回退 a）。py 侧薄适配把返回值转 plain
/// list（pyo3 直接抽 np.float32 数组不可靠）。
struct PyEvaluator {
    a: pyo3::PyObject,
    b: Option<pyo3::PyObject>,
}

impl PyEvaluator {
    fn obj_for(&self, perspective: &str) -> &pyo3::PyObject {
        if perspective == "B" {
            self.b.as_ref().unwrap_or(&self.a)
        } else {
            &self.a
        }
    }

    fn call_batch(
        &self,
        encodings: &[roco_core::encoder::EncodedState],
        masks: &[[f32; roco_core::mcts_actions::NUM_ACTIONS]],
        perspective: &str,
    ) -> pyo3::PyResult<(Vec<f64>, Vec<Vec<f64>>)> {
        pyo3::Python::with_gil(|py| {
            let states = pyo3::types::PyList::new(
                py,
                encodings.iter().map(|e| encoded_to_pydict(py, e).unwrap()),
            )?;
            let masks_arr = PyArray2::from_vec2(
                py,
                &masks.iter().map(|m| m.to_vec()).collect::<Vec<_>>(),
            )?;
            let res = self
                .obj_for(perspective)
                .bind(py)
                .call_method1("evaluate_batch", (states, masks_arr))?;
            let (vals, priors) = res.extract::<(Vec<f64>, Vec<Vec<f64>>)>()?;
            Ok((vals, priors))
        })
    }
}

impl roco_core::mcts::SearchEvaluator for PyEvaluator {
    fn evaluate(
        &mut self,
        state: &roco_core::battle_state::BattleState,
        mask: &[f32; roco_core::mcts_actions::NUM_ACTIONS],
        perspective: &str,
    ) -> (f64, [f32; roco_core::mcts_actions::NUM_ACTIONS]) {
        let pi = if perspective == "B" { 1 } else { 0 };
        let enc = roco_core::encoder::encode_battle_state(state, pi, false);
        let (vals, priors) = self
            .call_batch(std::slice::from_ref(&enc), &[*mask], perspective)
            .expect("py evaluate_batch 调用失败");
        let mut p = [0f32; roco_core::mcts_actions::NUM_ACTIONS];
        for (i, v) in priors[0].iter().enumerate().take(roco_core::mcts_actions::NUM_ACTIONS) {
            p[i] = *v as f32;
        }
        (vals[0], p)
    }

    fn evaluate_batch(
        &mut self,
        states: &[&roco_core::battle_state::BattleState],
        masks: &[[f32; roco_core::mcts_actions::NUM_ACTIONS]],
        perspective: &str,
    ) -> (Vec<f64>, Vec<[f32; roco_core::mcts_actions::NUM_ACTIONS]>) {
        let pi = if perspective == "B" { 1 } else { 0 };
        let encodings: Vec<roco_core::encoder::EncodedState> = states
            .iter()
            .map(|s| roco_core::encoder::encode_battle_state(s, pi, false))
            .collect();
        let (vals, priors) = self
            .call_batch(&encodings, masks, perspective)
            .expect("py evaluate_batch 调用失败");
        let mut out = Vec::with_capacity(priors.len());
        for row in &priors {
            let mut p = [0f32; roco_core::mcts_actions::NUM_ACTIONS];
            for (i, v) in row.iter().enumerate().take(roco_core::mcts_actions::NUM_ACTIONS) {
                p[i] = *v as f32;
            }
            out.push(p);
        }
        (vals, out)
    }
}

/// 阶段4-6 入口：真实评估器驱动的 MCTS 搜索（spec 同 py_mcts_stub）。
/// evaluator_a = 主模型（叶评估 + 根先验），evaluator_b = 对手策略模型
/// （缺省回退 a）；均为 py `evaluate_batch(states, masks)->(values, priors)`
/// 对象（返回值需为 plain list，见 py 侧薄适配）。
#[pyfunction]
#[pyo3(signature = (spec_json, cfg_json, evaluator_a=None, evaluator_b=None))]
fn py_mcts_search(
    spec_json: &str,
    cfg_json: &str,
    evaluator_a: Option<pyo3::PyObject>,
    evaluator_b: Option<pyo3::PyObject>,
) -> String {
    use roco_core::battle_state::BattleSpec;
    use roco_core::mcts::{mcts_search_traced, SearchCfg};
    use roco_core::np_random::NpRandom;

    let spec: BattleSpec = serde_json::from_str(spec_json).expect("spec 解析失败");
    let cfg_v: serde_json::Value = serde_json::from_str(cfg_json).expect("cfg 解析失败");
    let g = |k: &str| cfg_v.get(k).cloned();
    let mut cfg = SearchCfg::default();
    if let Some(v) = g("num_simulations").and_then(|v| v.as_u64()) {
        cfg.num_simulations = v as usize;
    }
    if let Some(v) = g("c_puct").and_then(|v| v.as_f64()) {
        cfg.c_puct = v;
    }
    if let Some(v) = g("root_noise").and_then(|v| v.as_f64()) {
        cfg.root_noise = v;
    }
    if let Some(v) = g("max_turns").and_then(|v| v.as_i64()) {
        cfg.max_turns = v;
    }
    if let Some(v) = g("opp_greedy").and_then(|v| v.as_bool()) {
        cfg.opp_greedy = v;
    }
    if let Some(v) = g("opp_temperature").and_then(|v| v.as_f64()) {
        cfg.opp_temperature = v;
    }
    if let Some(v) = g("use_network_opponent").and_then(|v| v.as_bool()) {
        cfg.use_network_opponent = v;
    }
    if let Some(v) = g("leaf_batch_size").and_then(|v| v.as_u64()) {
        cfg.leaf_batch_size = v as usize;
    }

    let want_digests = cfg_v.get("digest_trace").and_then(|v| v.as_bool()).unwrap_or(false);
    let (mut state, mut engine, mut rng) = roco_core::turn::state_from_spec(&spec);
    let mut np_rng = NpRandom::seed_i64(spec.seed + 1);

    let (probs, counts, trace, digests) = if let Some(a) = evaluator_a {
        let mut ev = PyEvaluator {
            a,
            b: evaluator_b,
        };
        let mut trace: Vec<Vec<usize>> = Vec::new();
        let mut digests: Vec<Vec<serde_json::Value>> = Vec::new();
        let (probs, counts) = mcts_search_traced(
            &mut state,
            &mut engine,
            &mut rng,
            &mut np_rng,
            &mut ev,
            None,
            &cfg,
            &mut trace,
            if want_digests { Some(&mut digests) } else { None },
        );
        (probs, counts, trace, digests)
    } else {
        let mut stub = UniformStub;
        let mut trace: Vec<Vec<usize>> = Vec::new();
        let mut digests: Vec<Vec<serde_json::Value>> = Vec::new();
        let (probs, counts) = mcts_search_traced(
            &mut state,
            &mut engine,
            &mut rng,
            &mut np_rng,
            &mut stub,
            None,
            &cfg,
            &mut trace,
            if want_digests { Some(&mut digests) } else { None },
        );
        (probs, counts, trace, digests)
    };

    let rng_head = rng.state_head(8);
    let obj = serde_json::json!({
        "probs_bits": probs.iter().map(|p| p.to_bits()).collect::<Vec<u32>>(),
        "counts": counts,
        "trace": trace,
        "digest_trace": digests,
        "np_head": np_rng.state_head(8),
        "np_pos": np_rng.pos(),
        "rng_head": rng_head,
        "rng_mti": rng.mti(),
        "turn": state.turn,
    });
    obj.to_string()
}

// ── 伤害公式对拍 ──

#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (
    power, atk_base, def_base,
    atk_stage = 0.0, def_stage = 0.0, stab_mult = 1.0, type_mult = 1.0,
    weather_mult = 1.0, damage_reduction = 0.0, power_mult = 1.0,
    counter_power_mult = 1.0, additive_power = 0, damage_mult = 1.0,
    combo_count = 1, mark_bonus = 0.0,
))]
fn py_calc_damage(
    power: i64,
    atk_base: i64,
    def_base: i64,
    atk_stage: f64,
    def_stage: f64,
    stab_mult: f64,
    type_mult: f64,
    weather_mult: f64,
    damage_reduction: f64,
    power_mult: f64,
    counter_power_mult: f64,
    additive_power: i64,
    damage_mult: f64,
    combo_count: i64,
    mark_bonus: f64,
) -> i64 {
    calc_damage(
        power, atk_base, def_base, atk_stage, def_stage, stab_mult, type_mult,
        weather_mult, damage_reduction, power_mult, counter_power_mult,
        additive_power, damage_mult, combo_count, mark_bonus,
    )
}

#[pymodule]
fn roco_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(engine_info, m)?)?;
    m.add_function(wrap_pyfunction!(add, m)?)?;
    m.add_function(wrap_pyfunction!(py_random_stream, m)?)?;
    m.add_function(wrap_pyfunction!(py_choice_stream, m)?)?;
    m.add_function(wrap_pyfunction!(py_sample_indices, m)?)?;
    m.add_function(wrap_pyfunction!(py_randbelow_stream, m)?)?;
    m.add_function(wrap_pyfunction!(py_shuffle_indices, m)?)?;
    m.add_function(wrap_pyfunction!(py_genrand_u32_stream, m)?)?;
    m.add_function(wrap_pyfunction!(py_calc_damage, m)?)?;
    m.add_function(wrap_pyfunction!(py_resolve, m)?)?;
    m.add_function(wrap_pyfunction!(py_resolve_formula, m)?)?;
    m.add_function(wrap_pyfunction!(py_eval_cond, m)?)?;
    m.add_function(wrap_pyfunction!(py_infer_triggers, m)?)?;
    m.add_function(wrap_pyfunction!(py_execute, m)?)?;
    m.add_function(wrap_pyfunction!(py_run_battle, m)?)?;
    m.add_function(wrap_pyfunction!(py_run_battle_masks, m)?)?;
    m.add_function(wrap_pyfunction!(py_run_battle_encoded, m)?)?;
    m.add_function(wrap_pyfunction!(py_mcts_search, m)?)?;
    m.add_function(wrap_pyfunction!(py_selfplay_game, m)?)?;
    m.add_function(wrap_pyfunction!(py_mcts_stub, m)?)?;
    m.add_function(wrap_pyfunction!(py_fixed_turns, m)?)?;
    Ok(())
}
