"""probe_value_quality.py — 价值头的"预测质量"度量（辅助头方向的正经指标）。

背景：辅助头当初是为了治"价值头局内几乎没有区分度"（实测 TV(先验,搜索)=0.036、
被迫用 state_value 当叶值）。所以判它有没有用，看的不是 BC 的 policy top-1，而是：

  1. **胜负判别力**：中局状态能不能分出谁会赢（AUC / 准确率），并**按回合分桶**看
     （早期没信号很正常；关键是中后期）
  2. **区分度**：同一回合桶内 V 的标准差（std 越小 = 越"糊"）
  3. **校准**：V 与实际终局 margin 的相关性
  4. **辅助头自身**：各维 BCE vs "全预测常数"基线（看目标是否可学 / 是否被类别不平衡淹没）

参照：从状态里直接读"我方 6 只 vs 对方 6 只的血量总和之差"作为**无学习基线**。

用法:
  python -X utf8 native/tools/probe_value_quality.py --data checkpoints/bc_data_plan.npz \
      --ckpt checkpoints/bc20k_v4_rebase.pt [--ckpt ...] [--limit 40000]
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

DEFAULT_KEYS = ("sprite_stats", "sprite_elements", "sprite_states", "skill_stats",
                "skill_elements", "skill_states", "global_stats", "global_elements",
                "form_elements", "form_avail", "ast_tokens", "ast_values")
INT_KEYS = {"ast_tokens", "sprite_elements", "skill_elements", "global_elements",
            "form_elements"}


def auc(y_true: np.ndarray, score: np.ndarray) -> float:
    """ROC-AUC（Mann-Whitney U，允许并列）。"""
    y_true = np.asarray(y_true, dtype=bool)
    n_pos, n_neg = int(y_true.sum()), int((~y_true).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    ranks[order] = np.arange(1, len(score) + 1, dtype=np.float64)
    # 并列取平均秩
    s_sorted = np.asarray(score)[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return float((ranks[y_true].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def build_batch(ds: dict, idx: np.ndarray, keys) -> dict:
    import torch

    out = {}
    for k in keys:
        arr = ds[k][idx]
        if k in INT_KEYS:
            out[k] = torch.from_numpy(arr.astype(np.int64))
        else:
            out[k] = torch.from_numpy(np.asarray(arr, dtype=np.float32))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="checkpoints/bc_data_plan.npz")
    ap.add_argument("--ckpt", action="append", default=[])
    ap.add_argument("--limit", type=int, default=40000)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--device", default="")
    ap.add_argument("--bc-split", action="store_true",
                    help="只在 bc_pretrain 的 **val 半区** 取样评估（检查点没训过这些局）。"
                         "不加这个会因为训练局泄漏把 AUC 抬高 10+ 个点")
    args = ap.parse_args()

    import torch

    from backend.engine.ai.core.model import ModularBattleNet

    dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    ds = {k: v for k, v in np.load(args.data).items()}
    g = ds["global_stats"].astype(np.float32)
    turn = np.rint(g[:, 0] * 150.0).astype(np.int64)
    outcome = ds["outcome"].astype(np.float32)
    stats = ds["sprite_stats"].astype(np.float32)

    rng = np.random.default_rng(0)
    pool = np.arange(len(outcome))
    if args.bc_split:
        from backend.engine.ai.bc_pretrain import holdout_split

        _, pool, _ = holdout_split(ds, 1, 0.1, 7)
        print(f"[bc-split] 只在 val 半区评估（{len(pool)} 个样本，检查点没训过）")
    idx = np.sort(rng.choice(pool, size=min(args.limit, len(pool)), replace=False))
    print(f"数据 {args.data}｜取样 {len(idx)}｜回合 中位 {int(np.median(turn[idx]))}")

    y = outcome[idx] > 0.05           # 用"我方最终更优"当正类（含平局按负类处理）
    y_margin = outcome[idx]

    # 无学习基线：血量差（我方 6 只 HP 和 − 对方 6 只 HP 和）
    hp = stats[idx][:, :, 0]
    hp_diff = hp[:, :6].sum(axis=1) - hp[:, 6:].sum(axis=1)

    buckets = [("早期 turn<=10", turn[idx] <= 10),
               ("中局 11-30", (turn[idx] > 10) & (turn[idx] <= 30)),
               ("后期 >30", turn[idx] > 30)]

    def report(name: str, score: np.ndarray) -> None:
        overall = auc(y, score)
        cells = [f"AUC {overall:.3f}"]
        for label, mask in buckets:
            cells.append(f"{label} {auc(y[mask], score[mask]):.3f}(n={int(mask.sum())})")
        # 区分度：同桶内 std；校准：与终局 margin 的 Spearman
        std = float(np.std(score))
        corr = float(np.corrcoef(score, y_margin)[0, 1])
        print(f"  {name:<34} {' | '.join(cells)} | std={std:.3f} r={corr:+.3f}")

    print(f"\n{'模型':<34} 判别力 / 分桶")
    report("基线: 血量差(无学习)", hp_diff.astype(np.float64))

    for ckpt in args.ckpt:
        model = ModularBattleNet.load(ckpt, device=dev).eval()
        vals = np.empty(len(idx), dtype=np.float32)
        with torch.no_grad():
            for s in range(0, len(idx), args.batch):
                sl = idx[s:s + args.batch]
                xb = build_batch(ds, sl, DEFAULT_KEYS)
                xb = {k: (v.to(dev) if not k.startswith("hist") else v.to(dev))
                      for k, v in xb.items()}
                v, _ = model(xb)
                vals[s:s + len(sl)] = v.squeeze(1).cpu().numpy()
        ck = Path(ckpt).name
        flags = (f"slot={getattr(model,'slot_pool',False)} aux={getattr(model,'aux_heads',False)}"
                 f"(dim={getattr(model,'aux_dim',0)}) hist={getattr(model,'history',0)}")
        report(f"{ck} [{flags}]", vals.astype(np.float64))

        # 辅助头自身质量：BCE 看"是否优于常数基线"，CE 看 top-1 vs 多数类
        if getattr(model, "aux_heads", False):
            try:
                from backend.engine.ai.aux_targets import (
                    derive_aux_targets,
                    derive_opp_action_targets,
                )

                tgt = derive_opp_action_targets(ds) if getattr(model, "aux_dim", 0) == 23 \
                    else derive_aux_targets(ds)
            except Exception as exc:  # noqa: BLE001
                print(f"    (辅助标签算不出: {exc})")
                continue
            t = tgt[idx]
            logits = []
            with torch.no_grad():
                for s in range(0, len(idx), args.batch):
                    sl = idx[s:s + args.batch]
                    xb = {k: v.to(dev) for k, v in build_batch(ds, sl, DEFAULT_KEYS).items()}
                    _, _, a = model.forward_with_aux(xb)
                    if a is not None:
                        logits.append(a.cpu().numpy())
            if logits:
                pred = np.concatenate(logits, axis=0)
                if t.ndim == 1:
                    # 分类式辅助头（如"对手本回合动作"）：头宽 = 最大标签 + 1
                    # （标签里已含"未知"位 22，所以是 max+1 而不是 max+2）
                    if pred.shape[1] == int(t.max()) + 1:
                        y = t.reshape(-1)
                        known = y < pred.shape[1] - 1
                        if known.any():
                            top1 = pred[known].argmax(axis=1) == y[known]
                            _, counts = np.unique(y[known], return_counts=True)
                            majority = counts.max() / counts.sum()
                            print(f"    辅助头(CE)：已知 {known.mean() * 100:.1f}%｜"
                                  f"top-1 {top1.mean() * 100:.1f}% vs 多数类基线 "
                                  f"{majority * 100:.1f}%（提升 {(top1.mean() - majority) * 100:+.1f} pt）")
                elif pred.shape[1] == t.shape[1]:
                    eps = 1e-7
                    p = 1 / (1 + np.exp(-pred))
                    bce = -(t * np.log(p + eps) + (1 - t) * np.log(1 - p + eps)).mean(axis=0)
                    base = -(t * np.log(t.mean(axis=0) + eps)
                             + (1 - t) * np.log(1 - t.mean(axis=0) + eps)).mean(axis=0)
                    better = int((bce < base).sum())
                    print(f"    辅助头(BCE)：{better}/{len(bce)} 维优于常数基线｜"
                          f"正类比例 min/中位/max = "
                          f"{np.round([t.mean(axis=0).min(), np.median(t.mean(axis=0)), t.mean(axis=0).max()], 3)}")
            else:
                print("    (没拿到辅助头输出)")


if __name__ == "__main__":
    main()
