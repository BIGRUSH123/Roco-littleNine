# -*- coding: utf-8 -*-
"""对比两个 npz 数据集是否完全一致（串行 vs 多进程生成）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
a = np.load(sys.argv[1])
b = np.load(sys.argv[2])
keys_a, keys_b = set(a.files), set(b.files)
print(f"键集合相同: {keys_a == keys_b}（{len(keys_a)} 键）")
bad = 0
for k in sorted(keys_a & keys_b):
    x, y = a[k], b[k]
    if x.shape != y.shape:
        print(f"  ✗ {k}: 形状 {x.shape} != {y.shape}")
        bad += 1
        continue
    if not np.array_equal(x, y):
        diff = int((x != y).sum()) if x.dtype.kind in "iuf" else -1
        print(f"  ✗ {k}: 数值不同（{diff} 处）")
        bad += 1
print("结论：" + ("完全一致 ✓" if bad == 0 else f"{bad} 个数组不一致 ✗"))
