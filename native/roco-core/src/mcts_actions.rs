//! mcts_actions — 合法动作掩码（移植自 backend/engine/ai/core/mcts.py
//! 的 get_valid_actions / action_index_to_action），阶段4 MCTS 搜索用。
//!
//! 动作空间（17 维）：
//!   0-9   技能槽（active.skills 前 10）
//!   10-14 板凳槽（固定映射，不跳过力竭；active 计入跳过）
//!   15    聚能
//!   16    道具

use crate::battle_state::{BattleSkill, BattleState};

pub const NUM_ACTIONS: usize = 17;

/// py get_valid_actions：17 维 0/1 mask（能量支付判定走引擎侧
/// skill_energy_cost，与 can_pay_skill_energy_cost 同源）。
pub fn valid_actions_mask(state: &BattleState, team: &str) -> [f32; NUM_ACTIONS] {
    let mut mask = [0f32; NUM_ACTIONS];
    let pi = if team == "A" { 0 } else { 1 };
    let player = &state.players[pi];
    let ai = player.active_index;
    if ai >= player.team.len() {
        return mask;
    }
    let active = &player.team[ai];

    let fill_bench = |mask: &mut [f32; NUM_ACTIONS], allow: bool| {
        let mut bench_slot = 0usize;
        for (i, s) in player.team.iter().enumerate() {
            if i == ai {
                continue;
            }
            if bench_slot < 5 {
                mask[10 + bench_slot] = if !s.is_fainted() && allow { 1.0 } else { 0.0 };
                bench_slot += 1;
            }
        }
    };

    // 力竭 → 仅换宠（10-14），屏蔽技能/聚能/道具
    if active.is_fainted() {
        fill_bench(&mut mask, true);
        return mask;
    }

    // 蓄力中：仅蓄力技能（若可支付）+ 换宠（引擎允许中断蓄力）
    if active.charging {
        let charged_idx = charged_index(active);
        if (0..10).contains(&charged_idx) {
            let i = charged_idx as usize;
            let sk = &active.skills[i];
            if sk.cooldown <= 0 && can_pay(state, team, ai, sk, i) {
                mask[i] = 1.0;
            }
        }
        fill_bench(&mut mask, true);
        return mask;
    }

    // 技能 0-9
    for (i, sk) in active.skills.iter().take(10).enumerate() {
        if !sk.sealed && sk.cooldown <= 0 && can_pay(state, team, ai, sk, i) {
            mask[i] = 1.0;
        }
    }
    // 换宠 10-14（locked 全禁）
    fill_bench(&mut mask, active.locked_turns <= 0);
    // 聚能 15（非蓄力恒合法——蓄力分支已提前返回）
    mask[15] = 1.0;

    // 道具 16：py 只按 is_exhausted（uses >= max_uses），不看冷却
    if let Some(it) = &player.item {
        if it.uses < it.max_uses {
            mask[16] = 1.0;
        }
    }
    mask
}

/// valid 列表（mask > 0 的下标，升序）。
pub fn valid_from_mask(mask: &[f32; NUM_ACTIONS]) -> Vec<usize> {
    (0..NUM_ACTIONS).filter(|&i| mask[i] > 0.0).collect()
}

/// py _charged_skill_index：引用跟随技能对象（skill_id）优先。
fn charged_index(active: &crate::battle_state::Sprite) -> i64 {
    if let Some(id) = active.charged_skill_ref {
        for (i, sk) in active.skills.iter().enumerate() {
            if sk.skill_id == id {
                return i as i64;
            }
        }
        return -1;
    }
    active.charged_skill_index
}

/// py can_pay_skill_energy_cost（供掩码判定，不改状态）。
fn can_pay(
    state: &BattleState,
    team: &str,
    user_idx: usize,
    sk: &BattleSkill,
    skill_index: usize,
) -> bool {
    let pi = if team == "A" { 0 } else { 1 };
    let user = &state.players[pi].team[user_idx];
    let cost = crate::turn::skill_energy_cost(state, team, user_idx, sk, skill_index);
    if cost <= 0 || user.energy >= cost {
        return true;
    }
    let blood_price = user.modifiers.get("blood_price").copied().unwrap_or(0.0);
    if blood_price <= 0.0 {
        return false;
    }
    let deficit = (cost - user.energy) as f64;
    let hp_cost = crate::damage::py_round(user.max_hp as f64 * blood_price * deficit);
    user.current_hp > hp_cost
}

/// 板凳槽位 → team 实际索引（py _bench_to_team_index，固定槽位不跳过力竭）。
pub fn bench_to_team_index(active_index: usize, bench_slot: usize, team_len: usize) -> Option<usize> {
    let team_idx = if bench_slot < active_index { bench_slot } else { bench_slot + 1 };
    if team_idx < team_len {
        Some(team_idx)
    } else {
        None
    }
}
