"""backend/engine/ai/bc_pretrain.py — 行为克隆预训练（自博弈微调的起点）。

输入: gen_bc_data.py 产出的 npz（编码状态 + 专家动作 one-hot + 掩码 + 终局胜负
     + game_id/team_id/is_meta）。

验证切分（整队留出）:
  - meta 对局: 留出最后 --holdout-teams 支队伍参与的全部对局 → 验证集，
    直接度量"没见过的队伍上的策略泛化"；
  - 随机阵容对局: 按完整对局随机抽 --random-val-frac 进验证集。

训练语义与 train_rl 完全一致（value MSE + masked policy CE），只把
MCTS 访问分布目标换成专家 one-hot。按验证集 policy top-1 保存最优权重。

用法:
  python -m backend.engine.ai.bc_pretrain --data checkpoints/bc_data.npz \
      --out checkpoints/bc_init.pt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from backend.engine.ai.core.mcts import NUM_ACTIONS
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.core.replay_buffer import (
    RecentIterationsReplayBuffer,
    check_action_width,
)
from backend.engine.ai.core.vocab import VOCAB_SIZE
from backend.engine.ai.train import train_rl

# 状态数组中需要转回训练 dtype 的键（保存时为省体积压成 f16/int16）
_INT_KEYS = {"ast_tokens", "sprite_elements", "skill_elements", "global_elements"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="checkpoints/bc_data.npz")
    ap.add_argument("--out", default="checkpoints/bc_init.pt")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--dropout", type=float, default=0.0)
    ap.add_argument("--holdout-teams", type=int, default=1,
                    help="留出作验证的 meta 队伍数（整队级泛化检验）")
    ap.add_argument("--random-val-frac", type=float, default=0.1)
    ap.add_argument("--device", default="")
    ap.add_argument("--seed", type=int, default=7)
    return ap.parse_args()


def load_dataset(path: str) -> dict[str, np.ndarray]:
    data = np.load(path)
    ds: dict[str, np.ndarray] = {}
    for key in data.files:
        arr = data[key]
        if key == "ast_tokens":
            arr = arr.astype(np.int64)
        elif key in _INT_KEYS:
            arr = arr.astype(np.int32)
        elif arr.dtype == np.float16:
            arr = arr.astype(np.float32)
        ds[key] = arr
    return ds


def holdout_split(
    ds: dict[str, np.ndarray],
    holdout_teams: int,
    random_val_frac: float,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """整队留出 + 随机对局抽样 → (train_idx, val_idx, 留出队伍 id 列表)。"""
    n = len(ds["outcome"])
    is_meta = ds["is_meta"].astype(bool)
    team_id = ds["team_id"]
    game_id = ds["game_id"]
    rng = np.random.default_rng(seed)

    meta_ids = sorted(set(team_id[is_meta].tolist()))
    val_mask = np.zeros(n, dtype=bool)
    if meta_ids:
        holdout = meta_ids[-holdout_teams:] if holdout_teams > 0 else []
        val_mask |= is_meta & np.isin(team_id, holdout)
    else:
        holdout = []
        print("!! 数据集中没有 meta 对局，整队留出退化为纯随机切分")

    random_games = np.unique(game_id[~is_meta])
    if len(random_games) and random_val_frac > 0:
        picked = rng.permutation(random_games)[: max(1, int(len(random_games) * random_val_frac))]
        val_mask |= ~is_meta & np.isin(game_id, picked)

    val_idx = np.flatnonzero(val_mask)
    train_idx = np.flatnonzero(~val_mask)
    if len(val_idx) == 0:
        raise SystemExit("验证集为空：检查 holdout-teams / random-val-frac")
    if len(train_idx) == 0:
        raise SystemExit("训练集为空：留出比例过大")
    return train_idx, val_idx, holdout


def main() -> None:
    args = parse_args()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")

    print(f"加载数据: {args.data}")
    ds = load_dataset(args.data)
    n = len(ds["outcome"])
    n_meta = int(ds["is_meta"].sum())
    print(f"样本 {n}（meta {n_meta} / random {n - n_meta}）")

    train_idx, val_idx, holdout = holdout_split(
        ds, args.holdout_teams, args.random_val_frac, args.seed)
    if holdout:
        print(f"整队留出验证: meta 队伍 {holdout} → val {len(val_idx)} 样本")
    print(f"train={len(train_idx)}  val={len(val_idx)}")

    # ── replay buffer（复用自博弈的批量取数与训练循环）──
    replay = RecentIterationsReplayBuffer(keep_iterations=1)
    states = [{k: ds[k][i] for k in ds if k not in
               ("action", "mask", "outcome", "game_id", "team_id", "is_meta")}
              for i in range(n)]
    policy = np.zeros((n, NUM_ACTIONS), dtype=np.float32)
    policy[np.arange(n), ds["action"]] = 1.0
    check_action_width("BC 数据集 mask", ds["mask"])
    replay.push_batch(states, policy, ds["mask"].astype(np.float32),
                      ds["outcome"].astype(np.float32), ds["game_id"])

    model = ModularBattleNet(
        trunk_dim=256, num_blocks=4, dropout=args.dropout,
        vocab_size=VOCAB_SIZE, with_attention=True,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr,
                                 weight_decay=args.weight_decay)

    best = {"top1": -1.0, "epoch": -1}

    def on_epoch(stats: dict, mdl: ModularBattleNet) -> None:
        if stats["val_policy_top1"] > best["top1"]:
            best["top1"] = stats["val_policy_top1"]
            best["epoch"] = stats["epoch"]
            # 走标准 save()（含 state_dict + 结构元数据）：裸 state_dict 会让
            # ModularBattleNet.load 读不了，评估/部署路径全断（2026-09-20 踩过）
            mdl.save(args.out)

    history = train_rl(
        model, replay,
        epochs=args.epochs, batch_size=args.batch_size, device=device,
        optimizer=optimizer,
        val_indices=val_idx,
        on_epoch=on_epoch,
    )
    if not history:
        raise SystemExit("训练未执行（样本不足或切分异常）")

    last = history[-1]
    print(f"\n完成: best_epoch={best['epoch']} "
          f"best_val_top1={best['top1']:.3f}  "
          f"final: v_loss={last['val_v_loss']:.4f} p_loss={last['val_p_loss']:.4f} "
          f"top1={last['val_policy_top1']:.3f} top3={last['val_policy_top3']:.3f} "
          f"val_acc={last['val_acc']:.3f}")
    print(f"最优权重已保存: {args.out}")

    # 留出队伍逐队诊断（泛化是否崩，直接看每支留出队）
    sidecar = {
        "best_epoch": best["epoch"],
        "best_val_policy_top1": best["top1"],
        "final_history": {k: float(last[k]) for k in last},
        "holdout_teams": holdout,
        "samples": int(n),
    }
    Path(args.out).with_suffix(".json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
