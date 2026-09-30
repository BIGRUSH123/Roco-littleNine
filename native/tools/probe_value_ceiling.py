"""probe_value_ceiling.py — 当前状态特征到底能支撑多高的胜负判别力？

做法：手工构造一组"看得见的局面特征"（血量/存活/魔力/能量/印记/道具/回合），
用**逻辑回归**拟合终局胜负（无隐层、无时序），与网络价值头在同一批样本上的 AUC 对比：

  - 若 LR ≈ 网络 → 网络已经把这些当前特征榨干，再加"当前状态类"的辅助标签也不会涨，
    要涨只能加**时序/对手意图**信息（v6 的回合历史方向）
  - 若 LR 明显更高 → 网络没学好，问题在优化/容量，辅助标签还有空间
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

from native.tools.probe_value_quality import auc  # noqa: E402  (同一份 AUC 实现)


def hand_features(ds: dict, idx: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """只用"当前状态里看得见"的量：血量/存活/魔力/能量/印记/道具/回合。"""
    st = ds["sprite_stats"].astype(np.float32)[idx]
    g = ds["global_stats"].astype(np.float32)[idx]
    hp = st[:, :, 0]
    mx = np.maximum(st[:, :, 1], 1.0)
    own_hp, opp_hp = hp[:, :6], hp[:, 6:]
    feats, names = [], []

    def add(name: str, v: np.ndarray) -> None:
        feats.append(np.asarray(v, dtype=np.float64).reshape(len(idx), -1))
        names.append(name)

    add("血量比差", own_hp.sum(1) / mx[:, :6].sum(1) - opp_hp.sum(1) / mx[:, 6:].sum(1))
    add("存活差", (own_hp > 0).sum(1) - (opp_hp > 0).sum(1))
    add("魔力差", g[:, 2] - g[:, 3])
    add("回合", g[:, 0])
    add("天气", g[:, 1])
    add("印记(己)", g[:, 4] - g[:, 5])
    add("印记(敌)", g[:, 6] - g[:, 7])
    add("道具可用(己)", g[:, 9])
    add("道具可用(敌)", g[:, 12] if g.shape[1] > 12 else np.zeros(len(idx)))
    add("场上血比(己)", hp[:, 0] / mx[:, 0])
    add("场上血比(敌)", hp[:, 6] / mx[:, 6])
    add("己方最低血比", (own_hp / mx[:, :6]).min(1))
    add("敌方最低血比", (opp_hp / mx[:, 6:]).min(1))
    return np.concatenate(feats, axis=1), names


def fit_logreg(x: np.ndarray, y: np.ndarray, steps: int = 400, lr: float = 0.5):
    """标准化 + 带 L2 的梯度下降（不依赖 sklearn）。"""
    mu, sd = x.mean(0), x.std(0) + 1e-6
    xs = (x - mu) / sd
    w = np.zeros(xs.shape[1])
    b = 0.0
    for _ in range(steps):
        z = xs @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        grad_w = xs.T @ (p - y) / len(y) + 1e-3 * w
        grad_b = float((p - y).mean())
        w -= lr * grad_w
        b -= lr * grad_b
    return (lambda xx: ((xx - mu) / sd) @ w + b), w


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="checkpoints/bc_data_plan.npz")
    ap.add_argument("--limit", type=int, default=40000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bc-split", action="store_true",
                    help="只在 bc_pretrain 的 val 半区取样（否则网络一侧会被训练局泄漏抬高）")
    ap.add_argument("--ckpt", action="append", default=[])
    args = ap.parse_args()

    ds = {k: v for k, v in np.load(args.data).items()}
    outcome = ds["outcome"].astype(np.float32)
    rng = np.random.default_rng(args.seed)
    pool = np.arange(len(outcome))
    if args.bc_split:
        from backend.engine.ai.bc_pretrain import holdout_split

        _, pool, _ = holdout_split(ds, 1, 0.1, 7)
        print(f"[bc-split] 只在 val 半区取样（{len(pool)} 个样本）")
    idx = np.sort(rng.choice(pool, size=min(args.limit, len(pool)), replace=False))
    y = (outcome[idx] > 0.05).astype(np.float64)

    x, names = hand_features(ds, idx)
    rng2 = np.random.default_rng(args.seed + 1)
    perm = rng2.permutation(len(idx))
    cut = int(len(idx) * 0.6)
    tr, te = perm[:cut], perm[cut:]

    pred, w = fit_logreg(x[tr], y[tr])
    print(f"数据 {args.data}｜样本 {len(idx)}（train {cut} / test {len(idx) - cut}）")
    print(f"\n手工特征 {x.shape[1]} 维 → 逻辑回归（无隐层、无时序）")
    print(f"  全测试集 AUC = {auc(y[te] > 0.5, pred(x[te])):.3f}")
    order = np.argsort(-np.abs(w))
    print("  权重最大的 6 个特征：", [(names[o], round(float(w[o]), 2)) for o in order[:6]])

    # 分回合桶
    turn = np.rint(ds["global_stats"].astype(np.float32)[idx][:, 0] * 150).astype(int)
    for label, mask in (("早期 ≤10", turn <= 10), ("中局 11-30", (turn > 10) & (turn <= 30)),
                        ("后期 >30", turn > 30)):
        m = mask[te]
        print(f"  {label}: AUC {auc(y[te][m] > 0.5, pred(x[te][m])):.3f} (n={int(m.sum())})")

    # 网络对照（同一批测试样本）
    if args.ckpt:
        import torch

        from backend.engine.ai.core.model import ModularBattleNet
        from native.tools.probe_value_quality import DEFAULT_KEYS, build_batch  # noqa: PLC0415

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        print("\n网络对照（同测试集）：")
        for ckpt in args.ckpt:
            model = ModularBattleNet.load(ckpt, device=dev).eval()
            vals = np.empty(len(idx), dtype=np.float32)
            with torch.no_grad():
                for s in range(0, len(idx), 512):
                    sl = idx[s:s + 512]
                    xb = {k: v.to(dev) for k, v in build_batch(ds, sl, DEFAULT_KEYS).items()}
                    v, _ = model(xb)
                    vals[s:s + len(sl)] = v.squeeze(1).cpu().numpy()
            print(f"  {Path(ckpt).name:<32} AUC {auc(y[te] > 0.5, vals[te]):.3f}")


if __name__ == "__main__":
    main()
