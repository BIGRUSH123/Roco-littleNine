# -*- coding: utf-8 -*-
"""检查 BC 冒烟数据里道具/首领化动作是否出现（action_idx 16）、掩码分布。"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

ds = np.load(_ROOT / "checkpoints/bc_smoke.npz", allow_pickle=True)
print("键:", list(ds.keys()))
actions = ds["action"]
masks = ds["mask"] if "mask" in ds else None
print("样本数:", len(actions))
print("动作分布:", Counter(actions.tolist()).most_common())
if masks is not None:
    legal_item = int((masks[:, 16] > 0).sum())
    print(f"道具可用的决策点数: {legal_item}/{len(masks)}")
    print(f"道具可用时被选中的次数: {int(((actions == 16)).sum())}")
team_ids = ds["team_id"] if "team_id" in ds else None
if team_ids is not None:
    print("meta 队索引分布(前 10):", Counter(team_ids.tolist()).most_common(10))
print("shapes:", {k: ds[k].shape for k in ds.keys()})
