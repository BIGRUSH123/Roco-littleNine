"""自博弈样本落盘（离线复训）的测试。

两条要钉住的：
  1. `_dump_round_samples` 写出的 npz 键/形状必须和 BC 数据集一致（否则 `bc_pretrain`
     读不了），且 `policy` 是分布、`action` 是其 argmax（兼容只认 one-hot 的旧路径）。
  2. `bc_pretrain` 的 policy 目标选择：带 `policy` 用分布，不带才退回 one-hot。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import numpy as np


def _fake_states(n: int) -> list[dict[str, np.ndarray]]:
    g = np.random.default_rng(0)
    out = []
    for _ in range(n):
        out.append({
            "sprite_stats": g.random((12, 7), dtype=np.float32) * 400,
            "sprite_elements": g.integers(0, 18, (12, 2), dtype=np.int32),
            "sprite_states": g.random((12, 105), dtype=np.float32),
            "skill_stats": g.random((10, 2), dtype=np.float32) * 120,
            "skill_elements": g.integers(0, 18, (10, 2), dtype=np.int32),
            "skill_states": g.random((10, 9), dtype=np.float32),
            "global_stats": g.random((15,), dtype=np.float32),
            "global_elements": g.integers(0, 5, (1,), dtype=np.int32),
            "form_elements": g.integers(0, 18, (5, 2), dtype=np.int32),
            "form_avail": g.random((5,), dtype=np.float32),
            "ast_tokens": g.integers(10, 380, (384,), dtype=np.int32),
            "ast_values": g.random((384,), dtype=np.float32),
        })
    return out


def test_dump_round_samples_is_bc_pretrain_compatible(tmp_path):
    from backend.engine.ai.bc_pretrain import load_dataset
    from backend.engine.ai.core.replay_buffer import _OBS_KEYS
    from backend.engine.ai.train import _dump_round_samples, _round_samples_path

    n = 16
    states = _fake_states(n)
    probs = np.zeros((n, 22), dtype=np.float32)
    probs[np.arange(n), np.arange(n) % 22] = 0.6
    probs[np.arange(n), (np.arange(n) + 1) % 22] = 0.4
    masks = np.ones((n, 22), dtype=np.float32)
    outcomes = np.linspace(-1.0, 1.0, n).astype(np.float32)

    _dump_round_samples(str(tmp_path), 3, states, probs, masks, outcomes, None,
                        sims=400, max_turns=60)

    path = _round_samples_path(str(tmp_path), 3)
    ds = load_dataset(path)                      # 走 bc_pretrain 的加载器
    for key in _OBS_KEYS:
        assert key in ds, f"缺观测键 {key}"
        assert ds[key].shape[0] == n
    assert ds["policy"].shape == (n, 22)
    assert np.allclose(ds["policy"].sum(axis=1), 1.0, atol=1e-5)
    assert ds["action"].tolist() == ds["policy"].argmax(axis=1).tolist()
    assert ds["is_meta"].shape == (n,) and ds["team_id"].shape == (n,)
    # sidecar 元信息
    import json
    from pathlib import Path

    side = json.loads(Path(path).with_suffix(".json").read_text(encoding="utf-8"))
    assert side["round"] == 3 and side["samples"] == n and side["sims"] == 400


def test_offline_retrain_from_dumped_round(tmp_path):
    """端到端：dump 出来的 npz 能直接喂给 `bc_pretrain`（认访问分布当目标）。

    这条是"样本持久化"这个功能的验收标准：自我博弈不用重跑，配方可以离线扫。
    """
    import json
    import subprocess
    from pathlib import Path

    from backend.engine.ai.train import _dump_round_samples

    n = 32
    states = _fake_states(n)
    probs = np.full((n, 22), 1 / 22, dtype=np.float32)
    probs[:, 0] = 0.5
    probs /= probs.sum(axis=1, keepdims=True)
    masks = np.ones((n, 22), dtype=np.float32)
    outcomes = np.linspace(-1.0, 1.0, n).astype(np.float32)
    _dump_round_samples(str(tmp_path), 1, states, probs, masks, outcomes, None)

    data = tmp_path / "samples" / "round1.npz"
    out = tmp_path / "offline.pt"
    root = Path(__file__).resolve().parents[2]
    res = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "backend.engine.ai.bc_pretrain",
         "--data", str(data), "--out", str(out),
         "--epochs", "1", "--batch-size", "8", "--device", "cpu"],
        cwd=str(root), capture_output=True, text=True, timeout=600,
    )
    text = res.stdout + res.stderr
    assert res.returncode == 0, f"离线复训失败：{text[-600:]}"
    assert "MCTS 访问分布" in text, f"没认出访问分布目标：{text[-600:]}"
    assert out.exists(), "没产出权重"
    side = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["samples"] == n
