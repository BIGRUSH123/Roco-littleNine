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
    ap.add_argument("--trunk-dim", type=int, default=256,
                    help="trunk 宽度（容量对照实验；需被注意力头数整除）")
    ap.add_argument("--num-blocks", type=int, default=4)
    # ── v5 架构开关（默认关 = v4，老检查点/老基线可比） ──
    ap.add_argument("--slot-pool", action="store_true",
                    help="AST 按技能槽位分 10 段池化（保住槽位↔效果的绑定）")
    ap.add_argument("--aux-heads", action="store_true",
                    help="加辅助头（标签从数据离线推导，不重跑对局）")
    ap.add_argument("--aux-mode", choices=("structural", "final_hp", "opp_action"),
                    default="structural",
                    help="structural=赛制货币+短程动态(18维)；final_hp=旧版终局血量比(12维)；"
                         "opp_action=对手本回合的动作(23维/CE)——唯一携带"
                         "『非当前局面』信息的目标")
    ap.add_argument("--aux-weight", type=float, default=0.3,
                    help="辅助损失权重（仅在 --aux-heads 时生效）")
    ap.add_argument("--history", type=int, default=0,
                    help="回合历史长度 K（0=关）。历史特征从现有 npz 离线推导，"
                         "不用重跑对局数据")
    ap.add_argument("--value-loss", choices=("mse", "bce"), default="mse",
                    help="价值损失口径：mse=旧行为；bce=按胜率口径（(outcome+1)/2），"
                         "实测同特征下判别力更高")
    return ap.parse_args()


def derive_final_hp_targets(ds: dict[str, np.ndarray]) -> np.ndarray:
    """旧版辅助目标（终局血量比）——实现已搬到 `aux_targets`，这里只留转发以免老脚本断。"""
    from backend.engine.ai.aux_targets import derive_final_hp_targets as _impl

    return _impl(ds)


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
    # 走 `push_arrays`：数据集本来就是按 key 堆叠好的数组，逐样本建 dict 再 np.stack
    # 会多做一次全量拷贝（174568 样本 ≈ 1.8GB 瞬时内存 + 1.6s）；扩数据量时这点是内存余量。
    replay = RecentIterationsReplayBuffer(keep_iterations=1)
    # policy 目标：自博弈落盘的 npz 带 `policy`（MCTS 访问分布）→ 直接用它；
    # 只有 one-hot 的 BC 数据则按 `action` 现造。
    if "policy" in ds and ds["policy"].ndim == 2 and ds["policy"].shape[1] == NUM_ACTIONS:
        policy = np.asarray(ds["policy"], dtype=np.float32)
        print(f"policy 目标: MCTS 访问分布（自博弈样本）｜平均非零动作数 "
              f"{(policy > 0).sum(axis=1).mean():.1f}")
    else:
        policy = np.zeros((n, NUM_ACTIONS), dtype=np.float32)
        policy[np.arange(n), ds["action"]] = 1.0
        print("policy 目标: 动作 one-hot（BC 专家样本）")
    check_action_width("BC 数据集 mask", ds["mask"])
    aux_targets = None
    aux_dim = 0
    aux_loss_kind = "bce"
    if args.aux_heads and args.aux_weight > 0:
        if args.aux_mode == "final_hp":
            from backend.engine.ai.aux_targets import derive_final_hp_targets

            aux_targets = derive_final_hp_targets(ds)
            desc = "终局血量比(12，旧版)"
        elif args.aux_mode == "opp_action":
            from backend.engine.ai.aux_targets import (
                OPP_ACTION_DIM, OPP_ACTION_UNK, derive_opp_action_targets,
            )

            aux_targets = derive_opp_action_targets(ds)
            aux_dim = OPP_ACTION_DIM + 1        # 末位留给"未知"（CE 的 ignore_index）
            aux_loss_kind = "ce"
            known = int((aux_targets < OPP_ACTION_UNK).mean() * 100)
            top = np.bincount(aux_targets[aux_targets < OPP_ACTION_UNK],
                              minlength=OPP_ACTION_DIM)
            desc = (f"对手本回合动作({aux_dim}维/CE，已知 {known}%，"
                    f"最常见 {int(top.argmax())} 占 {top.max() / max(top.sum(), 1):.1%})")
        else:
            from backend.engine.ai.aux_targets import AUX_DIM, derive_aux_targets

            aux_targets = derive_aux_targets(ds)
            desc = f"赛制货币+短程动态({AUX_DIM})"
        aux_dim = aux_dim or int(aux_targets.shape[1] if aux_targets.ndim == 2 else 0)
        print(f"辅助标签: {desc} {aux_targets.shape}"
              f"｜损失 {aux_loss_kind}｜dim={aux_dim}")
    obs_extra = None
    if args.history > 0:
        from backend.engine.ai.history_features import build_history_arrays

        hist_feats, hist_acts = build_history_arrays(ds, args.history)
        obs_extra = {"hist_feats": hist_feats, "hist_actions": hist_acts}
        filled = float((hist_feats[:, -1, :].sum(axis=1) > 0).mean())
        print(f"回合历史: K={args.history} feats{hist_feats.shape} "
              f"acts{hist_acts.shape}｜最近一步非空的样本占比 {filled:.1%}")
    replay.push_arrays(
        {k: ds[k] for k in ds if k not in
         ("action", "mask", "outcome", "game_id", "team_id", "is_meta")},
        policy, ds["mask"], ds["outcome"], ds["game_id"],
        aux=aux_targets, obs_extra=obs_extra)

    model = ModularBattleNet(
        trunk_dim=args.trunk_dim, num_blocks=args.num_blocks, dropout=args.dropout,
        vocab_size=VOCAB_SIZE, with_attention=True,
        slot_pool=args.slot_pool, aux_heads=args.aux_heads,
        aux_dim=aux_dim or 12, history=args.history,
    )
    print(f"模型: trunk_dim={args.trunk_dim} num_blocks={args.num_blocks} "
          f"dropout={args.dropout} slot_pool={args.slot_pool} aux_heads={args.aux_heads}"
          f"(dim={aux_dim}) history={args.history} 参数量={model.num_params:,}")
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
        aux_loss_weight=args.aux_weight if args.aux_heads else 0.0,
        value_loss_mode=args.value_loss,
        aux_loss=aux_loss_kind,
    )
    if not history:
        raise SystemExit("训练未执行（样本不足或切分异常）")

    last = history[-1]
    aux_msg = (f"aux_loss={last.get('val_aux_loss', 0.0):.4f} "
               if args.aux_heads and args.aux_weight > 0 else "")
    print(f"\n完成: best_epoch={best['epoch']} "
          f"best_val_top1={best['top1']:.3f}  "
          f"final: v_loss={last['val_v_loss']:.4f} p_loss={last['val_p_loss']:.4f} "
          f"{aux_msg}"
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
        # 容量对照实验要能看出这盘权重是怎么训出来的
        "config": {
            "data": str(args.data),
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "weight_decay": args.weight_decay,
            "dropout": args.dropout,
            "trunk_dim": args.trunk_dim,
            "num_blocks": args.num_blocks,
            "slot_pool": bool(args.slot_pool),
            "aux_heads": bool(args.aux_heads),
            "aux_mode": args.aux_mode if args.aux_heads else "off",
            "aux_dim": int(aux_dim),
            "aux_loss_kind": aux_loss_kind,
            "aux_weight": args.aux_weight if args.aux_heads else 0.0,
            "value_loss": args.value_loss,
            "history": int(args.history),
            "seed": args.seed,
            "params": int(model.num_params),
        },
    }
    Path(args.out).with_suffix(".json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
