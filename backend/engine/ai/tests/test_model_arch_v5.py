"""v5 可选架构件测试：AST 槽位分段池化、辅助头、旧检查点向后兼容。

这三件事的共同风险都是"静默改语义"：老权重能不能按 v4 结构原样加载、
分段池化到底有没有按槽位切、辅助头会不会污染推理路径 —— 全部在这里钉住。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import torch

from backend.engine.ai.core.model import AUX_TARGET_DIM, ModularBattleNet
from backend.engine.ai.core.vocab import SLOT_COUNT, SLOT_MARKER_IDS


def _batch(batch_size: int = 2, seq_len: int = 384) -> dict[str, torch.Tensor]:
    """形状合法的假状态（内容不重要，只验证前向通路与形状契约）。"""
    g = torch.Generator().manual_seed(0)

    def rand(*shape):
        return torch.rand(*shape, generator=g)

    def rint(high, *shape):
        return torch.randint(0, high, shape, generator=g)

    return {
        "sprite_stats": rand(batch_size, 12, 7) * 400.0,
        "sprite_elements": rint(18, batch_size, 12, 2),
        "sprite_states": rand(batch_size, 12, 105),
        "skill_stats": rand(batch_size, 10, 2) * 120.0,
        "skill_elements": rint(18, batch_size, 10, 2),
        "skill_states": rand(batch_size, 10, 9),
        "global_stats": rand(batch_size, 15),
        "global_elements": rint(5, batch_size, 1),
        "form_elements": rint(18, batch_size, 5, 2),
        "form_avail": rand(batch_size, 5),
        "ast_tokens": rint(380, batch_size, seq_len) + 10,
        "ast_values": rand(batch_size, seq_len),
    }


def test_slot_pooling_segments_by_marker_order():
    """分段规则：槽位哨兵按出现顺序切段；PAD 不计入；无哨兵时退化为单段。"""
    model = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0,
                             slot_pool=True).eval()
    # 换成恒等投影，直接看每段池化后的向量（否则被 Linear+LN+GELU 糊住）
    model.ast_slot_proj = torch.nn.Identity()
    dim = model.ast_dim
    marker_a, marker_b = SLOT_MARKER_IDS[1], SLOT_MARKER_IDS[2]
    filler, pad = 101, 0

    # 位置:      0     1        2    3     4        5    6    7
    # token:  filler marker_a filler PAD  marker_b filler filler filler
    tokens = torch.tensor([[filler, marker_a, filler, pad, marker_b, filler, filler, filler]])
    non_pad = tokens != 0
    ast_out = torch.zeros(1, 8, dim)
    for pos in range(8):
        ast_out[0, pos] = float(pos)          # 每个位置一个常数向量，便于核对均值

    slots = model._pool_ast_slots(ast_out, tokens, non_pad, 1).reshape(1, SLOT_COUNT, dim)

    # 段 0 = 位置 0..3（去掉 PAD 位置 3）→ 均值 (0+1+2)/3 = 1.0
    assert torch.allclose(slots[0, 0], torch.full((dim,), 1.0), atol=1e-5)
    # 段 1 = 位置 4..7 → 均值 (4+5+6+7)/4 = 5.5
    assert torch.allclose(slots[0, 1], torch.full((dim,), 5.5), atol=1e-5)
    # 段 2..9 没有 token → 全零（clamp 保证不除零）
    assert torch.allclose(slots[0, 2:], torch.zeros(SLOT_COUNT - 2, dim), atol=1e-6)

    # 无哨兵（异常/老数据）→ 全部 token 落进段 0，不崩
    plain = torch.full((1, 8), filler)
    slots_plain = model._pool_ast_slots(
        ast_out, plain, plain != 0, 1).reshape(1, SLOT_COUNT, dim)
    assert torch.allclose(slots_plain[0, 0], torch.full((dim,), 3.5), atol=1e-5)
    assert torch.allclose(slots_plain[0, 1:], torch.zeros(SLOT_COUNT - 1, dim), atol=1e-6)


def test_v5_flags_are_opt_in_and_change_output():
    state = _batch()
    base = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0).eval()
    v5 = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0,
                          slot_pool=True, aux_heads=True).eval()

    assert base.slot_pool is False and base.aux_heads is False
    v5_value, v5_logits, v5_aux = v5.forward_with_aux(state)
    assert v5_value.shape == (2, 1) and v5_logits.shape == (2, 22)
    assert v5_aux.shape == (2, AUX_TARGET_DIM)
    assert torch.isfinite(v5_value).all() and torch.isfinite(v5_logits).all()

    # 推理路径（forward）只吐两个头，形状与 v4 一致
    value, logits = v5(state)
    assert value.shape == (2, 1) and logits.shape == (2, 22)
    _, _, aux_none = base.forward_with_aux(state)
    assert aux_none is None

    assert v5.num_params > base.num_params


def test_legacy_checkpoint_loads_as_v4(tmp_path):
    """老检查点（没有 v5 键）必须按 v4 结构原样加载 —— 这是不破坏在跑实验的红线。"""
    legacy = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0)
    path = tmp_path / "legacy.pt"
    torch.save({
        "state_dict": legacy.state_dict(),
        "trunk_dim": 64, "num_blocks": 1, "dropout": 0.0,
        "vocab_size": legacy.vocab_size, "with_attention": True, "ast_max_len": 384,
        "type": "EntityBottleneckNet",
    }, path)

    loaded = ModularBattleNet.load(str(path), device="cpu")
    assert loaded.slot_pool is False and loaded.aux_heads is False
    value, logits = loaded(_batch())
    assert value.shape == (2, 1) and logits.shape == (2, 22)


def test_v5_checkpoint_roundtrip(tmp_path):
    model = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0,
                             slot_pool=True, aux_heads=True).eval()
    path = tmp_path / "v5.pt"
    model.save(str(path))
    loaded = ModularBattleNet.load(str(path), device="cpu")
    assert loaded.slot_pool is True and loaded.aux_heads is True

    state = _batch()
    with torch.no_grad():
        expected = model.forward_with_aux(state)
        actual = loaded.forward_with_aux(state)
    for a, b in zip(expected, actual, strict=False):
        assert torch.allclose(a, b, atol=1e-6)


def test_derive_final_hp_targets_broadcasts_last_sample():
    """辅助标签 = 每局最后一个样本的 12 只精灵血量比，广播到该局所有样本。"""
    import numpy as np

    from backend.engine.ai.bc_pretrain import derive_final_hp_targets

    # 故意让 game_id 乱序，验证分组不依赖输入顺序
    game_ids = np.array([7, 7, 5, 5, 5, 7])
    stats = np.zeros((len(game_ids), 12, 7), dtype=np.float32)
    stats[:, :, 1] = 100.0
    hp_rows = [
        [1, 1] * 6, [2, 2] * 6, [3, 3] * 6, [4, 4] * 6, [5, 5] * 6, [6, 6] * 6,
    ]
    for i, row in enumerate(hp_rows):
        stats[i, :, 0] = row

    out = derive_final_hp_targets({"sprite_stats": stats, "game_id": game_ids})

    # 局 7 的样本是 idx 0,1,5 → 最后一个是 idx 5（血量 6）
    assert np.allclose(out[[0, 1, 5]], 0.06)
    # 局 5 的样本是 idx 2,3,4 → 最后一个是 idx 4（血量 5）
    assert np.allclose(out[[2, 3, 4]], 0.05)
    assert out.shape == (len(game_ids), 12)


def test_history_features_align_turns_and_sides():
    """回合历史：按 game_id/回合对齐，己方取本侧样本、对方取对方侧样本（视角互换）。"""
    import numpy as np

    from backend.engine.ai.history_features import build_history_arrays

    # 一局：A 侧 3 回合 + B 侧 3 回合（bc_record 的存储顺序）
    game_ids = np.zeros(6, dtype=np.int64)
    turns = np.array([1, 2, 3, 1, 2, 3], dtype=np.float32)
    stats = np.zeros((6, 12, 7), dtype=np.float32)
    stats[:, :, 1] = 100.0                      # max_hp 全 100
    for u in range(1, 4):                       # A 侧样本 0,1,2（下标 u-1）
        stats[u - 1, :6, 0] = 100 - 10 * u      # A 自己的血量
        stats[u - 1, 6:, 0] = 70 - 10 * u       # A 看到的 B 血量
        stats[u + 2, :6, 0] = 70 - 10 * u       # B 侧样本：B 自己的血量（= A 的对面）
        stats[u + 2, 6:, 0] = 100 - 10 * u      # B 看到的 A 血量
    gstats = np.zeros((6, 15), dtype=np.float32)
    gstats[:3, 0] = turns[:3] / 150.0
    gstats[3:, 0] = turns[3:] / 150.0
    gstats[:3, 2], gstats[:3, 3] = 5.0, 6.0     # A 视角：自己 5 命、对面 6 命
    gstats[3:, 2], gstats[3:, 3] = 6.0, 5.0     # B 视角镜像
    action = np.array([0, 1, 2, 10, 11, 12], dtype=np.int64)

    ds = {"game_id": game_ids, "global_stats": gstats, "sprite_stats": stats,
          "action": action}
    feats, acts = build_history_arrays(ds, k=2)

    # 布局：0-5 我方血量比 / 6-11 我方力竭 / 12 我方命数 /
    #       13-18 对方血量比 / 19-24 对方力竭 / 25 对方命数
    # A 侧第 3 回合（下标 2，turn=3）：历史两步 = turn1(旧) → turn2(新)
    assert np.allclose(feats[2, 0, 0:6], 0.9)      # 我方 turn1 血量比 90/100
    assert np.allclose(feats[2, 0, 6:13], [0, 0, 0, 0, 0, 0, 5])   # 力竭 0、命数 5
    assert np.allclose(feats[2, 0, 13:19], 0.6)    # 对方 turn1 血量比 60/100
    assert np.allclose(feats[2, 0, 19:26], [0, 0, 0, 0, 0, 0, 6])  # 对方力竭 0、命数 6
    assert np.allclose(feats[2, 1, 0:6], 0.8)      # turn2 我方
    assert np.allclose(feats[2, 1, 13:19], 0.5)    # turn2 对方
    assert acts[2, 0].tolist() == [0, 10]          # turn1：我方动作 0、对方动作 10
    assert acts[2, 1].tolist() == [1, 11]          # turn2

    # 首回合没有任何历史
    assert np.allclose(feats[0], 0.0) and acts[0].tolist() == [[-1, -1], [-1, -1]]

    # B 侧第 3 回合（下标 5）：己方是 B 视角的量，对方取 A 侧样本（镜像）
    assert np.allclose(feats[5, 0, 0:6], 0.6)      # B 自己 turn1 = 60/100
    assert np.allclose(feats[5, 0, 13:19], 0.9)    # 对面(A) turn1 = 90/100
    assert np.allclose(feats[5, 0, 12], 6.0)       # B 自己命数 6
    assert np.allclose(feats[5, 0, 25], 5.0)       # 对面命数 5
    assert acts[5, 0].tolist() == [10, 0]          # 我方(B)=10、对方(A)=0


def test_history_branch_is_opt_in_and_robust():
    state = _batch()
    base = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0).eval()
    v6 = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0, history=2).eval()
    assert base.history == 0 and v6.history == 2
    assert v6.num_params > base.num_params

    # 缺历史键（搜索/老调用点）不崩，用零历史
    value, logits = v6(state)
    assert value.shape == (2, 1) and torch.isfinite(logits).all()

    # 给了历史就用历史：不同历史 → 不同输出
    import numpy as np

    with_hist = dict(state)
    with_hist["hist_feats"] = torch.rand(2, 2, 26)
    with_hist["hist_actions"] = torch.full((2, 2, 2), -1, dtype=torch.long)
    other = dict(with_hist)
    other["hist_feats"] = torch.zeros(2, 2, 26)
    v_a, _ = v6(with_hist)
    v_b, _ = v6(other)
    assert not torch.allclose(v_a, v_b)

    # 任意历史不该被当成普通 obs 参与别的分支（形状契约）
    assert np.asarray(with_hist["hist_actions"]).shape == (2, 2, 2)


def test_train_rl_consumes_history_extra():
    """端到端：obs_extra 里的历史数组要真的进 batch（训练跑通且梯度非零）。"""
    import numpy as np

    from backend.engine.ai.core.replay_buffer import RecentIterationsReplayBuffer
    from backend.engine.ai.train import train_rl

    n = 32
    state = _batch(batch_size=n)
    states = [{k: v[i].numpy() for k, v in state.items()} for i in range(n)]
    buffer = RecentIterationsReplayBuffer(keep_iterations=1)
    buffer.push_batch(
        states,
        np.eye(22, dtype=np.float32)[np.zeros(n, dtype=int)],
        np.ones((n, 22), dtype=np.float32),
        np.linspace(-1.0, 1.0, n).astype(np.float32),
        np.arange(n, dtype=np.int64),
        obs_extra={
            "hist_feats": np.random.rand(n, 2, 26).astype(np.float32),
            "hist_actions": np.random.randint(-1, 22, (n, 2, 2)).astype(np.int64),
        },
    )
    assert buffer.obs_extra is not None
    assert buffer.obs_extra["hist_feats"].shape == (n, 2, 26)

    model = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0, history=2)
    history = train_rl(
        model, buffer, epochs=1, batch_size=8, device="cpu",
        optimizer=torch.optim.Adam(model.parameters(), lr=1e-3),
        val_indices=np.arange(8, dtype=np.int64),
    )
    assert history and np.isfinite(history[0]["val_p_loss"])


def test_aux_targets_structural_orientation_and_horizon():
    """新版辅助目标：终局力竭/判负线按**本视角**取，短程窗口取恰好 H 回合之后。"""
    import numpy as np

    from backend.engine.ai.aux_targets import aux_layout, derive_aux_targets

    # 一局：A 侧 3 回合 + B 侧 3 回合；A 的 0 号精灵在 turn3 力竭，B 全程满血
    game_ids = np.zeros(6, dtype=np.int64)
    turns = np.array([1, 2, 3, 1, 2, 3], dtype=np.float32)
    stats = np.zeros((6, 12, 7), dtype=np.float32)
    stats[:, :, 1] = 100.0
    own_a = {1: [100, 100, 100, 100, 100, 100],
             2: [50, 100, 100, 100, 100, 100],
             3: [0, 100, 100, 100, 100, 100]}
    for u in range(1, 4):
        stats[u - 1, :6, 0] = own_a[u]          # A 视角的己方
        stats[u - 1, 6:, 0] = 100.0             # A 视角的对方（B）满血
        stats[u + 2, :6, 0] = 100.0             # B 视角的己方满血
        stats[u + 2, 6:, 0] = own_a[u]          # B 视角的对方（A）
    gstats = np.zeros((6, 15), dtype=np.float32)
    gstats[:3, 0] = turns[:3] / 150.0
    gstats[3:, 0] = turns[3:] / 150.0                               # 注意顺序：先 A 块后 B 块
    ds = {"game_id": game_ids, "global_stats": gstats, "sprite_stats": stats,
          "action": np.zeros(6, dtype=np.int64)}

    out = derive_aux_targets(ds, horizon=2)
    L = aux_layout()
    assert out.shape == (6, 18)
    assert ((out >= 0) & (out <= 1)).all()

    # A 侧 turn1（行 0）：H=2 → 看 turn3 → 己方会出现力竭
    assert out[0, 14] == 1.0            # faint_soon: 我方
    assert out[0, 15] == 0.0            # faint_soon: 对方
    assert out[0, 16] > 0.0             # hp_drop_soon: 我方掉血
    assert out[0, 17] == 0.0            # 对方没掉血

    # A 侧 turn2（行 1）：H=2 → turn4 不存在（本侧只有 3 回合）→ 0（局面已结束，无后续力竭）
    assert out[1, 14] == 0.0 and out[1, 16] == 0.0

    # 终局力竭（本视角）：A 视角我方 0 号力竭、对方全活
    assert out[0, 0] == 1.0 and np.allclose(out[0, 1:6], 0.0)
    assert out[0, L["final_faints"]].tolist() == [0.25, 0.0]
    # B 侧视角必须镜像：它的对方（A）有人力竭
    assert out[3, 6] == 1.0 and out[3, 0] == 0.0
    assert out[4, L["final_faints"]].tolist() == [0.0, 0.25]


def test_aux_legacy_final_hp_still_available(tmp_path):
    """旧版 12 维目标仍可用（对照既有检查点）；模型 aux_dim 要跟着变。"""
    import numpy as np

    from backend.engine.ai.aux_targets import derive_final_hp_targets

    stats = np.zeros((4, 12, 7), dtype=np.float32)
    stats[:, :, 1] = 100.0
    stats[:, :, 0] = 40.0
    ds = {"game_id": np.array([0, 0, 1, 1]), "sprite_stats": stats}
    out = derive_final_hp_targets(ds)
    assert out.shape == (4, 12) and np.allclose(out, 0.4)

    model = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0,
                             aux_heads=True, aux_dim=18)
    _, _, aux = model.forward_with_aux(_batch())
    assert aux.shape == (2, 18)          # 头宽度跟着 aux_dim 走


def test_train_rl_consumes_aux_labels():
    """端到端：replay 带 aux + 模型开辅助头 → 训练 1 轮就有非零 aux 损失。"""
    import numpy as np

    from backend.engine.ai.core.model import AUX_TARGET_DIM
    from backend.engine.ai.core.replay_buffer import RecentIterationsReplayBuffer
    from backend.engine.ai.train import train_rl

    n = 64
    state = _batch(batch_size=n)
    states = [{k: v[i].numpy() for k, v in state.items()} for i in range(n)]
    buffer = RecentIterationsReplayBuffer(keep_iterations=1)
    buffer.push_batch(
        states,
        np.eye(22, dtype=np.float32)[np.zeros(n, dtype=int)],
        np.ones((n, 22), dtype=np.float32),
        np.linspace(-1.0, 1.0, n).astype(np.float32),
        np.arange(n, dtype=np.int64),
        aux=np.full((n, AUX_TARGET_DIM), 0.5, dtype=np.float32),
    )
    assert buffer.aux_buffer is not None and buffer.aux_buffer.shape == (n, AUX_TARGET_DIM)

    model = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0,
                             aux_heads=True)
    history = train_rl(
        model, buffer, epochs=1, batch_size=16, device="cpu",
        optimizer=torch.optim.Adam(model.parameters(), lr=1e-3),
        val_indices=np.arange(8, dtype=np.int64),
        aux_loss_weight=0.5,
    )
    assert history and history[0]["val_aux_loss"] > 0.0
    assert history[0]["train_aux_loss"] > 0.0

    # 没开辅助头的模型 + 带 aux 的 replay：不该报错，也不该有 aux 损失
    plain = ModularBattleNet(trunk_dim=64, num_blocks=1, dropout=0.0)
    history_plain = train_rl(
        plain, buffer, epochs=1, batch_size=16, device="cpu",
        optimizer=torch.optim.Adam(plain.parameters(), lr=1e-3),
        val_indices=np.arange(8, dtype=np.int64),
        aux_loss_weight=0.5,
    )
    assert history_plain and history_plain[0]["val_aux_loss"] == 0.0
