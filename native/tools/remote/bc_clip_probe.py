# -*- coding: utf-8 -*-
"""找出 BC 数据里越界（float16 上限被夹到 ±65504）的状态值落在哪些局/哪些特征位。

背景：`gen_bc_data` 会把 |x|>65504 的 float32 特征先夹再降精度（否则 inf 进 log1p
归一化后整批损失变 NaN）。计数非空说明引擎侧真的产出了极端数值——2026-09-22 已知
成因是"增益翻倍类技能被反复使用 → 属性步数指数爆炸"。本脚本定位到局号与特征位，
便于判断是少数几局还是系统性机制。

用法: python bc_clip_probe.py <bc_*.npz> [特征键=default:sprite_stats] [阈值=65000]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def main() -> None:
    path = sys.argv[1]
    key = sys.argv[2] if len(sys.argv) > 2 else "sprite_stats"
    thr = float(sys.argv[3]) if len(sys.argv) > 3 else 65000.0
    with np.load(path, mmap_mode="r", allow_pickle=False) as z:
        if key not in z.files:
            print(f"没有键 {key}；可用: {list(z.files)}")
            return
        arr = z[key]
        gid = np.asarray(z["game_id"])
        print(f"{path}  {key} shape={arr.shape} dtype={arr.dtype}")
        hit = np.zeros(arr.shape[0], dtype=bool)
        step = 20000
        for lo in range(0, arr.shape[0], step):
            blk = np.asarray(arr[lo:lo + step])
            hit[lo:lo + step] = (np.abs(blk) > thr).any(axis=tuple(range(1, blk.ndim)))
        idx = np.nonzero(hit)[0]
        print(f"越界样本 {len(idx)} / {arr.shape[0]}（阈值 {thr:g}）")
        if len(idx):
            games = sorted(set(gid[idx].tolist()))
            print(f"涉及局号 {len(games)} 局: {games[:20]}"
                  + (" ..." if len(games) > 20 else ""))
            # 特征位分布：把除样本维以外的所有维拉平
            flat = np.asarray(arr[idx]).reshape(len(idx), -1)
            over = np.abs(flat) > thr
            cols = np.nonzero(over.any(axis=0))[0]
            print(f"越界特征位 {len(cols)} 个: {cols[:20].tolist()}"
                  + (" ..." if len(cols) > 20 else ""))
            vals = flat[over]
            print(f"越界值范围 {vals.min():.3g} .. {vals.max():.3g}"
                  f"（夹取后应贴着 ±65504）")


if __name__ == "__main__":
    main()
