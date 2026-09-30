# -*- coding: utf-8 -*-
"""probe_trunk_linear.py — 决定性一测：信息在主干里、还是根本没进来？

拿已训练网络的**主干输出**（最后一个残差块之后的 256 维 h），冻结后只训一个逻辑回归：

  - 若 h 上的线性探针 AUC ≈ 网络价值头 AUC  → 价值头没浪费信息，缺的是**特征本身**
    （当前状态特征的天花板 0.82）→ 加"当前状态类"的辅助标签不会有任何用，
    要涨必须加**时序 / 对手意图**信息
  - 若 h 上的线性探针 AUC 明显更高       → 信息在主干里，价值头没取出来 →
    辅助头/价值头结构有救，方向是把 aux 的梯度接到价值路径上
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

from native.tools.probe_value_ceiling import fit_logreg  # noqa: E402
from native.tools.probe_value_quality import DEFAULT_KEYS, auc, build_batch  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="checkpoints/bc_data_plan.npz")
    ap.add_argument("--limit", type=int, default=40000)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--bc-split", action="store_true",
                    help="用 bc_pretrain 的 train/val 切分：探针只在 train 半区拟合、"
                         "模型与探针都在 val 半区评估（否则会有训练局泄漏）")
    args = ap.parse_args()

    import torch

    from backend.engine.ai.core.model import ModularBattleNet

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = ModularBattleNet.load(args.ckpt, device=dev).eval()

    # 抓主干最后一块的输出（= 价值头的输入 h）
    captured: dict[str, torch.Tensor] = {}

    def hook(_mod, _inp, out):
        captured["h"] = out.detach()

    handle = model.blocks[-1].register_forward_hook(hook)

    ds = {k: v for k, v in np.load(args.data).items()}
    outcome = ds["outcome"].astype(np.float32)
    if args.bc_split:
        # 复现 bc_pretrain 的切分（seed/比例与训练时一致）→ val 半区是模型没见过的局
        from backend.engine.ai.bc_pretrain import holdout_split

        train_idx, val_idx, _ = holdout_split(ds, 1, 0.1, 7)
        rng = np.random.default_rng(0)
        take = min(args.limit // 2, len(val_idx))
        val_idx = np.sort(rng.choice(val_idx, size=take, replace=False))
        take_tr = min(args.limit // 2, len(train_idx))
        train_idx = np.sort(rng.choice(train_idx, size=take_tr, replace=False))
        idx = np.concatenate([train_idx, val_idx])
        n_tr = len(train_idx)
        print(f"[bc-split] train {n_tr} / val {len(val_idx)}（val 半区模型没训过）")
    else:
        rng = np.random.default_rng(0)
        idx = np.sort(rng.choice(len(outcome), size=min(args.limit, len(outcome)),
                                 replace=False))
        n_tr = int(len(idx) * 0.6)
    y = (outcome[idx] > 0.05).astype(np.float64)
    turn = np.rint(ds["global_stats"].astype(np.float32)[idx][:, 0] * 150).astype(int)

    hs, vals = [], np.empty(len(idx), dtype=np.float32)
    with torch.no_grad():
        for s in range(0, len(idx), args.batch):
            sl = idx[s:s + args.batch]
            xb = {k: v.to(dev) for k, v in build_batch(ds, sl, DEFAULT_KEYS).items()}
            v, _ = model(xb)
            vals[s:s + len(sl)] = v.squeeze(1).cpu().numpy()
            hs.append(captured["h"].float().cpu().numpy())
    handle.remove()
    h = np.concatenate(hs, axis=0)

    if args.bc_split:
        tr, te = np.arange(0, n_tr), np.arange(n_tr, len(idx))
    else:
        perm = np.random.default_rng(1).permutation(len(idx))
        tr, te = perm[:n_tr], perm[n_tr:]
    pred, _ = fit_logreg(h[tr], y[tr], steps=500, lr=0.5)

    # 对照：同样的主干特征，但用**网络训练时的软标签**（终局 outcome ∈ [-1,1]）做回归。
    # 若它的 AUC 也掉到 ≈ 网络水平，那"头没取出信息"是假象，真正的问题是**标签选择**。
    mu, sd = h[tr].mean(0), h[tr].std(0) + 1e-6
    hs_tr = (h[tr] - mu) / sd
    w_soft = np.linalg.lstsq(hs_tr, outcome[idx][tr], rcond=None)[0]
    soft_te = ((h[te] - mu) / sd) @ w_soft

    print(f"ckpt {Path(args.ckpt).name}｜样本 {len(idx)}｜主干维度 {h.shape[1]}")
    print(f"  网络价值头 AUC        = {auc(y[te] > 0.5, vals[te]):.3f}")
    print(f"  主干 h 的线性探针 AUC = {auc(y[te] > 0.5, pred(h[te])):.3f}"
          f"（标签=二值胜负）")
    print(f"  主干 h 的软标签回归 AUC = {auc(y[te] > 0.5, soft_te):.3f}"
          f"（标签=终局 outcome，与网络训练同口径）")
    # 过拟合检查：同一个头在 train / test 上的 AUC 差
    print(f"  价值头 train/test AUC = {auc(y[tr] > 0.5, vals[tr]):.3f} / "
          f"{auc(y[te] > 0.5, vals[te]):.3f}"
          f"（差 {auc(y[tr] > 0.5, vals[tr]) - auc(y[te] > 0.5, vals[te]):+.3f}）")
    print(f"  探针   train/test AUC = {auc(y[tr] > 0.5, pred(h[tr])):.3f} / "
          f"{auc(y[te] > 0.5, pred(h[te])):.3f}")
    for label, mask in (("早期 ≤10", turn <= 10), ("中局 11-30", (turn > 10) & (turn <= 30)),
                        ("后期 >30", turn > 30)):
        m = mask[te]
        print(f"    {label}: 网络 {auc(y[te][m] > 0.5, vals[te][m]):.3f}"
              f"  主干探针 {auc(y[te][m] > 0.5, pred(h[te][m])):.3f} (n={int(m.sum())})")


if __name__ == "__main__":
    main()
