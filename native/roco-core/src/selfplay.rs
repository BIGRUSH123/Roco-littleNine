//! py train.py MCTSAgent + _play_one_rl_battle 的 Rust 镜像（阶段5a）。
//!
//! 真自我博弈：双方各按 MCTSAgent 语义搜索/记录/采样——
//! - B 侧决策在 swap 视图上搜索（py 交换 player_a/player_b 的等价物：
//!   克隆 state 并交换 players[0]/[1]；globals/mark 键不换——py 的
//!   team_counters/marks 同样不随 swap 换键，两侧行为一致）；
//! - 搜索后用新鲜 mask 过滤 + 重归一（防御 save/restore 微差），全零回退
//!   聚能且【不记录】该样本（py 同）；
//! - 采样 = policy_select_idx（温度幂 + 单均匀抽签；np 流与 py
//!   _sample_action 的 np.random.choice 同序）；
//! - 真实回合推进用 execute_turn_fixed + EvalRepl（网络力竭换人，与 py
//!   MCTSAgent.choose_replacement 同语义）；
//! - 记录双方视角样本：A 记 +outcome，B 记 -outcome。

use crate::battle_state::BattleState;
use crate::encoder::{encode_battle_state, EncodedState};
use crate::mcts::{
    action_index_to_action, battle_outcome_a, gather_action, mcts_search_traced, policy_select_idx,
    EvalRepl, SearchCfg, SearchEvaluator,
};
use crate::mcts_actions::NUM_ACTIONS;
use crate::np_random::NpRandom;
use crate::rng::PyRandom;
use crate::rule_agent::Action;
use crate::engine::VmEngine;

pub struct SelfplayCfg {
    pub search: SearchCfg,
    pub temperature: f64,
    /// 单局墙钟上限（毫秒）；None = 不限
    pub timeout_ms: Option<u64>,
}

#[derive(Default)]
pub struct GameRecords {
    pub states: Vec<EncodedState>,
    pub probs: Vec<[f32; NUM_ACTIONS]>,
    pub masks: Vec<[f32; NUM_ACTIONS]>,
    pub side: Vec<u8>,
    /// (side, sampled action idx) 逐决策记录（对拍采样用）
    pub moves: Vec<(u8, i64)>,
    pub digests: Vec<serde_json::Value>,
    pub outcome_a: f64,
    pub winner: Option<String>,
    pub turns: usize,
}

/// 单侧决策核心（MCTSAgent.choose_action 的决策段镜像）。
/// 返回 (动作, Some(记录))；记录为 None = 聚能兜底（py 同样不记录）。
fn decide(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    cfg: &SearchCfg,
    temperature: f64,
    perspective: usize,
) -> (Action, i64, Option<(EncodedState, [f32; NUM_ACTIONS], [f32; NUM_ACTIONS])>) {
    // B 侧：swap 视图（py 交换 player_a/player_b；globals 不换）。
    // 观察者 owner 是 (team, idx) 标签，须随视图翻转（py 按精灵对象身份，免疫）。
    let mut view: Option<BattleState> = None;
    if perspective == 1 {
        let mut v = state.clone();
        v.players.swap(0, 1);
        // 直改修饰的精灵标签同样按 (team, idx) 记录，随视图翻转；
        // 视图克隆用完即弃，无需翻转回。
        v.direct_mod_sprite_ids = v
            .direct_mod_sprite_ids
            .iter()
            .map(|(t, i)| {
                (
                    if t == "A" { "B".to_string() } else { "A".to_string() },
                    *i,
                )
            })
            .collect();
        view = Some(v);
        engine.flip_owner_teams();
    }
    let searching = view.as_mut().unwrap_or(state);

    let mut trace: Vec<Vec<usize>> = Vec::new();
    let (mut probs, _counts) = mcts_search_traced(
        searching, engine, rng, np_rng, evaluator, None, cfg, &mut trace, None,
    );
    if perspective == 1 {
        engine.flip_owner_teams();
    }

    // 新鲜 mask 过滤 + 重归一（py：valid_mask 重算，probs*mask/sum）
    let fresh_mask = crate::mcts_actions::valid_actions_mask(searching, "A");
    let mut sum;
    {
        // py probs*mask 后 .sum()：numpy float32 成对求和（np_sum_f32 镜像），
        // 顺序累加会有 1ulp 噪声，在采样边界处被放大
        for i in 0..NUM_ACTIONS {
            probs[i] *= fresh_mask[i];
        }
        sum = crate::mcts::np_sum_f32(&probs);
    }
    if sum <= 0.0 {
        // 全零：锁定且无替换 → 聚能兜底，不记录（py 同）
        return (gather_action(), -1, None);
    }
    for v in probs.iter_mut() {
        *v /= sum;
    }

    // 记录（搜索视图 perspective 0 = 真实决策侧视角）
    let record = (encode_battle_state(searching, 0, false), probs, fresh_mask);

    // 采样（py _sample_action：温度幂 + 单均匀；<0 → 聚能）
    let idx = policy_select_idx(&probs, temperature, false, np_rng);
    let action = if idx < 0 {
        gather_action()
    } else {
        action_index_to_action(searching, "A", idx as usize).unwrap_or_else(gather_action)
    };
    (action, idx, Some(record))
}

/// 单局自我博弈（py _play_one_rl_battle 主循环镜像）。
pub fn play_game(
    state: &mut BattleState,
    engine: &mut VmEngine,
    rng: &mut PyRandom,
    np_rng: &mut NpRandom,
    evaluator: &mut dyn SearchEvaluator,
    cfg: &SelfplayCfg,
) -> GameRecords {
    let mut rec = GameRecords::default();
    // py 按 agent 分 history，最终 a.history + b.history 串联——
    // 记录顺序必须与之同构：先收集后拼接
    let mut a_records: Vec<(EncodedState, [f32; NUM_ACTIONS], [f32; NUM_ACTIONS])> = Vec::new();
    let mut b_records: Vec<(EncodedState, [f32; NUM_ACTIONS], [f32; NUM_ACTIONS])> = Vec::new();
    let start = std::time::Instant::now();
    let mut turn = 0usize;

    while state.winner.is_none() && turn < cfg.search.max_turns as usize {
        // py execute_turn：先 turn+=1 + 清理 + 回合开始阶段，然后 agent 才
        // 搜索/编码（决策发生在回合内）——顺序必须一致
        crate::turn::begin_turn(state, engine, rng);

        // py _select_action 道具循环：采样到 item → 结算道具 → 重新
        // 搜索/记录（每次 choose_action 都记录），上限 8 次
        let mut select = |perspective: usize,
                          rec_side: u8,
                          records: &mut Vec<(EncodedState, [f32; NUM_ACTIONS], [f32; NUM_ACTIONS])>,
                          rec: &mut GameRecords| -> Action {
            let team = if perspective == 0 { "A" } else { "B" };
            for _ in 0..8 {
                let (act, idx, r) = decide(
                    state, engine, rng, np_rng, evaluator, &cfg.search, cfg.temperature,
                    perspective,
                );
                if let Some((st, p, m)) = r {
                    records.push((st, p, m));
                }
                rec.moves.push((rec_side, idx));
                if act.kind == "item" {
                    crate::turn::resolve_item(state, engine, rng, team);
                    continue;
                }
                return act;
            }
            gather_action()
        };

        let act_a = select(0, 0, &mut a_records, &mut rec);
        let act_b = select(1, 1, &mut b_records, &mut rec);

        {
            let mut repl = EvalRepl::new(
                evaluator,
                np_rng,
                cfg.search.opp_temperature,
                cfg.search.opp_greedy,
            );
            crate::turn::resolve_turn(state, engine, rng, &mut repl, act_a, act_b);
        }
        rec.digests.push(crate::turn::state_digest(state));

        turn += 1;
        if let Some(ms) = cfg.timeout_ms {
            if start.elapsed().as_millis() as u64 >= ms {
                break;
            }
        }
    }

    for (st, p, m) in a_records {
        rec.states.push(st);
        rec.probs.push(p);
        rec.masks.push(m);
        rec.side.push(0);
    }
    for (st, p, m) in b_records {
        rec.states.push(st);
        rec.probs.push(p);
        rec.masks.push(m);
        rec.side.push(1);
    }
    rec.turns = turn;
    rec.outcome_a = battle_outcome_a(
        state,
        cfg.search.max_turns,
        cfg.search.draw_margin,
        cfg.search.gamma,
        cfg.search.tanh_k,
    );
    rec.winner = state.winner.clone();
    rec
}
