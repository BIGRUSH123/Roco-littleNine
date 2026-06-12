"""MCTS 根并行实现

在多个进程中并行运行独立的 MCTS 搜索，最后合并结果。
每个 worker 运行 num_simulations // num_workers 次模拟。
"""

from __future__ import annotations

import multiprocessing as mp
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from backend.engine.ai.core.evaluator import PolicyValueEvaluator
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory

NUM_ACTIONS = 17


def parallel_mcts_search_root(
    battle: Battle,
    model,
    factory: SimFactory,
    opponent_agent,
    num_simulations: int = 200,
    num_workers: int = 4,
    device: str = "cpu",
    **kwargs
) -> np.ndarray:
    """根并行 MCTS：每个 worker 独立运行完整搜索，最后合并结果

    Args:
        battle: 当前对战状态
        model: 神经网络模型
        factory: 用于状态恢复的工厂
        opponent_agent: 对手 agent
        num_simulations: 总模拟次数
        num_workers: 并行 worker 数量
        device: 推理设备
        **kwargs: 传递给 mcts_search 的其他参数

    Returns:
        (17,) 动作概率分布
    """
    # 计算每个 worker 的模拟次数
    sims_per_worker = num_simulations // num_workers
    remainder = num_simulations % num_workers
    worker_sims = [sims_per_worker + (1 if i < remainder else 0) for i in range(num_workers)]

    # 序列化初始状态（使用 pickle）
    import pickle
    initial_state = {
        'battle_pickle': pickle.dumps(battle),
        'mutable_state': battle.save_mutable_state(),  # 备用
    }

    # 序列化对手 agent（如果需要）
    opponent_config = _serialize_opponent(opponent_agent)

    # 准备 worker 参数
    worker_args = [
        (
            initial_state,
            factory,
            model,
            opponent_config,
            worker_sims[i],
            device,
            kwargs,
            i,  # worker_id（用于不同的随机种子）
        )
        for i in range(num_workers)
    ]

    # 并行执行
    with mp.Pool(num_workers) as pool:
        results = pool.starmap(_worker_mcts, worker_args)

    # 合并结果：累加访问次数
    merged_visits = np.zeros(NUM_ACTIONS, dtype=np.float32)
    for visit_counts in results:
        merged_visits += visit_counts

    # 转换为概率分布
    total = merged_visits.sum()
    if total > 0:
        return merged_visits / total

    # 回退：均匀分布
    from backend.engine.ai.core.mcts import get_valid_actions
    _, mask = get_valid_actions(battle.player_a, battle)
    return mask / max(mask.sum(), 1.0)


def _worker_mcts(
    initial_state: dict,
    factory: SimFactory,
    model,
    opponent_config: dict,
    num_simulations: int,
    device: str,
    kwargs: dict,
    worker_id: int,
) -> np.ndarray:
    """Worker 进程：独立运行 MCTS 搜索

    Returns:
        (17,) 访问次数
    """
    # 设置不同的随机种子（避免所有 worker 产生相同的结果）
    np.random.seed((np.random.randint(0, 2**31) + worker_id * 1000) % (2**32))

    # 从保存的状态恢复 battle
    # 注意：initial_state 包含完整的 battle 状态
    # 我们需要在 worker 中重建 battle 对象

    # TODO: 实现状态恢复
    # 目前的 save_mutable_state 需要一个已存在的 battle 对象
    # 我们需要另一种方式：pickle 整个 battle 或实现 restore_battle

    # 临时方案：使用 pickle
    import pickle
    battle = pickle.loads(initial_state['battle_pickle'])

    # 恢复对手 agent
    opponent_agent = _deserialize_opponent(opponent_config, model, device)

    # 运行 MCTS
    from backend.engine.ai.core.mcts import mcts_search

    probs = mcts_search(
        battle=battle,
        model=model,
        factory=factory,
        opponent_agent=opponent_agent,
        num_simulations=num_simulations,
        device=device,
        **kwargs
    )

    # 将概率转换为访问次数
    # probs ∝ visit_counts，所以 visit_counts ≈ probs * num_simulations
    visit_counts = probs * num_simulations

    return visit_counts


def _serialize_opponent(opponent_agent) -> dict:
    """序列化对手 agent 配置"""
    from backend.engine.ai.core.mcts import NetworkPolicyAgent

    if isinstance(opponent_agent, NetworkPolicyAgent):
        return {
            'type': 'NetworkPolicyAgent',
            'temperature': opponent_agent._temperature,
            'greedy': opponent_agent._greedy,
        }
    else:
        # 其他类型的 agent（如 RuleAgent）
        return {
            'type': type(opponent_agent).__name__,
            'agent': opponent_agent,  # 假设可序列化
        }


def _deserialize_opponent(config: dict, model, device: str):
    """反序列化对手 agent"""
    from backend.engine.ai.core.mcts import NetworkPolicyAgent
    from backend.engine.ai.core.evaluator import TorchEvaluator

    if config['type'] == 'NetworkPolicyAgent':
        evaluator = TorchEvaluator(model, device)
        return NetworkPolicyAgent(
            evaluator=evaluator,
            temperature=config['temperature'],
            greedy=config['greedy'],
        )
    else:
        # 其他类型
        return config['agent']


# ═══════════════════════════════════════════════════════════════════
# 辅助函数：从根节点提取访问次数
# ═══════════════════════════════════════════════════════════════════

def extract_visit_counts_from_root(root, num_actions: int = NUM_ACTIONS) -> np.ndarray:
    """从 MCTS 根节点提取访问次数

    Args:
        root: MCTSNode 根节点
        num_actions: 动作空间大小

    Returns:
        (num_actions,) 访问次数
    """
    counts = np.zeros(num_actions, dtype=np.float32)
    for action_idx in root.valid_actions:
        child = root.children.get(action_idx)
        if child is not None:
            counts[action_idx] = float(child.visit_count)
    return counts
