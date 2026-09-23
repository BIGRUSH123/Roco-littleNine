# -*- coding: utf-8 -*-
"""扫一份 BC npz 里所有浮点特征/标签的非有限值（inf/nan）计数。

用途：`gen_bc_data --out` 早期版本**没有** float16 越界护栏，越界值在 astype 时
变成 ±inf 直接落盘；inf 进 log1p 归一化后会让整批损失变 NaN。本脚本只读 mmap，
按块统计，内存占用与文件大小无关。

用法: python bc_finite_scan.py <bc_*.npz>
"""
from __future__ import annotations

import sys

import numpy as np


def main() -> None:
    path = sys.argv[1]
    with np.load(path, mmap_mode="r", allow_pickle=False) as z:
        for key in z.files:
            arr = z[key]
            if not np.issubdtype(arr.dtype, np.floating):
                print(f"{key:<18} {str(arr.dtype):<10} shape={arr.shape}  非浮点，跳过")
                continue
            n_bad = 0
            worst = 0.0
            step = 20000
            for lo in range(0, arr.shape[0], step):
                blk = np.asarray(arr[lo:lo + step])
                bad = ~np.isfinite(blk)
                n_bad += int(bad.sum())
                finite = blk[~bad]
                if finite.size:
                    worst = max(worst, float(np.abs(finite).max()))
            print(f"{key:<18} {str(arr.dtype):<10} shape={arr.shape}"
                  f"  非有限={n_bad}  有限最大值={worst:.4g}")


if __name__ == "__main__":
    main()
