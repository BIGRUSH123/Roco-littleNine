# -*- coding: utf-8 -*-
"""把 BC 数据里的非有限值（inf/nan）夹到 float16 可表示范围，复制出一份干净数据。

用途：`gen_bc_data` 早期版本没有 float16 越界护栏，越界特征在 astype 时变 ±inf 落盘。
想让"旧世界"数据和"新世界"数据在**同一数据口径**下对比时，用它把旧数据补齐。
inf 一律夹到 ±65504（与护栏同口径），nan 归 0。

用法: python bc_sanitize.py <in.npz> <out.npz> [--dry]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

LIMIT = 65504.0


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    dry = "--dry" in sys.argv
    out: dict[str, np.ndarray] = {}
    with np.load(src, mmap_mode="r", allow_pickle=False) as z:
        for key in z.files:
            arr = np.asarray(z[key])
            if np.issubdtype(arr.dtype, np.floating):
                bad = ~np.isfinite(arr)
                n = int(bad.sum())
                if n:
                    print(f"  {key}: 修 {n} 个非有限值")
                    arr = np.nan_to_num(arr, nan=0.0,
                                        posinf=np.float16(LIMIT),
                                        neginf=np.float16(-LIMIT))
            out[key] = arr
    if dry:
        print("dry-run：不落盘")
        return
    np.savez(dst, **out)
    print(f"写出 {dst}  ({Path(dst).stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
