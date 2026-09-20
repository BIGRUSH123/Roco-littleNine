# -*- coding: utf-8 -*-
"""native/tools/eval_bc_holdout.py — 逐队检查 BC 检查点的泛化。

bc_pretrain 只报整体验证指标（top1/top3/val_acc），看不出「泛化是不是崩在
某一支留出队上」。本工具用与训练**完全相同**的切分（同 seed / holdout-teams
/ random-val-frac），按 team_id 分组评估，输出每队一行的诊断表。

只读：加载已有 npz + ckpt，不做任何训练。

用法:
  python native/tools/eval_bc_holdout.py --data checkpoints/bc_data.npz \
      --ckpt checkpoints/bc_init.pt --holdout-teams 4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.engine.ai.bc_pretrain import holdout_split, load_dataset
from backend.engine.ai.core.mcts import NUM_ACTIONS
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.core.vocab import VOCAB_SIZE
from backend.engine.ai.data.meta_teams import load_meta_teams

_NON_OBS = ("action", "mask", "outcome", "game_id", "team_id", "is_meta")


def value_classes(v: np.ndarray, margin: float = 0.15) -> np.ndarray:
    """与训练/验证一致的胜负三分类：+1 胜 / 0 平 / -1 负。"""
    out = np.zeros_like(v, dtype=np.int64)
    out[v > margin] = 1
    out[v < -margin] = -1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="checkpoints/bc_data.npz")
    ap.add_argument("--ckpt", default="checkpoints/bc_init.pt")
    ap.add_argument("--holdout-teams", type=int, default=4)
    ap.add_argument("--random-val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=7, help="必须与训练时一致")
    ap.add_argument("--batch-size", type=int, default=512)
    ap.add_argument("--train-sample", type=int, default=20000,
                    help="训练集抽样条数（量化 in-sample 表现，0=跳过）")
    ap.add_argument("--device", default="")
    args = ap.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    ds = load_dataset(args.data)
    train_idx, val_idx, holdout = holdout_split(
        ds, args.holdout_teams, args.random_val_frac, args.seed)

    model = ModularBattleNet(trunk_dim=256, num_blocks=4, dropout=0.0,
                             vocab_size=VOCAB_SIZE, with_attention=True)
    model.load_state_dict(torch.load(args.ckpt, map_location="cpu"))
    model.to(device).eval()

    obs_keys = [k for k in ds if k not in _NON_OBS]
    n_val = len(val_idx)
    top1_hit = np.zeros(n_val, dtype=bool)
    top3_hit = np.zeros(n_val, dtype=bool)
    v_ok = np.zeros(n_val, dtype=bool)
    v_pred = np.zeros(n_val, dtype=np.float32)

    def evaluate(idx: np.ndarray, top1_out, top3_out, v_ok_out, v_pred_out) -> None:
        with torch.no_grad():
            for start in range(0, len(idx), args.batch_size):
                chunk = idx[start:start + args.batch_size]
                xb = {k: torch.from_numpy(ds[k][chunk]).to(device) for k in obs_keys}
                mv = torch.from_numpy(ds["mask"][chunk]).to(device)
                value, logits = model(xb)
                logits = logits.masked_fill(mv < 0.5, -1e9)
                target = torch.from_numpy(ds["action"][chunk]).to(device)
                top1 = logits.argmax(dim=-1)
                top3 = logits.topk(min(3, NUM_ACTIONS), dim=-1).indices
                vb = value.squeeze(1).cpu().numpy()
                top1_out[start:start + len(chunk)] = (top1 == target).cpu().numpy()
                top3_out[start:start + len(chunk)] = top3.eq(
                    target.unsqueeze(1)).any(dim=1).cpu().numpy()
                v_pred_out[start:start + len(chunk)] = vb
                v_ok_out[start:start + len(chunk)] = (
                    value_classes(vb) == value_classes(ds["outcome"][chunk])).astype(bool)

    evaluate(val_idx, top1_hit, top3_hit, v_ok, v_pred)

    # 训练集抽样：量化「背下来的成分」（in-sample 与 holdout 的落差）
    n_train_sample = min(args.train_sample, len(train_idx))
    train_sample = (np.random.default_rng(args.seed).permutation(train_idx)[:n_train_sample]
                    if n_train_sample else np.array([], dtype=np.int64))
    tr_top1 = np.zeros(len(train_sample), dtype=bool)
    tr_v_ok = np.zeros(len(train_sample), dtype=bool)
    tr_pred = np.zeros(len(train_sample), dtype=np.float32)
    if len(train_sample):
        evaluate(train_sample, tr_top1, np.zeros(len(train_sample), dtype=bool),
                 tr_v_ok, tr_pred)

    meta_names = {i: t["name"] for i, t in enumerate(load_meta_teams(None) or [])}
    team_id = ds["team_id"][val_idx]
    is_meta = ds["is_meta"][val_idx].astype(bool)
    groups: list[tuple[str, np.ndarray]] = []
    for tid in sorted(set(team_id[is_meta].tolist())):
        tag = f"meta[{tid}] {meta_names.get(int(tid), '?')}"
        groups.append((tag, is_meta & (team_id == tid)))
    groups.append(("random 随机阵容", ~is_meta))

    print(f"\n数据 {args.data}  检查点 {args.ckpt}  val={n_val} 持有 {len(holdout)} 队: {holdout}")
    print(f"{'分组':<34}{'样本':>7}{'p_top1':>9}{'p_top3':>8}{'val_acc':>9}{'v_mse':>9}")
    for tag, mask in groups:
        if not mask.any():
            continue
        o = ds["outcome"][val_idx][mask]
        print(f"{tag:<34}{int(mask.sum()):>7}{top1_hit[mask].mean():>9.3f}"
              f"{top3_hit[mask].mean():>8.3f}{v_ok[mask].mean():>9.3f}"
              f"{((v_pred[mask] - o) ** 2).mean():>9.4f}")

    # 留出队 vs 随机阵容 vs 训练集抽样：泛化落差与「背题」成分
    held = np.isin(team_id, holdout) & is_meta
    rand = ~is_meta
    print("\n=== 对照 ===")
    for tag, t1, t3, acc, n in (
            ("留出 meta 队（完全没见过）", top1_hit[held], top3_hit[held],
             v_ok[held], int(held.sum())),
            ("随机阵容", top1_hit[rand], top3_hit[rand], v_ok[rand], int(rand.sum())),
            (f"训练集抽样（in-sample）", tr_top1, np.zeros(0), tr_v_ok, len(train_sample))):
        if n:
            t3s = f"{t3.mean():.3f}" if t3.size else "  -  "
            print(f"  {tag:<28} n={n:>6}  p_top1={t1.mean():.3f}  p_top3={t3s}"
                  f"  val_acc={acc.mean():.3f}")
    if len(train_sample) and held.any():
        print(f"  训练集 - 留出队 top1 落差: "
              f"{tr_top1.mean() - top1_hit[held].mean():+.3f}"
              f"（差值大 = 记住了训练队伍的成分多）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
