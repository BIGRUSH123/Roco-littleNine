"""并行 MCTS Agent - 用于训练加速

在 MCTSAgent 基础上添加并行化支持。
"""

import multiprocessing as mp
import numpy as np

from backend.engine.ai.core.mcts_parallel import parallel_mcts_search_root

# 延迟导入避免循环依赖
def _get_mcts_agent_class():
    """延迟导入 MCTSAgent"""
    from backend.engine.ai.train import MCTSAgent
    return MCTSAgent


class ParallelMCTSAgent:
    """并行版本的 MCTS Agent

    在 MCTSAgent 基础上，使用进程池并行化 MCTS 搜索。

    使用方法：
        # 创建时指定进程池
        pool = multiprocessing.Pool(4)
        agent = ParallelMCTSAgent(
            team="A",
            player=player_a,
            factory=factory,
            opponent_agent=opponent,
            num_simulations=800,
            num_workers=4,
            pool=pool,  # 复用进程池
            ...
        )

        # 使用（与 MCTSAgent 完全一样）
        action = agent.choose_action(battle)

        # 训练结束后关闭进程池
        pool.close()
        pool.join()
    """

    def __init__(
        self,
        team: str,
        player,
        factory,
        opponent_agent,
        num_simulations: int = 800,  # 默认更多模拟（分摊开销）
        num_workers: int = 4,
        pool=None,  # 复用的进程池（重要！）
        temperature: float = 1.0,
        root_noise: float = 0.25,
        record: bool = False,
        opp_greedy: bool = False,
        *,
        model=None,
        device: str = "cpu",
        evaluator=None,
        max_turns: int = 100,
        draw_margin: float = 0.02,
        gamma: float = 1.0,
        tanh_k: float = 0.0,
        leaf_batch_size: int = 16,
    ):
        # 动态获取并调用父类（避免循环导入）
        MCTSAgent = _get_mcts_agent_class()

        # 手动初始化父类（不使用 super，因为不是真正的继承）
        # 复制 MCTSAgent.__init__ 的逻辑
        self.team = team
        self.player = player
        self._factory = factory
        self._opponent = opponent_agent
        self._num_simulations = num_simulations
        self._temperature = temperature
        self._root_noise = root_noise
        self._record = record
        self._opp_greedy = opp_greedy
        self._model = model
        self._device = device
        self._evaluator = evaluator
        self._max_turns = max_turns
        self._draw_margin = draw_margin
        self._gamma = gamma
        self._tanh_k = tanh_k
        self._leaf_batch_size = leaf_batch_size

        # Keep the same public contract as MCTSAgent. collect_rl_samples reads
        # agent.history after each game, and choose_action appends to it.
        self.history: list[tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]] = []

        # 并行化参数
        self._num_workers = num_workers
        self._pool = pool
        self._own_pool = False

        # 如果没有提供进程池，创建一个（但推荐外部管理）
        if self._pool is None:
            self._pool = mp.Pool(num_workers)
            self._own_pool = True

    def choose_action(self, battle):
        """选择动作（并行版本）

        逻辑与 MCTSAgent 完全相同，只是用 parallel_mcts_search_root
        替代 mcts_search。
        """
        from backend.sim.action import Action
        from backend.engine.ai.core.encoder import encode_battle_state
        from backend.engine.ai.core.mcts import get_valid_actions, action_index_to_action
        from backend.engine.ai.train import _sample_action

        swapped = False
        if self.team == "B":
            battle.player_a, battle.player_b = battle.player_b, battle.player_a
            swapped = True

        try:
            # 编码状态（用于训练记录）
            state = encode_battle_state(battle) if self._record else None

            # 使用并行 MCTS（关键差异）
            probs = parallel_mcts_search_root(
                battle=battle,
                model=None,
                factory=self._factory,
                opponent_agent=self._opponent,
                num_simulations=self._num_simulations,
                num_workers=self._num_workers,
                pool=self._pool,  # 复用进程池
                root_noise=self._root_noise,
                max_turns=self._max_turns,
                opp_greedy=self._opp_greedy,
                evaluator=self._evaluator,
                root_state=state,
                draw_margin=self._draw_margin,
                gamma=self._gamma,
                tanh_k=self._tanh_k,
                leaf_batch_size=self._leaf_batch_size,
                device="cpu",  # 并行版本使用 CPU
            )

            # 防御性归一化（与原版相同）
            _, valid_mask = get_valid_actions(battle.player_a, battle)
            probs = probs * valid_mask
            s = probs.sum()
            if s > 0:
                probs = probs / s
            else:
                return Action(kind="gather")

            if self._record and state is not None:
                self.history.append((state, probs.copy(), valid_mask.copy()))
        finally:
            if swapped:
                battle.player_a, battle.player_b = battle.player_b, battle.player_a

        action_idx = _sample_action(probs, self._temperature)
        if action_idx < 0:
            return Action(kind="gather")
        player = battle.player_a if self.team == "A" else battle.player_b
        action = action_index_to_action(player, action_idx)
        if action is not None:
            return action
        return Action(kind="gather")

    def choose_lead(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_replacement(self, battle) -> int:
        """力竭换宠：用网络策略头选择最佳替补。"""
        from backend.engine.ai.core.encoder import encode_battle_state
        from backend.engine.ai.core.mcts import NUM_ACTIONS

        alive = [
            i for i, s in enumerate(self.player.team)
            if not s.is_fainted and i != self.player.active_index
        ]
        if not alive:
            return -1

        swapped = False
        if self.team == "B":
            battle.player_a, battle.player_b = battle.player_b, battle.player_a
            swapped = True

        try:
            state = encode_battle_state(battle)
            mask = np.zeros(NUM_ACTIONS, dtype=np.float32)
            bench_slot = 0
            for i, s in enumerate(self.player.team):
                if i == self.player.active_index:
                    continue
                if bench_slot < 5 and not s.is_fainted:
                    mask[10 + bench_slot] = 1.0
                bench_slot += 1
            _, probs = self._evaluator.evaluate(state, mask)

            best_idx = -1
            best_score = -1.0
            bench_slot = 0
            for i, s in enumerate(self.player.team):
                if i == self.player.active_index:
                    continue
                if bench_slot < 5:
                    if not s.is_fainted and probs[10 + bench_slot] > best_score:
                        best_score = probs[10 + bench_slot]
                        best_idx = i
                    bench_slot += 1
        finally:
            if swapped:
                battle.player_a, battle.player_b = battle.player_b, battle.player_a

        return best_idx if best_idx >= 0 else alive[0]

    def on_game_end(self, winner: str) -> None:
        pass

    def close(self):
        """关闭自己创建的进程池"""
        if self._own_pool and self._pool is not None:
            self._pool.close()
            self._pool.join()
            self._pool = None

    def __del__(self):
        """析构时自动关闭"""
        self.close()
