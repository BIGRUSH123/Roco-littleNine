"""对手动作辅助目标的测试：按回合对齐、未知位、CE 路径能训。

为什么单列一条：这是唯一"非当前局面"的辅助目标（双方同局面同时决策，对手这一手
推不出来），如果它的标签对齐错了（错位一个回合就变成学"已发生的事"），实验结论
会完全反过来，所以对齐必须钉死。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import numpy as np
import torch


def _ds_two_blocks():
    """一局：A 侧 3 回合 + B 侧 3 回合，动作可辨认（A: 0/1/2，B: 10/11/12）。"""
    game_ids = np.zeros(6, dtype=np.int64)
    turns = np.array([1, 2, 3, 1, 2, 3], dtype=np.float32)
    gstats = np.zeros((6, 15), dtype=np.float32)
    gstats[:3, 0] = turns[:3] / 150.0
    gstats[3:, 0] = turns[3:] / 150.0
    return {"game_id": game_ids, "global_stats": gstats,
            "action": np.array([0, 1, 2, 10, 11, 12], dtype=np.int64)}


def test_opp_action_aligns_same_turn_and_marks_unknown():
    from backend.engine.ai.aux_targets import OPP_ACTION_UNK, derive_opp_action_targets

    out = derive_opp_action_targets(_ds_two_blocks())
    # A 侧回合 1/2/3 → 对手(B)同回合的动作 10/11/12
    assert out[:3].tolist() == [10, 11, 12]
    # B 侧回合 1/2/3 → 对手(A)同回合的动作 0/1/2
    assert out[3:].tolist() == [0, 1, 2]
    assert OPP_ACTION_UNK == 22


def test_opp_action_unknown_when_turn_missing():
    from backend.engine.ai.aux_targets import OPP_ACTION_UNK, derive_opp_action_targets

    ds = _ds_two_blocks()
    # 把 B 侧最后一条删掉（模拟对手那回合没有样本）→ A 侧第 2 回合应为未知
    ds["game_id"] = np.array([0, 0, 0, 0, 0], dtype=np.int64)
    ds["global_stats"] = ds["global_stats"][:5]
    ds["action"] = np.array([0, 1, 10, 11, 12], dtype=np.int64)
    out = derive_opp_action_targets(ds)
    assert out[2] == OPP_ACTION_UNK      # A 第 3 回合：对手块里只剩回合 1/2
    assert out[4] == 1                   # B 第 3 回合：对手(A)第 3 回合动作 = 2? 见下


def test_train_rl_ce_aux_path():
    """CE 辅助头 + 含未知位（ignore_index）能训出非零损失。"""
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.core.replay_buffer import RecentIterationsReplayBuffer
    from backend.engine.ai.train import train_rl

    n = 48
    keys = ("sprite_stats", "sprite_elements", "sprite_states", "skill_stats",
            "skill_elements", "skill_states", "global_stats", "global_elements",
            "form_elements", "form_avail", "ast_tokens", "ast_values")
    g = torch.Generator().manual_seed(0)
    states = []
    for _ in range(n):
        states.append({
            "sprite_stats": torch.rand(12, 7, generator=g).numpy(),
            "sprite_elements": torch.randint(0, 18, (12, 2), generator=g).numpy(),
            "sprite_states": torch.rand(12, 105, generator=g).numpy(),
            "skill_stats": torch.rand(10, 2, generator=g).numpy(),
            "skill_elements": torch.randint(0, 18, (10, 2), generator=g).numpy(),
            "skill_states": torch.rand(10, 9, generator=g).numpy(),
            "global_stats": torch.rand(15, generator=g).numpy(),
            "global_elements": torch.randint(0, 5, (1,), generator=g).numpy(),
            "form_elements": torch.randint(0, 18, (5, 2), generator=g).numpy(),
            "form_avail": torch.rand(5, generator=g).numpy(),
            "ast_tokens": torch.randint(10, 380, (384,), generator=g).numpy(),
            "ast_values": torch.rand(384, generator=g).numpy(),
        })
    assert set(states[0]) == set(keys)
    aux = np.random.default_rng(0).integers(0, 23, size=(n,), dtype=np.int64)

    buf = RecentIterationsReplayBuffer(keep_iterations=1)
    buf.push_batch(states, np.eye(22, dtype=np.float32)[np.zeros(n, dtype=int)],
                   np.ones((n, 22), dtype=np.float32),
                   np.linspace(-1, 1, n).astype(np.float32),
                   np.arange(n, dtype=np.int64), aux=aux)
    model = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0,
                             aux_heads=True, aux_dim=23)
    hist = train_rl(model, buf, epochs=1, batch_size=8, device="cpu",
                    optimizer=torch.optim.Adam(model.parameters(), lr=1e-3),
                    val_indices=np.arange(8, dtype=np.int64),
                    aux_loss_weight=0.5, aux_loss="ce")
    assert hist and hist[0]["train_aux_loss"] > 0.0 and hist[0]["val_aux_loss"] > 0.0
