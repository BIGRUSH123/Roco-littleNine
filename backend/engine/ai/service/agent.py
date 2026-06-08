"""backend/engine/ai/service/agent.py — 神经网络 AI 对手

提供两种模式:
  PolicyAgent — 策略头直出（毫秒级，快速但较弱）
  MCTSAgent   — MCTS 搜索（秒级，最强）

用法: 前端选 NeuralNet 或 NeuralMCTS 作为 AI 对手即可。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.evaluator import TorchEvaluator
from backend.engine.ai.core.mcts import (
    NUM_ACTIONS,
    NetworkPolicyAgent,
    action_index_to_action,
    get_valid_actions,
    mcts_search,
    policy_select_idx,
)
from backend.engine.ai.core.vocab import VOCAB_SIZE

if TYPE_CHECKING:
    from backend.sim.battle import Battle
    from backend.sim.player import Player


# ═══════════════════════════════════════════════════════════════════
# 模型加载
# ═══════════════════════════════════════════════════════════════════

_MODEL = None
_DEVICE = "cpu"
_CHECKPOINT = "checkpoints/modular_v3/model_rl_best.pt"


def set_checkpoint(path: str) -> None:
    """切换模型 checkpoint（需在创建 agent 前调用）。"""
    global _CHECKPOINT, _MODEL
    _CHECKPOINT = path
    _MODEL = None  # 强制重新加载


def _load_model():
    """惰性加载模型（首次用到时才加载，节省内存）。"""
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    import torch
    from pathlib import Path

    path = Path(_CHECKPOINT)
    if not path.exists():
        raise FileNotFoundError(
            f"模型 checkpoint 未找到: {_CHECKPOINT}\n"
            f"请先运行训练生成模型，或修改 _CHECKPOINT 路径"
        )

    data = torch.load(path, map_location=_DEVICE, weights_only=False)

    from backend.engine.ai.core.model import ModularBattleNet

    _MODEL = ModularBattleNet(
        trunk_dim=data.get("trunk_dim", 256),
        num_blocks=data.get("num_blocks", 4),
        dropout=float(data.get("dropout", 0.1)),
        vocab_size=data.get("vocab_size", VOCAB_SIZE),
        with_attention=data.get("with_attention", True),
    )

    try:
        _MODEL.load_state_dict(data["state_dict"])
    except RuntimeError as e:
        msg = str(e)
        if "size mismatch" in msg and "policy_head" in msg:
            raise RuntimeError(
                f"模型不兼容: {_CHECKPOINT}\n"
                f"  该 checkpoint 是旧版 10 动作空间 (无聚能)，"
                f"当前模型是 {NUM_ACTIONS} 动作空间。\n"
                f"  请使用 --sims 200 训练得到的新 checkpoint "
                f"(如 checkpoints/modular_v3/model_rl_best.pt)\n"
                f"  原始错误: {msg}"
            ) from e
        raise
    _MODEL.to(_DEVICE)
    _MODEL.eval()
    return _MODEL


# ═══════════════════════════════════════════════════════════════════
# Agent 基类
# ═══════════════════════════════════════════════════════════════════

class _BaseAgent:
    """神经网络 Agent 公共逻辑。"""

    team = "B"  # 固定为对手侧

    def __init__(self):
        self.player: Player | None = None
        self._evaluator = TorchEvaluator(_load_model(), _DEVICE)

    def choose_lead(self, battle: Battle) -> int:
        player = battle.player_b
        alive = [i for i, s in enumerate(player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_replacement(self, battle: Battle) -> int:
        player = battle.player_b
        alive = [
            i for i, s in enumerate(player.team)
            if not s.is_fainted and i != player.active_index
        ]
        return alive[0] if alive else -1

    def on_game_end(self, winner: str) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════
# PolicyAgent — 策略头直出
# ═══════════════════════════════════════════════════════════════════

class PolicyAgent(_BaseAgent):
    """仅用神经网络策略头输出，不做 MCTS 搜索。

    速度: 毫秒级（一次编码 + 一次前向）
    强度: 等于网络策略头直接 argmax
    """

    def choose_action(self, battle: Battle):
        player = battle.player_b
        valid, mask = get_valid_actions(player, battle)
        if not valid:
            from backend.sim.action import Action
            return Action(kind="gather")

        state = encode_battle_state(battle, perspective="B")
        _, probs = self._evaluator.evaluate(state, mask)
        idx = policy_select_idx(probs, temperature=0.0)  # 贪心
        action = action_index_to_action(player, idx)
        if action is not None:
            return action
        from backend.sim.action import Action
        return Action(kind="gather")


# ═══════════════════════════════════════════════════════════════════
# NeuralMCTSAgent — 完整 MCTS 搜索
# ═══════════════════════════════════════════════════════════════════

class NeuralMCTSAgent(_BaseAgent):
    """用 MCTS 搜索 + 神经网络评估选择动作。

    速度: 秒级（默认 100 次仿真，可调）
    强度: 最优（AlphaZero 标准玩法）
    """

    NUM_SIMULATIONS = 100

    def __init__(self):
        super().__init__()
        self._opponent = NetworkPolicyAgent(evaluator=self._evaluator, greedy=True)

    def choose_action(self, battle: Battle):
        player = battle.player_b
        valid, mask = get_valid_actions(player, battle)
        if not valid:
            from backend.sim.action import Action
            return Action(kind="gather")

        # MCTS 要求 battle.player_a = 搜索方。交换视角使 B 成为 A。
        battle.player_a, battle.player_b = battle.player_b, battle.player_a
        try:
            probs = mcts_search(
                battle, None, None, self._opponent,
                num_simulations=self.NUM_SIMULATIONS,
                evaluator=self._evaluator,
            )
        finally:
            battle.player_a, battle.player_b = battle.player_b, battle.player_a

        idx = policy_select_idx(probs, temperature=0.0)
        action = action_index_to_action(player, idx)
        if action is not None:
            return action
        from backend.sim.action import Action
        return Action(kind="gather")
