"""smoke_hook — rust_selfplay_hook 冒烟测试：1 局 rust shim + 直连评估器。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

os.environ["ROCO_SELFPLAY_EVAL"] = "direct"
os.environ["ROCO_SELFPLAY_MODEL"] = str(ROOT / "checkpoints" / "exp16" / "model_rl.pt")
os.environ["ROCO_SELFPLAY_DEVICE"] = "cuda"
os.environ["ROCO_SELFPLAY_TORCH_THREADS"] = "2"
os.environ["ROCO_SELFPLAY_GAME"] = "rust"

import random  # noqa: E402

import numpy as np  # noqa: E402

# 模拟 worker 的导入顺序：先 evaluator（触发 hook 安装）
from backend.engine.ai.core.evaluator import QueuePolicyEvaluator  # noqa: E402,F401

# 再延迟导入 train 的 _play_one_rl_battle —— 应拿到 shim
from backend.engine.ai.train import _load_sprite_skills, _play_one_rl_battle  # noqa: E402
from backend.engine.ai.rust_selfplay_hook import _rust_play_one_rl_battle  # noqa: E402
from backend.engine.ai.core.mcts import NUM_ACTIONS
import backend.engine.ai.train as tm  # noqa: E402

assert tm._play_one_rl_battle is _rust_play_one_rl_battle, "hook 未生效！"
print("✓ hook 已生效（train._play_one_rl_battle → rust shim）", flush=True)

# 直接构造 QueuePolicyEvaluator（模拟 worker），env 应使其变为直连
qe = QueuePolicyEvaluator(0, None, None)
assert qe._direct is not None, "直连模式未激活！"
print("✓ QueuePolicyEvaluator 直连模式已激活", flush=True)

random.seed(123)
np.random.seed(123)
factory = __import__("backend.sim.factory", fromlist=["SimFactory"]).SimFactory()
sprite_skills = _load_sprite_skills()

states, probs, masks, outcomes, end_reason, summary = _play_one_rl_battle(
    factory, sprite_skills, qe, 24, 60, 1.0, 0.25,
    draw_margin=0.15, game_timeout_s=450.0, gamma=1.0, tanh_k=0.0,
    leaf_batch_size=16, mirror=False,
)
print(f"✓ 单局完成: 决策数={len(probs)} end_reason={end_reason} "
      f"outcome范围=[{min(outcomes):.2f},{max(outcomes):.2f}] "
      f"winner={summary['winner']} turns={summary['turns']}", flush=True)
assert len(probs) == len(states) == len(masks) == len(outcomes)
assert all(p.shape == (NUM_ACTIONS,) for p in probs)
print("smoke OK", flush=True)
