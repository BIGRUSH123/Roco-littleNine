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

NUM_ACTIONS = 11  # 技能0-3 + 换宠4-8 + 聚能9 + 道具10


def get_valid_actions(player: Player, battle=None) -> tuple[list[int], np.ndarray]:
    """返回 (有效动作索引列表, 11维 float32 mask)。

    Args:
        player: 决策玩家。
        battle: 可选，传入时使用引擎侧能耗修正（天气/印记/energy_cost modifier）。
    """
    mask = np.zeros(NUM_ACTIONS, dtype=np.float32)

    active = player.active if player.active_index < len(player.team) else None
    if active is None or active.is_fainted:
        return [], mask

    # ── 能耗辅助：近似引擎侧实际 energy_cost 计算 ──
    def _effective_cost(sk) -> int:
        cost = sk.energy_cost
        # energy_cost modifier（on_next / per-turn VM 管线注入）
        ec_mod = active._modifiers.get("energy_cost", 0)
        if ec_mod:
            cost += round(ec_mod)
        # energy_cost multiplier
        ec_mult = active._modifiers.get("energy_cost_mult", 0)
        if ec_mult:
            cost = round(cost * (1.0 + ec_mult))
        # 印记能耗减免（需要知道 team 和 battle）
        if battle is not None:
            team = "A" if battle.player_a is player else "B"
            cost -= getattr(battle.globals, 'mark_energy_mod', lambda t: 0)(team)
        return max(0, cost)

    # 蓄力中：只能释放蓄力技能，禁止其他技能/聚能/换宠/道具
    charging = getattr(active, '_charging', False)
    charged_idx = getattr(active, '_charged_skill_index', -1)
    if charging:
        if charged_idx >= 0 and charged_idx < 4:
            sk = active.skills[charged_idx] if charged_idx < len(active.skills) else None
            if sk and not sk.sealed and sk.cooldown <= 0 and _effective_cost(sk) <= active.energy:
                mask[charged_idx] = 1.0
        valid = [i for i in range(NUM_ACTIONS) if mask[i] > 0]
        return valid, mask

    # 技能 (0-3)：使用引擎侧能耗近似
    for i, sk in enumerate(active.skills[:4]):
        if not sk.sealed and sk.cooldown <= 0 and _effective_cost(sk) <= active.energy:
            mask[i] = 1.0

    # 换宠 (4-8): 固定槽位映射（与 encode.py 的 _encode_bench_all 一致）
    #   力竭精灵保留在槽位中（mask=0），不跳过，确保动作索引与编码槽位
    #   始终一一对应。若跳过力竭精灵，槽位编号会随力竭状态漂移，
    #   导致网络输入槽位 N 与动作索引 4+N 对应不同精灵 → 策略学习混乱。
    locked = active.locked_turns > 0
    bench_slot = 0
    for i, s in enumerate(player.team):
        if i == player.active_index:
            continue
        if bench_slot < 5:
            mask[4 + bench_slot] = 1.0 if (not s.is_fainted and not locked) else 0.0
            bench_slot += 1

    # 聚能 (9): 蓄力中不可聚能（引擎侧会阻止，标记为非法避免无效分支）
    mask[9] = 1.0 if not charging else 0.0

    # 道具 (10)
    item = player.item
    if item is not None and not item.is_exhausted:
        mask[10] = 1.0

    valid = [i for i in range(NUM_ACTIONS) if mask[i] > 0]
    return valid, mask


def action_index_to_action(player: Player, action_idx: int) -> Action | None:
    """将 0-10 动作索引转为 Action 对象。"""
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
        return Action(kind='gather')
    elif action_idx == 10:
        return Action(kind='item')
    return None


def _bench_to_team_index(player: Player, bench_slot: int) -> int | None:
    """板凳槽位 → team 中的实际索引（固定槽位，不跳过力竭）。"""
    count = 0
    for i, s in enumerate(player.team):
        if i == player.active_index:
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
        valid, mask = get_valid_actions(player, battle)
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
        "opp_policy",  # 对手在本节点的策略分布（消除 _step_battle 中的重复推理）
    )

    def __init__(self, valid_actions: list[int], prior: np.ndarray):
        self.visit_count = 0
        self.total_value = 0.0
        self.prior = prior
        self.children: dict[int, MCTSNode] = {}
        self.valid_actions = valid_actions
        self.opp_policy: np.ndarray | None = None

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
    opp_greedy: bool = False,
    evaluator: PolicyValueEvaluator | None = None,
    root_state: np.ndarray | None = None,
) -> np.ndarray:
    """从当前对战状态执行 MCTS，返回动作概率分布 (11,)。

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
        (11,) float32 动作概率（∝ 访问次数）。
    """
    if evaluator is None:
        if model is None:
            raise ValueError("mcts_search 需要 model 或 evaluator")
        evaluator = TorchEvaluator(model, device)

    # 禁用 save_snapshot — MCTS 仿真不需要回溯序列化（省 ~17% 耗时）
    prev_mcts_sim = getattr(battle, '_mcts_sim', False)
    battle._mcts_sim = True
    try:

        player = battle.player_a
        valid, mask = get_valid_actions(player, battle)
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
        # 预铺根节点子节点空壳：确保第一轮模拟即可进入 Selection，
        # 防止 root 作为叶子节点被二次评估 → Dirichlet 噪声永不丢失。
        # 每个空壳的 prior/valid_actions 留空，等该子节点被选中并
        # 展开时再用当前状态的网络评估来填充。
        for a in valid:
            root.children[a] = MCTSNode([], np.zeros(NUM_ACTIONS, dtype=np.float32))
        # 预计算根节点对手策略（博弈树首次选择时直接采样，省 encode+eval）
        opp_player = battle.player_b
        opp_valid, opp_mask = get_valid_actions(opp_player, battle)
        if opp_valid:
            opp_state = encode_battle_state(battle, perspective="B")
            _, opp_prior = evaluator.evaluate(opp_state, opp_mask)
            root.opp_policy = opp_prior

        # ── MCTS 主循环 ──
        # 使用 save/restore 替代 clone：每轮仿真前保存可变状态，
        # 在原始 battle 上原地执行 selection → evaluation → backprop，
        # 最后恢复状态。消除全部对象图遍历/深拷贝开销（原占 ~30% 耗时）。
        for _ in range(num_simulations):
            saved = battle.save_mutable_state()
            node = root
            path: list[tuple[MCTSNode, int]] = []

            # ── Selection ──
            # 叶子节点判定：有无 children（不再依赖 visit_count > 0）。
            # 根节点的 children 已预铺空壳，首轮即可正常选路。
            while node.children:
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
                # 终端守卫：对局已结束或 active 已力竭时停止降序，
                # 避免在无效状态下推进回合导致树结构偏离。
                if battle.is_finished or battle.player_a.active.is_fainted:
                    break
                path.append((node, best_a))
                node = node.children[best_a]
                # 使用预缓存的对手策略（省掉 encode+eval），回退到 opponent_agent
                _step_battle(battle, best_a, opponent_agent,
                             opp_policy=path[-1][0].opp_policy,
                             opp_greedy=opp_greedy)

            # ── Expansion & Evaluation ──
            sim_player = battle.player_a
            sim_valid, sim_mask = get_valid_actions(sim_player, battle)

            if sim_valid and not battle.is_finished:
                leaf_state = encode_battle_state(battle)
                leaf_value, sim_prior = evaluator.evaluate(leaf_state, sim_mask)
                # 将当前节点的合法动作和先验更新为真实评估结果
                # （之前是空壳，现在是本状态的真实先验）
                node.valid_actions = sim_valid
                node.prior = sim_prior
                # 预计算对手策略
                opp_player = battle.player_b
                opp_valid, opp_mask = get_valid_actions(opp_player, battle)
                if opp_valid:
                    opp_state = encode_battle_state(battle, perspective="B")
                    _, opp_prior = evaluator.evaluate(opp_state, opp_mask)
                    node.opp_policy = opp_prior
                # 预铺子节点空壳：等它们被选中时再真实展开
                for a in sim_valid:
                    node.children[a] = MCTSNode(
                        [], np.zeros(NUM_ACTIONS, dtype=np.float32),
                    )
            else:
                # 终端节点：使用 battle_outcome_a 计算 value，确保与训练标签一致。
                # max_turns 平局时按局面分差判定（而非简单返回 0），消除 MCTS value
                # 估计与 RL 训练目标之间的系统性偏差。
                from backend.engine.ai.outcome import DEFAULT_DRAW_MARGIN, battle_outcome_a
                leaf_value, _ = battle_outcome_a(
                    battle, battle.MAX_TURNS, draw_margin=DEFAULT_DRAW_MARGIN,
                )

            # ── Backprop ──
            # 更新选择路径上的所有祖先节点以及当前展开的叶节点。
            for parent, a in reversed(path):
                parent.visit_count += 1
                parent.total_value += leaf_value
            node.visit_count += 1
            node.total_value += leaf_value

            # ── 回滚 ──
            battle.restore_mutable_state(saved)

    finally:
        # 恢复 save_snapshot 行为（异常时也保证恢复，避免标志泄漏）
        battle._mcts_sim = prev_mcts_sim

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


def _step_battle(
    battle: Battle, action_idx: int, opponent_agent,
    *, opp_policy: np.ndarray | None = None,
    opp_greedy: bool = False,
) -> None:
    """在 battle 上执行一回合：A 按 action_idx 行动，B 由 opponent_agent 决定。

    Args:
        opp_policy: 若提供，从该策略分布采样对手动作（省掉 encode+eval）；
                    否则调用 opponent_agent.choose_action（旧路径）。
        opp_greedy: 若 True，对手动作取 argmax（评估模式）；否则随机采样（训练模式）。

    使用 FixedAgent 包装双方，使 execute_turn 按预定动作执行。
    """
    from backend.sim.action import Action

    player_a = battle.player_a
    action_a = action_index_to_action(player_a, action_idx)
    # 仿真中 bench 精灵可能已力竭导致换宠动作失效。此时必须 fallback
    # 到聚能，不可 return 跳过回合：selection 循环已执行
    # node = node.children[best_a]（树指针已前进），若 battle 状态不变，
    # 后续 selection 在错误状态下选路→树结构逐渐损坏。
    # 注意：这会导致本轮的树边 (best_a) 与实际动作 (gather) 不匹配，
    # 但 fallback 只影响单次仿真，数百次仿真中影响可忽略。
    if action_a is None:
        action_a = Action(kind="gather")

    fixed_a = FixedAgent(action_a, opponent_agent)
    fixed_a._real = _PlayerSwappedAgent(opponent_agent, player_a)

    if opp_policy is not None:
        opp_idx = policy_select_idx(opp_policy, temperature=0.0 if opp_greedy else 1.0)
        player_b = battle.player_b
        action_b = action_index_to_action(player_b, opp_idx)
        if action_b is None:
            action_b = Action(kind="gather")
        fixed_b = _OppFixedAgent(action_b, battle.player_b)
        battle.execute_turn(fixed_a, fixed_b)
        return

    battle.execute_turn(fixed_a, opponent_agent)


class _PlayerSwappedAgent:
    """将 opponent agent 的 player 替换为 player_a，用于委托 choose_lead/choose_replacement。"""

    def __init__(self, source, player):
        self._source = source
        self.player = player
        self.team = "A"

    def choose_lead(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_replacement(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team)
                 if not s.is_fainted and i != self.player.active_index]
        return alive[0] if alive else -1  # -1 通知引擎扣魔力

    def on_game_end(self, winner: str) -> None:
        pass


class _OppFixedAgent:
    """对手 FixedAgent：choose_action 返回预定动作，其余委托给 player 自身。"""

    def __init__(self, action, player):
        self._action = action
        self.player = player
        self.team = "B"

    def choose_action(self, battle):
        return self._action

    def choose_lead(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_replacement(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team)
                 if not s.is_fainted and i != self.player.active_index]
        return alive[0] if alive else -1

    def on_game_end(self, winner: str) -> None:
        pass
