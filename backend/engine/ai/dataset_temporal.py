"""backend/engine/ai/dataset_temporal.py — 数据集的时间结构索引（历史/辅助目标共用）。

`bc_record` 每局存的是 **A 侧全部决策样本 + B 侧全部决策样本**（两侧各自按回合递增、
各自视角编码），回合号可从 `global_stats[:, 0] * 150` 还原。历史特征与辅助目标都需要
这套"局 → 两侧块 → 每回合首个样本"的索引，所以集中在这里，避免两处各写一份。
"""
from __future__ import annotations

import numpy as np


def turns_of(ds: dict[str, np.ndarray]) -> np.ndarray:
    """每个样本的回合号（编码器把 battle.turn/150 写进 global_stats[0]）。"""
    return np.rint(np.asarray(ds["global_stats"], dtype=np.float32)[:, 0] * 150.0).astype(np.int64)


def iter_side_blocks(gid: np.ndarray, turns: np.ndarray):
    """逐局产出 (idx, 侧1块, 侧2块)：idx 为该局样本的**绝对下标**，块内是相对位置。

    切分依据：同一侧块内回合单调递增，回合号回退处即为侧切换点。
    数据异常（多于两段）时只取前两段，避免静默错配。
    """
    gid = np.asarray(gid)
    order = np.argsort(gid, kind="stable")
    sorted_gid = gid[order]
    bounds = np.flatnonzero(np.r_[True, sorted_gid[1:] != sorted_gid[:-1], True])
    for start, end in zip(bounds[:-1], bounds[1:], strict=False):
        idx = order[start:end]
        cuts = np.flatnonzero(np.diff(turns[idx]) < 0) + 1
        blocks = np.split(np.arange(len(idx)), cuts)
        if len(blocks) < 2:
            continue
        yield idx, blocks[0], blocks[1]


def per_turn_first(block_abs: np.ndarray, turns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """每回合取第一个样本 → (该侧的唯一回合数组（递增）, 对应的**绝对下标**)。

    `block_abs` 传绝对下标；返回的回合数组同样按 `block_abs` 取。
    """
    first = np.flatnonzero(np.r_[True, turns[1:] != turns[:-1]])
    return turns[first], block_abs[first]


def future_sample(
    uniq_turns: np.ndarray, first_abs: np.ndarray, ref_turns: np.ndarray, horizon: int,
) -> np.ndarray:
    """对每个 ref 回合，取该侧"第 horizon 个后续回合"的样本绝对下标；不存在则 -1。

    `uniq_turns` / `first_abs` 来自 `per_turn_first`（回合递增），因此按位置偏移即可，
    回合号有缺号也不会错位。
    """
    rank = np.searchsorted(uniq_turns, ref_turns)      # ref 回合在本侧的位置
    target = rank + horizon
    out = np.full(len(ref_turns), -1, dtype=np.int64)
    ok = target < len(first_abs)
    out[ok] = first_abs[target[ok]]
    return out
