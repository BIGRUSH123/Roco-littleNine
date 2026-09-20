//! roco-core — 洛克王国世界模拟战斗引擎核心（纯 Rust，无 Python 依赖）。
//!
//! 移植自 backend/vm + backend/sim + backend/engine（Python oracle）。
//! 正确性标准：与 Python 引擎在固定种子下逐事件、逐状态字段一致
//! （见 backend/engine/differential/）。

pub mod battle_state;
pub mod cond;
pub mod data;
pub mod damage;
pub mod encoder;
pub mod engine;
pub mod formula;
pub mod journal;
pub mod mcts;
pub mod mcts_actions;
pub mod np_random;
pub mod observers;
pub mod replayer;
pub mod resolve;
pub mod rng;
pub mod rule_agent;
pub mod selfplay;
pub mod snapshot;
pub mod species_db;
pub mod statics;
pub mod turn;
pub mod vm_ctx;
pub mod vm_exec;
