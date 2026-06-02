"""backend/engine/ai/mcts.py — 蒙特卡洛树搜索（AlphaZero 风格）

用于自我博弈时选择动作。从当前对战状态出发，
用双头网络评估叶节点，按访问次数比例输出动作概率。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from backend.engine.ai.encode import encode_battle_state
from backend.engine.ai.evaluator import PolicyValueEvaluator, TorchEvaluator

if TYPE_CHECKING:
    from backend.engine.ai.model import BattleNet
    from backend.sim.action import Action
    from backend.sim.agent import Agent
    from backend.sim.battle import Battle
    from backend.sim.factory import SimFactory
    from backend.sim.player import Player

# ═══════════════════════════════════════════════════════════════════
# 动作空间
# ═══════════════════════════════════════════════════════════════════

NUM_ACTIONS = 10  # 技能0-3 + 换宠4-8 + 道具9


def get_valid_actions(player: Player) -> tuple[list[int], np.ndarray]:
    """返回 (有效动作索引列表, 10维 float32 mask)。"""
    mask = np.zeros(NUM_ACTIONS, dtype=np.float32)

    active = player.active if player.active_index < len(player.team) else None
    if active is None or active.is_fainted:
        return [], mask

    # 技能 (0-3)
    for i, sk in enumerate(active.skills[:4]):
        if not sk.sealed and sk.cooldown <= 0 and sk.energy_cost <= active.energy:
            mask[i] = 1.0

    # 换宠 (4-8): 板凳上存活的精灵
    locked = active.locked_turns > 0
    bench_slot = 0
    for i, s in enumerate(player.team):
        if i == player.active_index or s.is_fainted:
            continue
        if bench_slot < 5:
            mask[4 + bench_slot] = 1.0 if not locked else 0.0
            bench_slot += 1

    # 道具 (9)
    item = player.item
    if item is not None and not item.is_exhausted:
        mask[9] = 1.0

    valid = [i for i in range(NUM_ACTIONS) if mask[i] > 0]
    return valid, mask


def action_index_to_action(player: Player, action_idx: int) -> Action | None:
    """将 0-9 动作索引转为 Action 对象。"""
    from backend.sim.action import Action

    if action_idx < 4:
        return Action(kind='skill', skill_index=action_idx)
    elif action_idx < 9:
        bench_idx = action_idx - 4
        switch_idx = _bench_to_team_index(player, bench_idx)
        if switch_idx is not None:
            return Action(kind='switch', switch_index=switch_idx)
        return None
    elif action_idx == 9:
        return Action(kind='item')
    return None


def _bench_to_team_index(player: Player, bench_slot: int) -> int | None:
    """板凳槽位 → team 中的实际索引。"""
    count = 0
    for i, s in enumerate(player.team):
        if i == player.active_index or s.is_fainted:
            continue
        if count == bench_slot:
            return i
        count += 1
    return None


# ═══════════════════════════════════════════════════════════════════
# 动作采样辅助
# ═══════════════════════════════════════════════════════════════════

def policy_select_idx(probs: np.ndarray, temperature: float, greedy: bool = False) -> int:
    """按温度从概率分布采样动作索引；greedy 或温度≈0 时取 argmax。"""
    if greedy or temperature <= 1e-8:
        return int(np.argmax(probs))
    p = probs.astype(np.float64)
    if temperature != 1.0:
        p = np.power(p, 1.0 / temperature)
    s = p.sum()
    if s <= 0:
        return int(np.argmax(probs))
    p = p / s
    return int(np.random.choice(len(p), p=p))


# ═══════════════════════════════════════════════════════════════════
# NetworkPolicyAgent — MCTS 内部对手（仅策略头，无搜索）
# ═══════════════════════════════════════════════════════════════════

class NetworkPolicyAgent:
    """用网络策略头（无搜索）为 battle.player_b 选动作的轻量 agent。

    设计为"槽位驱动"：始终为传入 battle 的 **player_b** 决策，
    与 mcts_search 的规范化（我方=player_a、对手=player_b）一致。
    因此在自我博弈里既可作为 A 侧搜索中 B 的对手，也可作为 B 侧
    （已交换）搜索中 A 的对手——无需关心真实队标，且不持有任何
    会因状态重建而失效的 player 引用。
    """

    team = "B"

    def __init__(
        self,
        model=None,
        device: str = "cpu",
        temperature: float = 1.0,
        greedy: bool = False,
        evaluator: PolicyValueEvaluator | None = None,
    ):
        if evaluator is None:
            if model is None:
                raise ValueError("NetworkPolicyAgent 需要 model 或 evaluator")
            evaluator = TorchEvaluator(model, device)
        self._evaluator = evaluator
        self._temperature = temperature
        self._greedy = greedy

    def _decide(self, battle) -> int | None:
        # 直接从 B 视角编码，无需 swap/restore
        player = battle.player_b
        valid, mask = get_valid_actions(player)
        if not valid:
            return None
        state = encode_battle_state(battle, perspective="B")
        _, p = self._evaluator.evaluate(state, mask)
        return policy_select_idx(p, self._temperature, self._greedy)

    def choose_action(self, battle):
        from backend.sim.action import Action

        idx = self._decide(battle)
        if idx is None:
            player = battle.player_b
            rep = player.find_replacement() if hasattr(player, "find_replacement") else None
            if rep is not None:
                return Action(kind="switch", switch_index=rep)
            return Action(kind="gather")
        action = action_index_to_action(battle.player_b, idx)
        return action if action is not None else Action(kind="gather")

    def choose_lead(self, battle) -> int:
        player = battle.player_b
        alive = [i for i, s in enumerate(player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_replacement(self, battle) -> int:
        player = battle.player_b
        alive = [i for i, s in enumerate(player.team)
                 if not s.is_fainted and i != player.active_index]
        return alive[0] if alive else -1  # -1 通知引擎扣魔力

    def on_game_end(self, winner: str) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════
# FixedAgent — 包装真实 Agent，覆盖 choose_action
# ═══════════════════════════════════════════════════════════════════

class FixedAgent:
    """包装器：choose_action 返回预定动作，其余委托给真实 agent。"""

    def __init__(self, action: Action, real_agent):
        self._action = action
        self._real = real_agent

    def choose_action(self, battle) -> Action:
        return self._action

    def choose_lead(self, battle) -> int:
        return self._real.choose_lead(battle)

    def choose_replacement(self, battle) -> int:
        return self._real.choose_replacement(battle)

    def on_game_end(self, winner: str) -> None:
        self._real.on_game_end(winner)

    @property
    def team(self) -> str:
        return self._real.team

    @property
    def player(self):
        return self._real.player


# ═══════════════════════════════════════════════════════════════════
# MCTS
# ═══════════════════════════════════════════════════════════════════

class MCTSNode:
    __slots__ = (
        "visit_count", "total_value", "prior", "children", "valid_actions",
    )

    def __init__(self, valid_actions: list[int], prior: np.ndarray):
        self.visit_count = 0
        self.total_value = 0.0
        self.prior = prior
        self.children: dict[int, MCTSNode] = {}
        self.valid_actions = valid_actions

    @property
    def value(self) -> float:
        if self.visit_count == 0:
            return 0.0
        return self.total_value / self.visit_count


def mcts_search(
    battle: Battle,
    model: BattleNet | None,
    factory: SimFactory,
    opponent_agent,
    num_simulations: int = 200,
    c_puct: float = 2.0,
    device: str = "cpu",
    root_noise: float = 0.25,
    *,
    evaluator: PolicyValueEvaluator | None = None,
    root_state: np.ndarray | None = None,
) -> np.ndarray:
    """从当前对战状态执行 MCTS，返回动作概率分布 (10,)。

    Args:
        battle: 当前对战（player_a 是己方）。
        model: 双头网络（与 evaluator 二选一；并行 worker 传 None）。
        factory: 工厂（用于状态快照恢复）。
        opponent_agent: 对手 agent（player_b 侧，如 RuleAgent）。
        num_simulations: 模拟次数。
        c_puct: 探索系数。
        device: 推理设备（仅 TorchEvaluator 使用）。
        root_noise: 根节点 Dirichlet 噪声强度。
        evaluator: 可选推理后端（并行训练时由主进程批量服务）。
        root_state: 可选预编码状态（调用方已编码时复用，避免重复编码）。

    Returns:
        (10,) float32 动作概率（∝ 访问次数）。
    """
    if evaluator is None:
        if model is None:
            raise ValueError("mcts_search 需要 model 或 evaluator")
        evaluator = TorchEvaluator(model, device)

    player = battle.player_a
    valid, mask = get_valid_actions(player)
    if not valid:
        return mask / max(mask.sum(), 1.0)

    # ── 根节点先验（复用调用方预编码的状态） ──
    if root_state is None:
        root_state = encode_battle_state(battle)
    _, prior = evaluator.evaluate(root_state, mask)

    # Dirichlet 噪声
    if root_noise > 0:
        noise = np.random.dirichlet([0.3] * len(valid))
        for i, a in enumerate(valid):
            prior[a] = (1 - root_noise) * prior[a] + root_noise * noise[i]

    root = MCTSNode(valid, prior)
    root_clone = battle.clone_for_mcts()  # 轻量副本，跳过 dict 往返

    # ── MCTS 主循环 ──
    for _ in range(num_simulations):
        # 恢复根状态：从预构建副本深拷贝（clone_for_mcts 比 dict 往返快 3-5x）
        sim = root_clone.clone_for_mcts()
        node = root
        path: list[tuple[MCTSNode, int]] = []

        # ── Selection ──
        while node.children and node.visit_count > 0:
            best_a = -1
            best_score = -1e9
            sqrt_n = math.sqrt(node.visit_count + 1)
            for a in node.valid_actions:
                child = node.children.get(a)
                if child is None:
                    continue
                q = child.value
                u = c_puct * node.prior[a] * sqrt_n / (1 + child.visit_count)
                if q + u > best_score:
                    best_score = q + u
                    best_a = a
            if best_a < 0:
                break
            path.append((node, best_a))
            node = node.children[best_a]
            _step_battle(sim, best_a, opponent_agent)

        # ── Expansion & Evaluation ──
        sim_player = sim.player_a
        sim_valid, sim_mask = get_valid_actions(sim_player)

        if sim_valid and not sim.is_finished:
            sim_state = encode_battle_state(sim)
            leaf_value, sim_prior = evaluator.evaluate(sim_state, sim_mask)
            for a in sim_valid:
                node.children[a] = MCTSNode(sim_valid, sim_prior)
        else:
            leaf_value = 1.0 if sim.winner == "A" else (-1.0 if sim.winner == "B" else 0.0)

        # ── Backprop ──
        for parent, a in reversed(path):
            parent.visit_count += 1
            parent.total_value += leaf_value
        root.visit_count += 1
        root.total_value += leaf_value

    # ── 输出动作概率 ──
    counts = np.zeros(NUM_ACTIONS, dtype=np.float32)
    for a in root.valid_actions:
        child = root.children.get(a)
        if child:
            counts[a] = float(child.visit_count)
    total = counts.sum()
    if total > 0:
        return counts / total
    return mask / max(mask.sum(), 1.0)


def _step_battle(battle: Battle, action_idx: int, opponent_agent) -> None:
    """在 battle 上执行一回合：A 按 action_idx 行动，B 由 opponent_agent 决定。

    使用 FixedAgent 包装 A，使 execute_turn 按预定动作执行。
    """
    from backend.sim.action import Action

    player_a = battle.player_a
    action_a = action_index_to_action(player_a, action_idx)
    if action_a is None:
        return

    fixed_a = FixedAgent(action_a, opponent_agent)  # 复用 opponent 的 choose_lead 等
    # 为 player_a 侧的 fixed agent 设置正确的 player 引用
    fixed_a._real = _PlayerSwappedAgent(opponent_agent, player_a)

    battle.execute_turn(fixed_a, opponent_agent)


class _PlayerSwappedAgent:
    """将 opponent agent 的 player 替换为 player_a，用于委托 choose_lead/choose_replacement。"""

    def __init__(self, source, player):
        self._source = source
        self.player = player
        self.team = "A"

    def choose_lead(self, battle) -> int:
        # 委托前临时切换 player… 简单实现：直接取第一个存活
        alive = [i for i, s in enumerate(self.player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_replacement(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team)
                 if not s.is_fainted and i != self.player.active_index]
        return alive[0] if alive else -1  # -1 通知引擎扣魔力

    def on_game_end(self, winner: str) -> None:
        pass
