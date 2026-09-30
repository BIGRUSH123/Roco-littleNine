# -*- coding: utf-8 -*-
"""probe_search_info.py — 自博弈数据里「搜索相对先验带来了多少新信息」。

背景：v5 起点跑了 2 轮自博弈（13.2 小时），训练指标一路变好（val_acc 0.72→0.85、
p_top1 0.59→0.745）却**门控零提升**（49.48% → 49.83%，都不达 0.52）。
早先另测到 `TV(先验, 搜索) = 0.036` —— 若访问分布几乎等于网络自己的先验，那么
"拿搜索当标签训策略"就等于"拿自己的策略再训自己一遍"，学不出新东西。

本探针直接在**落盘的自博弈样本**上量这件事（用样本持久化，无需重跑自我博弈）：

  TV(π_搜索, p_先验)        逐样本的总变差距离（0=完全一致，1=完全不同）
  最优动作是否一致            argmax(π) == argmax(p) 的比例
  熵对比                     H(π) vs H(p)

用法:
  python -X utf8 native/tools/probe_search_info.py --data checkpoints/<run>/samples/round1.npz \
      --ckpt checkpoints/bc20k_v5_slotaux.pt [--limit 4000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "backend").is_dir():
    ROOT = Path(r"D:\projects\Roco-LittleNine")
sys.path.insert(0, str(ROOT))

from native.tools.probe_value_quality import DEFAULT_KEYS, build_batch  # noqa: E402


def entropy(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-12, 1.0)
    return -(p * np.log(p)).sum(axis=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--batch", type=int, default=512)
    args = ap.parse_args()

    import torch

    from backend.engine.ai.core.model import ModularBattleNet

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = ModularBattleNet.load(args.ckpt, device=dev).eval()

    with np.load(args.data) as d:
        n = len(d["outcome"])
        take = min(args.limit, n)
        idx = np.sort(np.random.default_rng(0).choice(n, size=take, replace=False))
        ds = {k: d[k] for k in d.files}
    pi = np.asarray(ds["policy"], dtype=np.float64)[idx]
    masks = np.asarray(ds["mask"], dtype=np.float64)[idx]

    priors = np.empty_like(pi)
    with torch.no_grad():
        for s in range(0, take, args.batch):
            sl = idx[s:s + args.batch]
            xb = {k: v.to(dev) for k, v in build_batch(ds, sl, DEFAULT_KEYS).items()}
            _, logits = model(xb)
            lg = logits.cpu().numpy().astype(np.float64)
            mk = masks[s:s + len(sl)]
            lg = np.where(mk < 0.5, -1e9, lg)
            e = np.exp(lg - lg.max(axis=1, keepdims=True))
            priors[s:s + len(sl)] = e / e.sum(axis=1, keepdims=True)

    tv = 0.5 * np.abs(pi - priors).sum(axis=1)
    agree = (pi.argmax(axis=1) == priors.argmax(axis=1)).mean()
    h_pi, h_p = entropy(pi), entropy(priors)
    legal = (masks > 0.5).sum(axis=1)

    print(f"样本 {take}（来自 {args.data}）｜平均合法动作 {legal.mean():.1f}")
    print(f"  平均非零访问动作数 {(pi > 0).sum(axis=1).mean():.1f}｜"
          f"非零先验动作数 {(priors > 1e-6).sum(axis=1).mean():.1f}")
    print(f"  TV(π搜索, p先验)：均值 {tv.mean():.3f}｜中位 {np.median(tv):.3f}"
          f"｜p90 {np.percentile(tv, 90):.3f}｜>0.1 的占比 {(tv > 0.1).mean() * 100:.1f}%")
    print(f"  argmax 一致率 {agree * 100:.1f}%")
    print(f"  熵：搜索 {h_pi.mean():.3f}｜先验 {h_p.mean():.3f}"
          f"（先验更尖 → 搜索在摊平它 / 更平 → 搜索在聚焦它）")
    print("\n参考：早先测到的 TV(先验, 搜索) = 0.036。")
    print("  若这里 TV 仍≈0.03-0.05 → 自博弈数据几乎不含先验之外的信息 → 该改搜索侧（根噪声/叶值/sims）；")
    print("  若 TV 明显更大（>0.15）→ 数据里有信息，问题在训练侧。")


if __name__ == "__main__":
    main()
