"""backend/engine/ai/history_features.py — 从已有 BC/自博弈数据构造「回合历史」输入。

为什么在这里构造而不是改编码器：改编码器输出 schema 会让全部已有 npz 作废
（两万局要重跑几小时），而历史信息本来就躺在数据里：

  - `bc_record` 每局存的是 **A 侧全部决策样本 + B 侧全部决策样本**（两侧各自按回合
    递增，每侧视角编码）；
  - `global_stats[:, 0] = turn / 150`，所以每个样本的回合号可直接还原；
  - 同一回合内可能多个样本（主动决策 + 强制换人），取**该回合第一个**样本作为
    "这一回合双方做了什么"。

于是对每个样本（在它自己的视角下）可以拼出，从上一回合起的 k 步：
    [我方 6 只血量比(6)、我方力竭(6)、我方剩余命数(1)、
     对方 6 只血量比(6)、对方力竭(6)、对方剩余命数(1)、双方该回合动作 id]
最后一项正是"预测对手下一手"最需要的监督信号，而它完全来自 npz 的 `action` 列。

输出（都是 (N, k, ·)，时间顺序**旧→新**，不足 k 步在**前面**补 -1/零）：
  feats: float32 (N, k, 26)
  acts:  int64   (N, k, 2)  —— [我方动作, 对方动作]；-1 = 该步不存在
"""
from __future__ import annotations

import numpy as np

from backend.engine.ai.dataset_temporal import (
    iter_side_blocks,
    turns_of,
)

HIST_FEAT_DIM = 26
_OWN = slice(0, 13)      # 我方：血量比 6 + 力竭 6 + 命数 1
_OPP = slice(13, 26)     # 对方：同上
HIST_ACTION_UNK = -1     # 动作 id ∈ 0..21；-1 = 未知/补位（模型侧 +1 偏移到 embedding 0 号槽）


def _own_fields(stats: np.ndarray, gstats: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """取样本**自身视角**的"我方"13 维：血量比 6 + 力竭 6 + 命数 1。"""
    ratio = np.clip(stats[pos, :6, 0] / np.maximum(stats[pos, :6, 1], 1.0), 0.0, 1.0)
    faint = (stats[pos, :6, 0] <= 0).astype(np.float32)
    lives = gstats[pos][:, 2:3].astype(np.float32)
    return np.concatenate([ratio.astype(np.float32), faint, lives], axis=1)


def _opp_fields(stats: np.ndarray, gstats: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """取样本**自身视角**的"对方"13 维。"""
    ratio = np.clip(stats[pos, 6:, 0] / np.maximum(stats[pos, 6:, 1], 1.0), 0.0, 1.0)
    faint = (stats[pos, 6:, 0] <= 0).astype(np.float32)
    lives = gstats[pos][:, 3:4].astype(np.float32)
    return np.concatenate([ratio.astype(np.float32), faint, lives], axis=1)


def build_history_arrays(
    ds: dict[str, np.ndarray], k: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """从数据集构造 (feats, acts)。见模块 docstring。"""
    if k <= 0:
        raise ValueError("k 必须 >= 1")
    gid = np.asarray(ds["game_id"])
    stats = np.asarray(ds["sprite_stats"], dtype=np.float32)
    gstats = np.asarray(ds["global_stats"], dtype=np.float32)
    action = np.asarray(ds["action"], dtype=np.int64)
    turn = turns_of(ds)

    n = len(gid)
    feats = np.zeros((n, k, HIST_FEAT_DIM), dtype=np.float32)
    acts = np.full((n, k, 2), HIST_ACTION_UNK, dtype=np.int64)
    valid_action = (action >= 0) & (action < 22)

    for idx, block1, block2 in iter_side_blocks(gid, turn):
        for mine, theirs in ((block1, block2), (block2, block1)):
            _fill_one_side(idx, turn, stats, gstats, action, valid_action,
                           mine, theirs, k, feats, acts)
    return feats, acts


def _per_turn_first(block: np.ndarray, turns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """每回合取第一个样本 → (唯一回合数组, 对应的块内下标)。"""
    first = np.flatnonzero(np.r_[True, turns[1:] != turns[:-1]])
    return turns[first], block[first]


def _fill_one_side(
    idx: np.ndarray, turns: np.ndarray, stats: np.ndarray, gstats: np.ndarray,
    action: np.ndarray, valid_action: np.ndarray,
    mine: np.ndarray, theirs: np.ndarray, k: int,
    feats: np.ndarray, acts: np.ndarray,
) -> None:
    """填一侧样本：我方字段来自本侧样本，对方字段来自对方侧样本（都要转成本视角）。"""
    my_turns, my_first = _per_turn_first(mine, turns[mine])
    their_turns, their_first = _per_turn_first(theirs, turns[theirs])
    my_rank = np.searchsorted(my_turns, turns[mine])         # 本块内回合序号
    their_rank = np.searchsorted(their_turns, turns[mine])   # 对手在"我当前回合之前"的回合数
    rows_all = idx[mine]

    for step in range(1, k + 1):
        slot = k - step                      # 旧→新：step 越大越靠前
        ok_my = my_rank - step >= 0
        ok_their = their_rank - step >= 0

        if ok_my.any():
            # my_first 是"局内相对下标"（_per_turn_first 返回的就是这个），直接查表即可
            src = idx[my_first[my_rank[ok_my] - step]]
            rows = rows_all[ok_my]
            feats[rows, slot, _OWN] = _own_fields(stats, gstats, src)
            feats[rows, slot, _OPP] = _opp_fields(stats, gstats, src)
            acts[rows, slot, 0] = np.where(valid_action[src], action[src], HIST_ACTION_UNK)

        if ok_their.any():
            # 对手视角样本：它的"我方"= 我的"对方"，反之亦然
            ts = idx[their_first[their_rank[ok_their] - step]]
            rows = rows_all[ok_their]
            feats[rows, slot, _OPP] = _own_fields(stats, gstats, ts)
            feats[rows, slot, _OWN] = _opp_fields(stats, gstats, ts)
            acts[rows, slot, 1] = np.where(valid_action[ts], action[ts], HIST_ACTION_UNK)
