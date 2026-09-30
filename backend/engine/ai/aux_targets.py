"""backend/engine/ai/aux_targets.py — 辅助头的目标（按本作赛制设计）。

设计动机（2026-09-24 实测，本地 2500 局）
----------------------------------------
第一版辅助目标是"终局每只精灵的血量比"，两个毛病：

  1. **与 value 标签同源**：`battle_outcome_a` 的 margin 就是"存活数 + 血量比 +
     命数"算出来的，预测终局血量比 ≈ 换个写法预测胜负，给共享主干的新梯度方向有限
     （KataGo 的 ownership 有用是因为它**局部且稠密**）。
  2. **搞错了货币**：本作 `Player.lives = 4`，**先力竭 4 只判负**。实测终局回合号
     中位 31、只有 16.9% 的局打满 60 回合上限 → **约 83% 的局是"打到第 4 只力竭"
     正常结束的**；而进入最后一回合前有 79.5% 的局已经只剩 1 条命（差一步力竭）。
     也就是说胜负押在**"谁先掉第 4 只"这个晚期尖锐事件**上，而"终局血量比"是消耗
     货币、不是击倒货币（还有 16.2% 的维度恒为满血）。

所以改成"赛制货币 + 短程动态"的复合目标（全部能从现有 npz 离线推导，不必重跑对局）：

    [0:12]  每只精灵"终局是否已力竭"(0/1)           —— 结算货币（谁被打掉）
    [12:14] 双方终局力竭只数 / 4                    —— 判负线（谁先到 4）
    [14:16] 未来 H 回合内，我方/对方是否会出现力竭   —— 短程动态（最该被预判的事）
    [16:18] 未来 H 回合内，双方队伍总血量掉幅        —— 短程动态（掉血速度）

后四项是"导致胜负的原因"，而不是胜负的另一种写法：局部、可预测，给主干补上真正
新的梯度方向。全部量纲落在 [0,1]，损失用 BCE。
"""
from __future__ import annotations

import numpy as np

from backend.engine.ai.dataset_temporal import (
    future_sample,
    iter_side_blocks,
    per_turn_first,
    turns_of,
)

AUX_HORIZON = 3                 # 短程动态窗口（回合）
AUX_DIM = 18                    # 布局见模块 docstring
_FAINT_END = slice(0, 12)
_FINAL_FAINTS = slice(12, 14)
_FAINT_SOON = slice(14, 16)
_HP_DROP_SOON = slice(16, 18)


def aux_layout() -> dict[str, slice]:
    """布局表（单一事实来源，测试与文档都读它）。"""
    return {
        "faint_at_end": _FAINT_END,
        "final_faints": _FINAL_FAINTS,
        "faint_soon": _FAINT_SOON,
        "hp_drop_soon": _HP_DROP_SOON,
    }


def derive_aux_targets(
    ds: dict[str, np.ndarray], horizon: int = AUX_HORIZON,
) -> np.ndarray:
    """构造 (N, AUX_DIM) 辅助标签；每侧都按**自己视角**取，不依赖块的先后顺序。"""
    gid = np.asarray(ds["game_id"])
    stats = np.asarray(ds["sprite_stats"], dtype=np.float32)
    turns = turns_of(ds)
    n = len(gid)

    hp = stats[:, :, 0]
    alive = (hp > 0).astype(np.float32)
    team_hp = np.stack([hp[:, :6].sum(axis=1), hp[:, 6:].sum(axis=1)], axis=1)

    out = np.zeros((n, AUX_DIM), dtype=np.float32)
    for idx, block1, block2 in iter_side_blocks(gid, turns):
        for mine, theirs in ((block1, block2), (block2, block1)):
            my_abs = idx[mine]
            my_turns = turns[my_abs]
            my_uniq, my_first = per_turn_first(my_abs, my_turns)
            if len(my_first) == 0:
                continue

            # ① 终局状态：用"本侧最后一个回合的首个样本"，视角天然正确
            my_final = my_first[-1]
            my_faint = (hp[my_final][:6] <= 0).astype(np.float32)
            their_faint = (hp[my_final][6:] <= 0).astype(np.float32)
            out[my_abs, 0:6] = my_faint[None, :]
            out[my_abs, 6:12] = their_faint[None, :]
            out[my_abs, 12] = my_faint.sum() / 4.0
            out[my_abs, 13] = their_faint.sum() / 4.0

            # ② 短程动态：本侧 H 回合后
            my_next = future_sample(my_uniq, my_first, my_turns, horizon)
            ok = my_next >= 0
            if ok.any():
                rows = my_abs[ok]
                d_alive = alive[rows][:, :6].sum(axis=1) - alive[my_next[ok]][:, :6].sum(axis=1)
                out[rows, 14] = (d_alive > 0).astype(np.float32)
                cur = team_hp[rows, 0]
                out[rows, 16] = np.clip((cur - team_hp[my_next[ok], 0]) /
                                        np.maximum(cur, 1.0), 0.0, 1.0)

            # ③ 短程动态：对手侧 H 回合后（对方块样本是对方视角 → 它的 first 6 是它的）
            th_abs = idx[theirs]
            th_uniq, th_first = per_turn_first(th_abs, turns[th_abs])
            th_next = future_sample(th_uniq, th_first, my_turns, horizon)
            ok_t = th_next >= 0
            if ok_t.any():
                rows = my_abs[ok_t]
                d_alive = alive[rows][:, 6:].sum(axis=1) - alive[th_next[ok_t]][:, :6].sum(axis=1)
                out[rows, 15] = (d_alive > 0).astype(np.float32)
                cur = team_hp[rows, 1]
                out[rows, 17] = np.clip((cur - team_hp[th_next[ok_t], 0]) /
                                        np.maximum(cur, 1.0), 0.0, 1.0)
    return out


def derive_final_hp_targets(ds: dict[str, np.ndarray]) -> np.ndarray:
    """旧版目标（终局每只精灵血量比，12 维）：保留用于对照既有检查点。"""
    stats = np.asarray(ds["sprite_stats"], dtype=np.float32)
    frac = np.clip(stats[:, :, 0] / np.maximum(stats[:, :, 1], 1.0), 0.0, 1.0)
    gid = np.asarray(ds["game_id"])
    order = np.argsort(gid, kind="stable")
    sorted_gid = gid[order]
    last_pos = np.flatnonzero(np.r_[sorted_gid[1:] != sorted_gid[:-1], True])
    group_sizes = np.diff(np.r_[-1, last_pos])
    out = np.empty_like(frac)
    out[order] = np.repeat(frac[order][last_pos], group_sizes, axis=0)
    return out


# ═══════════════════════════════════════════════════════════════════
# 「非当前状态」类的辅助目标（唯一可能突破当前局面信息天花板的方向）
# ═══════════════════════════════════════════════════════════════════
# 实测（2026-09-24，干净数据、val 半区）：13 维手工特征（血量差/存活差/魔力差/回合…）
# 的逻辑回归就能到 0.795 AUC，网络 0.805-0.824 —— **当前状态的信息接近饱和**，
# 因此任何"当前局面的函数"作辅助标签都加不进新东西（v5/v7 两个设计实测无效）。
# 唯一还能突破的是**不由当前局面决定**的量。本作是"每回合双方同时决策"，所以
# **对手本回合出的那一手**正是这样的量：它和我在同一局面上做出，局面里推不出来，
# 但它是 MCTS 对手建模（belief）真正需要预判的东西。

OPP_ACTION_DIM = 22        # 动作空间 0..21
OPP_ACTION_UNK = 22        # 该回合对手没有对应样本 → 未知（CE 时 ignore_index）


def derive_opp_action_targets(ds: dict[str, np.ndarray]) -> np.ndarray:
    """每个样本的标签 = **对手在同一回合**实际选择的动作索引（未知 → OPP_ACTION_UNK）。

    口径：样本状态是其**做出决策之前**的局面；双方在同局面同时决策，故对手动作
    与该样本状态独立可学但不平凡（正是 belief 要预测的东西）。标签来自对方侧样本块
    中**回合号相同**的那条记录的 `action` 列。
    """
    gid = np.asarray(ds["game_id"])
    action = np.asarray(ds["action"], dtype=np.int64)
    turns = turns_of(ds)
    out = np.full(len(gid), OPP_ACTION_UNK, dtype=np.int64)

    for idx, block1, block2 in iter_side_blocks(gid, turns):
        for mine, theirs in ((block1, block2), (block2, block1)):
            my_abs = idx[mine]
            my_turns = turns[my_abs]
            th_abs = idx[theirs]
            th_turns = turns[th_abs]
            if len(th_abs) == 0:
                continue
            th_uniq, th_first = per_turn_first(th_abs, th_turns)
            rank = np.searchsorted(th_uniq, my_turns)
            rank = np.clip(rank, 0, max(len(th_uniq) - 1, 0))
            same = th_uniq[rank] == my_turns
            if not same.any():
                continue
            # 同一回合可能有多个样本（主动决策 + 强制换人）→ per_turn_first 已取首个
            src = th_first[rank[same]]
            picked = action[src]
            valid = (picked >= 0) & (picked < OPP_ACTION_DIM)
            rows = my_abs[same]
            out[rows[valid]] = picked[valid]
    return out
