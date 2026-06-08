"""backend/engine/ai/train.py — AlphaZero 风格自我博弈强化学习训练

用法:
    python -m backend.engine.ai.train                         # 默认参数 RL 训练
    python -m backend.engine.ai.train --iterations 10         # 10轮迭代
    python -m backend.engine.ai.train --battles 200 --sims 400  # 每轮200局, 800次模拟
    python -m backend.engine.ai.train --resume model.pt       # 继续训练
"""

from __future__ import annotations

import argparse
import copy
import json
import multiprocessing as mp
import os
import pickle
import queue
import random
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
from backend.engine.ai.battle_log import BattleLogWriter, extract_battle_summary
from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.evaluator import BatchedInferenceServer, BatchedModelInferenceServer, TorchEvaluator
from backend.engine.ai.core.outcome import (
    DEFAULT_DRAW_MARGIN,
    DEFAULT_EVAL_MAX_TURNS,
    DEFAULT_SELFPLAY_MAX_TURNS,
    battle_outcome_a,
    eval_score_for_candidate,
    format_reason_counts,
)
from backend.engine.ai.tests.selfplay_worker import run_evaluate_worker, run_selfplay_worker
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.core.vocab import VOCAB_SIZE
from backend.engine.ai.core.mcts import (
    NUM_ACTIONS,
    NetworkPolicyAgent,
    action_index_to_action,
    get_valid_actions,
    mcts_search,
)
from backend.sim.factory import SimFactory
from backend.sim.player import Item

# ═══════════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════════

def _load_sprite_skills() -> dict[str, list[str]]:
    from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
    return SPRITE_RANDOM_POOL


def _random_item() -> Item:
    """返回随机道具：进化之力 或 愿力（等概率）。"""
    return Item.leader() if random.random() < 0.5 else Item.wish()


def _random_teams(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    max_team_size: int = 3,
    max_skills: int = 4,
) -> tuple:
    from backend.common.constants import STAT_KEYS
    from backend.common.nature import NATURE_TABLE

    names = list(sprite_skills.keys())
    random.shuffle(names)
    used: set[str] = set()  # 两队共享排重，避免 AB 重复选同一批精灵

    def build_team(label: str) -> list[dict]:
        max_possible = min(max_team_size, len(names) // 2)
        size = random.randint(1, max_possible) if max_possible >= 1 else 1
        specs: list[dict] = []
        for name in names:
            if name in used:
                continue
            if len(specs) >= size:
                break
            available = sprite_skills[name]
            n_skills = min(max_skills, len(available))
            chosen = random.sample(available, max(1, n_skills))
            # 随机性格
            nature = random.choice(list(NATURE_TABLE.keys()))
            # 随机选3项六维，IV 固定 10
            iv_keys = random.sample(list(STAT_KEYS), 3)
            iv = {k: 10 if k in iv_keys else 0 for k in STAT_KEYS}
            specs.append({"name": name, "skills": chosen, "nature": nature, "iv": iv})
            used.add(name)
        return specs

    return build_team("A"), build_team("B")


# ═══════════════════════════════════════════════════════════════════
# MCTSAgent — 将 MCTS 包装为 Agent 接口
# ═══════════════════════════════════════════════════════════════════

class MCTSAgent:
    """使用 MCTS 选择动作的 Agent，兼容 battle.execute_turn 接口。

    当 team=="B" 时，在调用 mcts_search 前临时交换 player_a/player_b，
    因为 mcts_search 始终从 player_a 视角工作。
    """

    def __init__(
        self,
        team: str,
        player,
        factory: SimFactory,
        opponent_agent,
        num_simulations: int = 200,
        temperature: float = 1.0,
        root_noise: float = 0.25,
        record: bool = False,
        opp_greedy: bool = False,
        *,
        model: ModularBattleNet | None = None,
        device: str = "cpu",
        evaluator=None,
        max_turns: int = DEFAULT_SELFPLAY_MAX_TURNS,
        gamma: float = 1.0,
        tanh_k: float = 0.0,
        leaf_batch_size: int = 1,
    ):
        self.team = team
        self.player = player
        self._factory = factory
        self._opponent = opponent_agent
        self._num_simulations = num_simulations
        self._temperature = temperature
        self._root_noise = root_noise
        self._record = record
        self._opp_greedy = opp_greedy
        self._max_turns = max_turns
        self._gamma = gamma
        self._tanh_k = tanh_k
        self._leaf_batch_size = leaf_batch_size
        if evaluator is None:
            if model is None:
                raise ValueError("MCTSAgent 需要 model 或 evaluator")
            evaluator = TorchEvaluator(model, device)
        self._evaluator = evaluator
        # 自我博弈训练样本：(本方视角状态 dict, MCTS 访问分布, 合法动作mask)
        self.history: list[tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]] = []

    def choose_lead(self, battle) -> int:
        alive = [i for i, s in enumerate(self.player.team) if not s.is_fainted]
        return alive[0] if alive else 0

    def choose_action(self, battle):
        from backend.sim.action import Action

        swapped = False
        if self.team == "B":
            battle.player_a, battle.player_b = battle.player_b, battle.player_a
            swapped = True

        try:
            # 本方视角下编码一次，同时用于 MCTS 根节点评估 + 训练样本记录
            state = encode_battle_state(battle) if self._record else None
            probs = mcts_search(
                battle, None, self._factory, self._opponent,
                num_simulations=self._num_simulations,
                root_noise=self._root_noise,
                max_turns=self._max_turns,
                opp_greedy=self._opp_greedy,
                evaluator=self._evaluator,
                root_state=state,  # 复用已编码状态，省掉 mcts_search 内部二次编码
                gamma=self._gamma,
                tanh_k=self._tanh_k,
                leaf_batch_size=self._leaf_batch_size,
            )
            # 防御 save/restore 状态微小差异：MCTS 中合法的动作
            # 在恢复后可能被判为非法。将 mask=0 位置的 probs 清零
            # 并重归一化，确保动作采样和训练数据都使用一致的分布。
            _, valid_mask = get_valid_actions(battle.player_a, battle)
            probs = probs * valid_mask
            s = probs.sum()
            if s > 0:
                probs = probs / s
            else:
                # valid_mask 全零：精灵被锁死且无替补，无任何合法动作。
                # 直接返回聚能作为安全兜底，避免 _sample_action 对全零数组
                # 返回 index=0 导致错误地执行被禁用的技能。
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

    def choose_replacement(self, battle) -> int:
        """力竭换宠：用网络策略头选择最佳替补。"""
        alive = [i for i, s in enumerate(self.player.team)
                 if not s.is_fainted and i != self.player.active_index]
        if not alive:
            return -1  # 通知引擎扣魔力

        # 用网络评估当前"力竭待换"状态，从 switch head (10-14) 选最佳
        swapped = False
        if self.team == "B":
            battle.player_a, battle.player_b = battle.player_b, battle.player_a
            swapped = True

        try:
            state = encode_battle_state(battle)
            # 构造 mask：仅启用存活板凳对应的 switch 动作
            mask = np.zeros(NUM_ACTIONS, dtype=np.float32)
            bench_slot = 0
            for i, s in enumerate(self.player.team):
                if i == self.player.active_index:
                    continue
                if bench_slot < 5 and not s.is_fainted:
                    mask[10 + bench_slot] = 1.0
                bench_slot += 1
            _, probs = self._evaluator.evaluate(state, mask)
            # 从 switch head (10-14) 中选概率最高的板凳槽位
            # bench_slot 必须与 mask 构造和 get_valid_actions 对齐：
            # 力竭精灵占槽位但不参与评分，槽位号始终递增。
            best_idx = -1
            best_score = -1.0
            bench_slot = 0
            for i, s in enumerate(self.player.team):
                if i == self.player.active_index:
                    continue
                if bench_slot < 5:
                    if not s.is_fainted:
                        if probs[10 + bench_slot] > best_score:
                            best_score = probs[10 + bench_slot]
                            best_idx = i
                    bench_slot += 1
        finally:
            if swapped:
                battle.player_a, battle.player_b = battle.player_b, battle.player_a

        return best_idx if best_idx >= 0 else alive[0]

    def on_game_end(self, winner: str) -> None:
        pass


def _sample_action(probs: np.ndarray, temperature: float) -> int:
    """按温度参数从概率分布中采样动作索引。
    当 probs 全零时返回 -1，由调用方兜底为聚能。
    """
    if probs.sum() <= 0:
        return -1
    if temperature <= 0 or temperature < 1e-8:
        return int(np.argmax(probs))
    if temperature != 1.0:
        probs = probs ** (1.0 / max(temperature, 1e-8))
    s = probs.sum()
    if s <= 0:
        return -1
    probs = probs / s
    return int(np.random.choice(len(probs), p=probs))


# ═══════════════════════════════════════════════════════════════════
# RL 数据收集 — MCTS 自我博弈
# ═══════════════════════════════════════════════════════════════════

def collect_rl_samples(
    model: ModularBattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    num_battles: int,
    num_simulations: int = 200,
    device: str = "cpu",
    max_turns: int = DEFAULT_SELFPLAY_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    temperature: float = 1.0,
    root_noise: float = 0.25,
    verbose: bool = True,
    progress_every: int = 10,
    battle_log_writer = None,
    gamma: float = 1.0,
    tanh_k: float = 0.0,
    leaf_batch_size: int = 1,
    mirror: bool = False,
) -> tuple[list[dict[str, np.ndarray]], np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    """MCTS 自我博弈收集 (state_dict, target_probs, mask, outcome) 四元组。

    与 AlphaZero 的差异（已修正）：
      1. 搜索内对手用当前网络（NetworkPolicyAgent），而非固定 RuleAgent
         → 真正的自我博弈。
      2. 同时记录 A、B **双方视角**的样本（B 的状态在交换后的本方视角下
         编码，结果取反）→ 数据翻倍且消除先手偏置。
      3. 每个决策只搜索一次（由 MCTSAgent 内部记录），不再重复搜索。

    Returns:
        states: list[dict[str, np.ndarray]]  Entity-based 编码的字典列表
        P: (N, 17) float32 MCTS 访问分布（策略目标）
        M: (N, 17) float32 合法动作 mask（1=合法, 0=非法）
        v: (N,) float32 对局结果（以各样本本方视角，+1=本方赢, -1=输, 0=平）
    """
    all_states: list[dict[str, np.ndarray]] = []
    all_probs: list[np.ndarray] = []
    all_masks: list[np.ndarray] = []
    all_outcomes: list[float] = []
    reason_counts: dict[str, int] = {}
    evaluator = TorchEvaluator(model, device)
    pe = max(1, progress_every)

    for i in range(num_battles):
        team_a, team_b = _random_teams(factory, sprite_skills)
        if mirror:
            team_b = copy.deepcopy(team_a)
        p1 = factory.build_player("A", team_a, item=_random_item())
        p2 = factory.build_player("B", team_b, item=_random_item())
        battle = factory.build_battle(p1, p2)

        # 搜索内的对手 = 当前网络策略头（贪心，不含温度噪声，确保对手强度）
        opp_a = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
        opp_b = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
        agent_a = MCTSAgent(
            "A", p1, factory, opp_a, num_simulations,
            temperature, root_noise=root_noise, record=True,
            evaluator=evaluator, max_turns=max_turns,
            gamma=gamma, tanh_k=tanh_k,
            leaf_batch_size=leaf_batch_size,
        )
        agent_b = MCTSAgent(
            "B", p2, factory, opp_b, num_simulations,
            temperature, root_noise=root_noise, record=True,
            evaluator=evaluator, max_turns=max_turns,
            gamma=gamma, tanh_k=tanh_k,
            leaf_batch_size=leaf_batch_size,
        )

        battle_started = time.monotonic()
        turn = 0
        while not battle.is_finished and turn < max_turns:
            battle.execute_turn(agent_a, agent_b)
            turn += 1
            if time.monotonic() - battle_started >= 450:  # 7.5min 单局上限
                break

        outcome_a, end_reason = battle_outcome_a(
            battle, max_turns, draw_margin=draw_margin,
            gamma=gamma, tanh_k=tanh_k,
        )
        if time.monotonic() - battle_started >= 450:
            end_reason = "timeout"

        # 写入对局技能日志（timeout 对局也记录，用于分析）
        if battle_log_writer is not None:
            battle_summary = extract_battle_summary(battle, end_reason)
            battle_log_writer.write(battle_summary)

        # timeout 对局不加入训练样本（过早截断导致价值标签不可靠）
        if end_reason == "timeout":
            reason_counts[end_reason] = reason_counts.get(end_reason, 0) + 1
            pe = max(1, progress_every)
            if verbose and (i + 1) % pe == 0:
                print(f"  RL 自我博弈 {i + 1}/{num_battles} 局 (timeout, 跳过), 样本 {len(all_states)}", flush=True)
            continue

        reason_counts[end_reason] = reason_counts.get(end_reason, 0) + 1

        for state, probs, m in agent_a.history:
            all_states.append(state)
            all_probs.append(probs)
            all_masks.append(m)
            all_outcomes.append(outcome_a)
        for state, probs, m in agent_b.history:
            all_states.append(state)
            all_probs.append(probs)
            all_masks.append(m)
            all_outcomes.append(-outcome_a)

        pe = max(1, progress_every)
        if verbose and (i + 1) % pe == 0:
            print(f"  RL 自我博弈 {i + 1}/{num_battles} 局, 样本 {len(all_states)}", flush=True)

    if not all_states:
        return (
            [],
            np.zeros((0, NUM_ACTIONS), dtype=np.float32),
            np.zeros((0, NUM_ACTIONS), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            {},
        )

    P = np.stack(all_probs).astype(np.float32)
    M = np.stack(all_masks).astype(np.float32)
    v = np.array(all_outcomes, dtype=np.float32)
    return all_states, P, M, v, reason_counts


def _play_one_rl_battle(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    evaluator,
    num_simulations: int,
    max_turns: int,
    temperature: float,
    root_noise: float,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    game_timeout_s: float = 450.0,
    gamma: float = 1.0,
    tanh_k: float = 0.0,
    leaf_batch_size: int = 1,
    mirror: bool = False,
) -> tuple[list[dict[str, np.ndarray]], list[np.ndarray], list[np.ndarray], list[float], str, dict]:
    """单局自我博弈，返回 (states, probs, masks, outcomes, end_reason, battle_summary)。

    game_timeout_s: 单局 wall-time 上限，超时强制退出并标记 end_reason="timeout"。
                    防止 MCTS 仿真在复杂对局状态中逐渐变慢导致单局卡死。
    gamma: 回合衰减因子，速胜奖励 > 拖沓胜（1.0 = 不衰减）。
    tanh_k: tanh 软裁决缩放系数（0 = 硬阈值）。
    mirror: 双方使用相同阵容（镜像对局）。
    """
    team_a, team_b = _random_teams(factory, sprite_skills)
    if mirror:
        team_b = copy.deepcopy(team_a)
    p1 = factory.build_player("A", team_a, item=_random_item())
    p2 = factory.build_player("B", team_b, item=_random_item())
    battle = factory.build_battle(p1, p2)

    opp_a = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
    opp_b = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
    agent_a = MCTSAgent(
        "A", p1, factory, opp_a, num_simulations,
        temperature, root_noise=root_noise, record=True,
        evaluator=evaluator, max_turns=max_turns,
        gamma=gamma, tanh_k=tanh_k,
        leaf_batch_size=leaf_batch_size,
    )
    agent_b = MCTSAgent(
        "B", p2, factory, opp_b, num_simulations,
        temperature, root_noise=root_noise, record=True,
        evaluator=evaluator, max_turns=max_turns,
        gamma=gamma, tanh_k=tanh_k,
        leaf_batch_size=leaf_batch_size,
    )

    battle_started = time.monotonic()
    turn = 0
    while not battle.is_finished and turn < max_turns:
        battle.execute_turn(agent_a, agent_b)
        turn += 1
        if time.monotonic() - battle_started >= game_timeout_s:
            break

    outcome_a, end_reason = battle_outcome_a(
        battle, max_turns, draw_margin=draw_margin,
        gamma=gamma, tanh_k=tanh_k,
    )
    # 超时覆盖 end_reason（优先级高于 max_turns / decisive）
    if time.monotonic() - battle_started >= game_timeout_s:
        end_reason = "timeout"

    # 提取对局回合技能摘要
    battle_summary = extract_battle_summary(battle, end_reason)

    states: list[dict[str, np.ndarray]] = []
    probs: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    outcomes: list[float] = []
    for state, pi, m in agent_a.history:
        states.append(state)
        probs.append(pi)
        masks.append(m)
        outcomes.append(outcome_a)
    for state, pi, m in agent_b.history:
        states.append(state)
        probs.append(pi)
        masks.append(m)
        outcomes.append(-outcome_a)
    return states, probs, masks, outcomes, end_reason, battle_summary


def collect_rl_samples_parallel(
    model: ModularBattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    num_battles: int,
    num_workers: int,
    device: str,
    inference_batch_size: int = 128,
    inference_timeout_ms: float = 5.0,
    num_simulations: int = 200,
    max_turns: int = DEFAULT_SELFPLAY_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    temperature: float = 1.0,
    root_noise: float = 0.25,
    verbose: bool = True,
    progress_every: int = 1,
    stall_timeout_s: float = 600.0,
    battle_log_writer = None,
    gamma: float = 1.0,
    tanh_k: float = 0.0,
    leaf_batch_size: int = 1,
    mirror: bool = False,
) -> tuple[list[dict[str, np.ndarray]], np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    """多进程局级 self-play + 主进程 CUDA 批量推理。

    work-stealing：worker 干完一局再领下一局，慢局只拖自己。
    stall_timeout_s：若全部 worker 在该秒数内都无任何对局完成（疑似单局 VM 死循环），
    终止剩余 worker、丢弃未完成局，用已完成的局继续，避免整轮冻结。
    """
    n_workers = max(1, min(num_workers, num_battles))

    if verbose:
        print(
            f"  并行自我博弈: {n_workers} workers 动态领取 {num_battles} 局, "
            f"batch={inference_batch_size}, timeout={inference_timeout_ms}ms",
            flush=True,
        )

    ctx = mp.get_context("spawn")
    # maxsize 限制队列容量，防止 Windows pipe 缓冲区溢出导致 _feed 线程崩溃
    request_queue = ctx.Queue(maxsize=n_workers * 4)
    result_queue = ctx.Queue(maxsize=n_workers * 2)
    # 任务队列：num_battles 个单局任务 + 每个 worker 一个 None 停止哨兵。
    task_queue = ctx.Queue()
    for i in range(num_battles):
        task_queue.put(i)
    for _ in range(n_workers):
        task_queue.put(None)

    model.eval()
    reply_queues: dict[int, object] = {}
    processes: list[mp.Process] = []
    # 临时目录：worker 将海量对局数据写入文件，只通过队列传递路径，避免管道死锁
    # 使用 TemporaryDirectory 对象管理生命周期，finally 中显式 cleanup
    temp_dir_ctx = tempfile.TemporaryDirectory(prefix="selfplay_")
    temp_dir = temp_dir_ctx.name
    # 单局时间预算取 stall_timeout_s 的 75%，确保慢局在全局卡死保护之前自行退出
    game_timeout_s = stall_timeout_s * 0.75
    for wid in range(n_workers):
        reply_q = ctx.Queue()
        reply_queues[wid] = reply_q
        seed = random.randint(0, 2**31 - 1)
        proc = ctx.Process(
            target=run_selfplay_worker,
            args=(
                wid, seed,
                num_simulations, max_turns, draw_margin, temperature, root_noise,
                progress_every, game_timeout_s, gamma, tanh_k, leaf_batch_size, mirror,
                task_queue, request_queue, reply_q, result_queue,
                temp_dir,
            ),
        )
        proc.start()
        processes.append(proc)

    server = BatchedInferenceServer(
        model, device, request_queue, reply_queues,
        batch_size=inference_batch_size,
        timeout_ms=inference_timeout_ms,
    )
    server.start()

    xs: list[dict[str, np.ndarray]] = []
    ps: list[np.ndarray] = []
    ms: list[np.ndarray] = []
    vs: list[np.ndarray] = []
    all_reason_counts: dict[str, int] = {}
    finished: set[int] = set()
    done_workers = 0
    battles_done = 0
    last_wait_report = time.monotonic()
    last_progress = time.monotonic()
    stalled = False
    try:
        while done_workers < n_workers:
            try:
                raw_result = result_queue.get(timeout=10.0)
                tag, wid = raw_result[0], raw_result[1]
            except queue.Empty:
                for pid_idx, proc in enumerate(processes):
                    if (
                        pid_idx not in finished
                        and proc.exitcode is not None
                        and proc.exitcode != 0
                    ):
                        raise RuntimeError(
                            f"self-play worker {pid_idx} 提前退出 "
                            f"(pid={proc.pid}, exitcode={proc.exitcode})"
                        )
                now = time.monotonic()
                if now - last_progress >= stall_timeout_s:
                    alive = [
                        wid for wid in range(n_workers)
                        if wid not in finished
                    ]
                    print(
                        f"  ⚠ 卡死保护：{stall_timeout_s:.0f}s 内无任何对局完成，"
                        f"判定 worker {alive} 单局死循环，终止剩余 worker，"
                        f"用已完成的 {battles_done}/{num_battles} 局继续",
                        flush=True,
                    )
                    stalled = True
                    break
                if verbose and now - last_wait_report >= 60.0:
                    print(
                        f"  进度: {battles_done}/{num_battles} 局完成, "
                        f"{done_workers}/{n_workers} workers 退出",
                        flush=True,
                    )
                    last_wait_report = now
                continue

            if tag == "error":
                raise RuntimeError(f"self-play worker {wid} 异常:\n{raw_result[2]}")
            last_progress = time.monotonic()
            if tag == "done":
                finished.add(wid)
                done_workers += 1
                continue
            # tag == "battle"
            filepath, end_reason, battle_summary = raw_result[2], raw_result[3], raw_result[4]
            corrupt = False
            try:
                with open(filepath, "rb") as f:
                    X, P, M, v = pickle.load(f)
            except (EOFError, pickle.UnpicklingError, OSError):
                corrupt = True
                print(f"  ⚠ worker {wid} 产生脏文件，已跳过: {os.path.basename(filepath)}", flush=True)
            finally:
                try:
                    os.unlink(filepath)
                except OSError:
                    pass
            if corrupt:
                battles_done += 1
                all_reason_counts["corrupt_worker"] = all_reason_counts.get("corrupt_worker", 0) + 1
                continue
            if len(X) > 0:
                # timeout 对局样本不可靠（过早截断），跳过不加入训练
                if end_reason != "timeout":
                    xs.extend(X)
                    ps.append(P)
                    ms.append(M)
                    vs.append(v)
            all_reason_counts[end_reason] = all_reason_counts.get(end_reason, 0) + 1
            battles_done += 1
            # 写入对局技能日志
            if battle_log_writer is not None and battle_summary is not None:
                battle_log_writer.write(battle_summary)
            if verbose and (battles_done % max(1, progress_every) == 0):
                print(
                    f"  进度: {battles_done}/{num_battles} 局完成",
                    flush=True,
                )

        if stalled:
            for proc in processes:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=10.0)
        for proc in processes:
            proc.join(timeout=3600)
            if proc.is_alive():
                proc.terminate()
                proc.join()
                raise RuntimeError(f"self-play worker {proc.pid} 超时未结束")
    except Exception:
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10.0)
        raise
    finally:
        server.stop()
        # 清理临时文件（TemporaryDirectory.cleanup 容错处理残余文件）
        try:
            temp_dir_ctx.cleanup()
        except OSError:
            pass

    if not xs:
        return (
            [],
            np.zeros((0, NUM_ACTIONS), dtype=np.float32),
            np.zeros((0, NUM_ACTIONS), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            all_reason_counts,
        )
    return xs, np.concatenate(ps), np.concatenate(ms), np.concatenate(vs), all_reason_counts



def train_rl(
    model: ModularBattleNet,
    replay,
    epochs: int,
    batch_size: int,
    device: str,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None = None,
    val_split: float = 0.1,
) -> list[dict]:
    """训练双头网络：value loss (MSE) + policy loss (cross-entropy)。

    replay: DictReplayBuffer 实例。训练/验证直接按索引从 replay buffer
    构造 batch，避免 DataLoader 逐条 __getitem__ + collate 开销。

    optimizer / scheduler: 由调用方在外部管理（全局学习率衰减），
    scheduler 在每 epoch 结束后 step()。若为 None 则跳过。
    """
    batch_size = max(1, int(batch_size))
    n = len(replay)
    if n < 2:
        print("  [train_rl] 样本不足，跳过训练")
        return []
    n_val = max(1, int(n * val_split))
    n_train = n - n_val
    if n_train == 0:
        print("  [train_rl] 训练样本不足(全被划入验证集)，跳过训练")
        return []

    use_pin = device.startswith("cuda")
    obs_keys = tuple(replay.buffers.keys())

    def make_batch(indices: np.ndarray) -> dict[str, torch.Tensor]:
        batch: dict[str, torch.Tensor] = {
            key: torch.from_numpy(replay.buffers[key][indices])
            for key in obs_keys
        }
        batch["policy"] = torch.from_numpy(replay.policy_buffer[indices])
        batch["mask"] = torch.from_numpy(replay.mask_buffer[indices])
        batch["outcome"] = torch.from_numpy(replay.outcome_buffer[indices])
        if use_pin:
            batch = {key: value.pin_memory() for key, value in batch.items()}
        return batch

    def iter_batches(indices: np.ndarray):
        for start in range(0, len(indices), batch_size):
            yield make_batch(indices[start:start + batch_size])

    def move_batch(batch: dict[str, torch.Tensor]):
        xb = {
            k: v.to(device, non_blocking=use_pin)
            for k, v in batch.items()
            if k not in ("policy", "mask", "outcome")
        }
        pb = batch["policy"].to(device, non_blocking=use_pin)
        mb = batch["mask"].to(device, non_blocking=use_pin)
        vb = batch["outcome"].unsqueeze(1).to(device, non_blocking=use_pin)
        return xb, pb, mb, vb

    val_indices = np.arange(n_train, n, dtype=np.int64)

    history: list[dict] = []

    for epoch in range(epochs):
        model.train()
        total_value_loss = 0.0
        total_policy_loss = 0.0

        train_indices = np.random.permutation(n_train).astype(np.int64, copy=False)
        for batch in iter_batches(train_indices):
            xb, pb, mb, vb = move_batch(batch)

            value, logits = model(xb)
            value_loss = F.mse_loss(value, vb)
            nn = len(vb)
            pb_safe = pb * mb
            pb_sum = pb_safe.sum(dim=-1, keepdim=True).clamp(min=1e-8)
            pb_safe = pb_safe / pb_sum
            masked_logits = logits.masked_fill(mb < 0.5, -1e9)
            per_sample = -torch.sum(pb_safe * F.log_softmax(masked_logits, dim=-1), dim=-1)
            policy_loss = per_sample.mean()
            loss = value_loss + policy_loss

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            total_value_loss += value_loss.item() * nn
            total_policy_loss += policy_loss.item() * nn

        train_v_loss = total_value_loss / n_train
        train_p_loss = total_policy_loss / n_train

        model.eval()
        val_v_loss = 0.0
        val_p_loss = 0.0
        val_correct = 0.0
        with torch.no_grad():
            for batch in iter_batches(val_indices):
                xv, pv, mv, vv = move_batch(batch)
                val_v, val_logits = model(xv)
                val_v_loss += F.mse_loss(val_v, vv).item() * len(vv)
                pb_safe = pv * mv
                pb_sum = pb_safe.sum(dim=-1, keepdim=True).clamp(min=1e-8)
                pb_safe = pb_safe / pb_sum
                masked_logits = val_logits.masked_fill(mv < 0.5, -1e9)
                val_p_loss += -torch.sum(pb_safe * F.log_softmax(masked_logits, dim=-1), dim=-1).sum().item()
                # 按 draw_margin 三分类：+1=胜, 0=平, -1=负，与训练标签一致
                val_flat = val_v.squeeze(1)
                vv_flat = vv.squeeze(1)
                pred = (val_flat > DEFAULT_DRAW_MARGIN).float() - (val_flat < -DEFAULT_DRAW_MARGIN).float()
                true = (vv_flat > DEFAULT_DRAW_MARGIN).float() - (vv_flat < -DEFAULT_DRAW_MARGIN).float()
                val_correct += (pred == true).float().sum().item()
        val_v_loss /= n_val
        val_p_loss /= n_val
        val_acc = val_correct / n_val

        if scheduler is not None:
            scheduler.step()

        history.append({
            "epoch": epoch + 1,
            "train_v_loss": train_v_loss,
            "train_p_loss": train_p_loss,
            "val_v_loss": val_v_loss,
            "val_p_loss": val_p_loss,
            "val_acc": val_acc,
        })

        if (epoch + 1) % 5 == 0 or epoch == 0:
            current_lr = scheduler.get_last_lr()[0] if scheduler is not None else optimizer.param_groups[0]["lr"]
            print(f"  Epoch {epoch + 1:3d}/{epochs}  "
                  f"v_loss={train_v_loss:.4f}/{val_v_loss:.4f}  "
                  f"p_loss={train_p_loss:.4f}/{val_p_loss:.4f}  "
                  f"val_acc={val_acc:.3f}  "
                  f"lr={current_lr:.2e}")

    return history


# ═══════════════════════════════════════════════════════════════════
# 评估门控（AlphaZero：新网络须明显强于旧最优才晋升）
# ═══════════════════════════════════════════════════════════════════

def _clone_model(model, device: str):
    """深拷贝 ModularBattleNet。"""
    from backend.engine.ai.core.model import ModularBattleNet
    clone = ModularBattleNet(
        trunk_dim=model.trunk_dim,
        num_blocks=model.num_blocks,
        dropout=model.dropout_val,
        vocab_size=model.vocab_size,
        ast_max_len=model.ast_max_len,
        with_attention=model.with_attention,
    )
    clone.load_state_dict(model.state_dict())
    clone.to(device)
    clone.eval()
    return clone


def _gate_decision(wins: float, completed: int, total: int, gate: float) -> bool | None:
    if completed <= 0 or total <= 0 or completed > total:
        return None
    remaining = total - completed
    if (wins + remaining) / total < gate:
        return False
    if wins / total >= gate:
        return True
    return None


def evaluate(
    candidate: ModularBattleNet,
    best: ModularBattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    n_games: int,
    num_simulations: int,
    device: str,
    max_turns: int = DEFAULT_EVAL_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    verbose: bool = True,
    leaf_batch_size: int = 1,
    early_stop_gate: float | None = None,
) -> float:
    """candidate vs best 对打，返回 candidate 胜率（平局计 0.5）。

    每局双方各用自己的网络做 MCTS（贪心、无探索噪声）；偶数局 candidate
    执先手 A、奇数局执后手 B，以消除先后手偏置。
    """
    if n_games <= 0:
        return 0.0

    wins = 0.0
    for g in range(n_games):
        team_a, team_b = _random_teams(factory, sprite_skills)
        p1 = factory.build_player("A", team_a, item=_random_item())
        p2 = factory.build_player("B", team_b, item=_random_item())
        battle = factory.build_battle(p1, p2)

        cand_is_a = (g % 2 == 0)
        model_a = candidate if cand_is_a else best
        model_b = best if cand_is_a else candidate

        eval_a = TorchEvaluator(model_a, device)
        eval_b = TorchEvaluator(model_b, device)
        # opp_a 是 A 方 MCTS 搜索中的"对手"（即 B），应使用 B 的网络
        # opp_b 是 B 方 MCTS 搜索中的"对手"（即 A），应使用 A 的网络
        opp_a = NetworkPolicyAgent(evaluator=eval_b, greedy=True)
        opp_b = NetworkPolicyAgent(evaluator=eval_a, greedy=True)
        agent_a = MCTSAgent(
            "A", p1, factory, opp_a, num_simulations,
            temperature=0.0, root_noise=0.0, record=False,
            evaluator=eval_a, opp_greedy=True, max_turns=max_turns,
            leaf_batch_size=leaf_batch_size,
        )
        agent_b = MCTSAgent(
            "B", p2, factory, opp_b, num_simulations,
            temperature=0.0, root_noise=0.0, record=False,
            evaluator=eval_b, opp_greedy=True, max_turns=max_turns,
            leaf_batch_size=leaf_batch_size,
        )

        turn = 0
        while not battle.is_finished and turn < max_turns:
            battle.execute_turn(agent_a, agent_b)
            turn += 1

        outcome_a, _ = battle_outcome_a(
            battle, max_turns, draw_margin=draw_margin,
        )
        wins += eval_score_for_candidate(outcome_a, cand_is_a)
        if early_stop_gate is not None:
            decision = _gate_decision(wins, g + 1, n_games, early_stop_gate)
            if decision is not None:
                if verbose:
                    status = "pass" if decision else "fail"
                    print(
                        f"    eval early-stop {status}: {g + 1}/{n_games} games, "
                        f"score_floor={wins / n_games:.2%}, gate={early_stop_gate:.2%}",
                        flush=True,
                    )
                return early_stop_gate if decision else wins / n_games

        if verbose and (g + 1) % 10 == 0:
            print(f"    评估 {g + 1}/{n_games} 局, 当前胜率 {wins / (g + 1):.2%}")

    return wins / n_games


def _play_one_eval_game(
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    candidate_evaluator,
    best_evaluator,
    game_index: int,
    num_simulations: int,
    max_turns: int,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    game_timeout_s: float = 450.0,
    leaf_batch_size: int = 1,
) -> float:
    """单局 candidate vs best，返回 candidate 得分：胜=1，平=0.5，负=0。

    game_timeout_s: 单局 wall-time 上限，超时强制退出（避免慢局拖死 worker）。
    """
    team_a, team_b = _random_teams(factory, sprite_skills)
    p1 = factory.build_player("A", team_a, item=_random_item())
    p2 = factory.build_player("B", team_b, item=_random_item())
    battle = factory.build_battle(p1, p2)

    cand_is_a = (game_index % 2 == 0)
    eval_a = candidate_evaluator if cand_is_a else best_evaluator
    eval_b = best_evaluator if cand_is_a else candidate_evaluator

    # opp_a 是 A 方 MCTS 搜索中的"对手"（即 B），应使用 B 的网络
    opp_a = NetworkPolicyAgent(evaluator=eval_b, greedy=True)
    opp_b = NetworkPolicyAgent(evaluator=eval_a, greedy=True)
    agent_a = MCTSAgent(
        "A", p1, factory, opp_a, num_simulations,
        temperature=0.0, root_noise=0.0, record=False,
        evaluator=eval_a, opp_greedy=True, max_turns=max_turns,
        leaf_batch_size=leaf_batch_size,
    )
    agent_b = MCTSAgent(
        "B", p2, factory, opp_b, num_simulations,
        temperature=0.0, root_noise=0.0, record=False,
        evaluator=eval_b, opp_greedy=True, max_turns=max_turns,
        leaf_batch_size=leaf_batch_size,
    )

    battle_started = time.monotonic()
    turn = 0
    while not battle.is_finished and turn < max_turns:
        battle.execute_turn(agent_a, agent_b)
        turn += 1
        if time.monotonic() - battle_started >= game_timeout_s:
            break

    outcome_a, _ = battle_outcome_a(
        battle, max_turns, draw_margin=draw_margin,
    )
    return eval_score_for_candidate(outcome_a, cand_is_a)


def evaluate_parallel(
    candidate: ModularBattleNet,
    best: ModularBattleNet,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    n_games: int,
    num_workers: int,
    device: str,
    inference_batch_size: int = 128,
    inference_timeout_ms: float = 5.0,
    num_simulations: int = 100,
    max_turns: int = DEFAULT_EVAL_MAX_TURNS,
    draw_margin: float = DEFAULT_DRAW_MARGIN,
    verbose: bool = True,
    progress_every: int = 1,
    stall_timeout_s: float = 600.0,
    leaf_batch_size: int = 1,
    early_stop_gate: float | None = None,
) -> float:
    """多进程局级门控评估 + 主进程双模型批量推理。

    work-stealing + 卡死保护（见 collect_rl_samples_parallel）。若发生卡死，
    胜率按已完成的对局数计算，避免单局死循环冻结整轮门控。
    """
    if n_games <= 0:
        return 0.0

    n_workers = max(1, min(num_workers, n_games))

    if verbose:
        print(
            f"  并行门控评估: {n_workers} workers 动态领取 {n_games} 局, "
            f"batch={inference_batch_size}, timeout={inference_timeout_ms}ms",
            flush=True,
        )

    ctx = mp.get_context("spawn")
    request_queue = ctx.Queue(maxsize=n_workers * 4)
    result_queue = ctx.Queue(maxsize=n_workers * 2)
    # 任务队列：n_games 个 game_index（决定先后手交替）+ 每 worker 一个停止哨兵。
    task_queue = ctx.Queue()
    for game_index in range(n_games):
        task_queue.put(game_index)
    for _ in range(n_workers):
        task_queue.put(None)

    candidate.eval()
    best.eval()
    reply_queues: dict[int, dict[str, object]] = {}
    processes: list[mp.Process] = []
    # 单局时间预算：取 stall_timeout_s 的一半，确保慢局在全局卡死保护之前自行退出
    game_timeout_s = stall_timeout_s * 0.75
    for wid in range(n_workers):
        candidate_q = ctx.Queue()
        best_q = ctx.Queue()
        reply_queues[wid] = {"candidate": candidate_q, "best": best_q}
        seed = random.randint(0, 2**31 - 1)
        proc = ctx.Process(
            target=run_evaluate_worker,
            args=(
                wid, seed,
                num_simulations, max_turns, draw_margin, progress_every,
                game_timeout_s, leaf_batch_size,
                task_queue, request_queue, candidate_q, best_q, result_queue,
            ),
        )
        proc.start()
        processes.append(proc)

    server = BatchedModelInferenceServer(
        {"candidate": candidate, "best": best},
        device, request_queue, reply_queues,
        batch_size=inference_batch_size,
        timeout_ms=inference_timeout_ms,
    )
    server.start()

    wins = 0.0
    completed_games = 0
    finished: set[int] = set()
    done_workers = 0
    last_wait_report = time.monotonic()
    last_progress = time.monotonic()
    stalled = False
    gate_decision: bool | None = None
    try:
        while done_workers < n_workers and gate_decision is None:
            try:
                raw_result = result_queue.get(timeout=10.0)
                tag, wid = raw_result[0], raw_result[1]
            except queue.Empty:
                for pid_idx, proc in enumerate(processes):
                    if (
                        pid_idx not in finished
                        and proc.exitcode is not None
                        and proc.exitcode != 0
                    ):
                        raise RuntimeError(
                            f"eval worker {pid_idx} 提前退出 "
                            f"(pid={proc.pid}, exitcode={proc.exitcode})"
                        )
                now = time.monotonic()
                if now - last_progress >= stall_timeout_s:
                    alive = [
                        wid for wid in range(n_workers)
                        if wid not in finished
                    ]
                    print(
                        f"  ⚠ 卡死保护：{stall_timeout_s:.0f}s 内无任何对局完成，"
                        f"判定 eval worker {alive} 单局死循环，终止剩余 worker，"
                        f"用已完成的 {completed_games}/{n_games} 局计算胜率",
                        flush=True,
                    )
                    stalled = True
                    break
                if verbose and now - last_wait_report >= 60.0:
                    print(
                        f"  评估进度: {completed_games}/{n_games} 局完成, "
                        f"{done_workers}/{n_workers} workers 退出",
                        flush=True,
                    )
                    last_wait_report = now
                continue

            if tag == "error":
                raise RuntimeError(f"eval worker {wid} 异常:\n{raw_result[3]}")
            last_progress = time.monotonic()
            if tag == "done":
                finished.add(wid)
                done_workers += 1
                continue
            # tag == "game"
            wins += float(raw_result[2])
            completed_games += 1
            if early_stop_gate is not None:
                gate_decision = _gate_decision(
                    wins, completed_games, n_games, early_stop_gate,
                )
                if gate_decision is not None and verbose:
                    status = "pass" if gate_decision else "fail"
                    print(
                        f"  eval early-stop {status}: {completed_games}/{n_games} games, "
                        f"score_floor={wins / n_games:.2%}, gate={early_stop_gate:.2%}",
                        flush=True,
                    )
            if verbose and (completed_games % max(1, progress_every) == 0):
                print(
                    f"  评估进度: {completed_games}/{n_games} 局, "
                    f"当前胜率 {wins / max(1, completed_games):.2%}",
                    flush=True,
                )

        if stalled or gate_decision is not None:
            for proc in processes:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=10.0)
        for proc in processes:
            proc.join(timeout=3600)
            if proc.is_alive():
                proc.terminate()
                proc.join()
                raise RuntimeError(f"eval worker {proc.pid} 超时未结束")
    except Exception:
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10.0)
        raise
    finally:
        server.stop()

    if gate_decision is True:
        return early_stop_gate if early_stop_gate is not None else wins / n_games
    if gate_decision is False:
        return wins / n_games
    denom = completed_games if stalled else n_games
    return wins / denom if denom > 0 else 0.0


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AlphaZero 风格 RL 训练")
    parser.add_argument("--battles", type=int, default=200,
                        help="每轮迭代自我博弈局数 (default: 200)")
    parser.add_argument("--sims", type=int, default=200,
                        help="MCTS 每步模拟次数 (default: 200)")
    parser.add_argument("--iterations", type=int, default=5,
                        help="RL 迭代轮数 (default: 5)")
    parser.add_argument("--epochs", type=int, default=20,
                        help="每轮训练 epoch 数 (default: 20)")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden", type=str, default="256,128")
    parser.add_argument("--dropout", type=float, default=0.0)
    parser.add_argument("--output", type=str, default="checkpoints/model_rl.pt")
    parser.add_argument("--resume", type=str, default="")
    parser.add_argument("--base-model", type=str,
                        default="",
                        help="基座模型路径（无 --resume 时从此权重加载而非随机初始化）")
    parser.add_argument("--device", type=str, default="")
    parser.add_argument("--data-cache", type=str, default="",
                        help="监督模式: 预生成数据缓存路径")
    parser.add_argument("--temperature", type=float, default=1.0,
                        help="自我博弈动作采样温度 (default: 1.0)")
    parser.add_argument("--weight-decay", type=float, default=1e-4,
                        help="L2 正则系数 (default: 1e-4)")
    parser.add_argument("--buffer", type=int, default=5,
                        help="经验回放缓冲：保留最近 N 轮自我博弈数据混合训练 (default: 5)")
    parser.add_argument("--eval-games", type=int, default=20,
                        help="每轮门控评估对局数，0=关闭门控 (default: 20)")
    parser.add_argument("--eval-sims", type=int, default=100,
                        help="门控评估时每步 MCTS 模拟次数 (default: 100)")
    parser.add_argument("--eval-workers", type=int, default=0,
                        help="门控评估并行 worker 数 (default: 0=自动跟随 --workers)")
    parser.add_argument("--gate", type=float, default=0.55,
                        help="晋升阈值：候选胜率≥该值才替换最优模型 (default: 0.55)")
    parser.add_argument("--root-noise", type=float, default=0.25,
                        help="自我博弈根节点 Dirichlet 噪声强度 (default: 0.25)")
    parser.add_argument("--workers", type=int, default=1,
                        help="自我博弈并行 worker 数 (default: 1=串行)")
    parser.add_argument("--batched-inference", action="store_true",
                        help="多 worker 时由主进程 CUDA 批量推理（需 workers>1）")
    parser.add_argument("--inference-batch-size", type=int, default=128,
                        help="批量推理最大 batch (default: 128)")
    parser.add_argument("--inference-timeout-ms", type=float, default=5.0,
                        help="攒 batch 等待毫秒 (default: 5)")
    parser.add_argument("--leaf-batch-size", type=int, default=1,
                        help="MCTS 叶节点批量评估大小；1=串行路径 (default: 1)")
    parser.add_argument("--progress-every", type=int, default=10,
                        help="自我博弈每 N 局打印进度 (default: 10)")
    parser.add_argument("--worker-stall-timeout", type=float, default=600.0,
                        help="并行采样/评估时，若全部 worker 在 N 秒内都无任何对局完成，"
                             "判定为卡死并终止剩余 worker，用已完成的对局继续 (default: 600)")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_SELFPLAY_MAX_TURNS,
                        help=f"自我博弈单局回合上限 (default: {DEFAULT_SELFPLAY_MAX_TURNS})")
    parser.add_argument("--eval-max-turns", type=int, default=DEFAULT_EVAL_MAX_TURNS,
                        help=f"门控评估单局回合上限 (default: {DEFAULT_EVAL_MAX_TURNS})")
    parser.add_argument("--draw-margin", type=float, default=DEFAULT_DRAW_MARGIN,
                        help=f"打满回合时局面分差低于此值记平局 (default: {DEFAULT_DRAW_MARGIN})")
    parser.add_argument("--gamma", type=float, default=1.0,
                        help="回合衰减因子 gamma < 1 时让速胜价值高于拖沓胜，"
                             "gamma=1.0 不衰减 (default: 1.0)")
    parser.add_argument("--tanh-k", type=float, default=0.0,
                        help="tanh 软裁决缩放系数 k > 0 时将非决胜对局的分差连续映射到 (-1,1)，"
                             "k=0 为硬阈值 (default: 0)")
    parser.add_argument("--mirror-frac", type=float, default=0.0,
                        help="镜像迭代比例：前 N%% 的迭代中全部对局使用相同阵容 (default: 0)")
    parser.add_argument("--log-dir", type=str, default="backend/engine/ai/log",
                        help="训练日志目录，自动写全量日志+结构化指标+汇总 (default: backend/engine/ai/log)")
    parser.add_argument("--no-log", action="store_true",
                        help="关闭自动日志记录")
    parser.add_argument("--run-name", type=str, default="",
                        help="实验批次名称，模型和日志自动存入对应子目录 (如 --run-name resnet_v1)")
    args = parser.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")

    # eval_workers=0 时自动跟随 self-play workers（评估不再串行拖死）
    eval_workers = args.eval_workers if args.eval_workers > 0 else args.workers

    logger = None
    battle_log = None
    run_name = args.run_name.strip() if args.run_name else ""
    if run_name:
        print(f"实验批次: {run_name}")

    # 日志/模型子目录
    log_dir = args.log_dir

    def _log(msg: str) -> None:
        """同时输出到控制台和全量日志。"""
        print(msg, flush=True)
        if logger is not None:
            logger.info(msg)

    checkpoints_dir = "checkpoints"
    if run_name:
        log_dir = f"{args.log_dir}/{run_name}"
        checkpoints_dir = f"checkpoints/{run_name}"

    if not args.no_log:
        from backend.engine.ai.run_logger import RunLogger
        log_params = {
            "mode": "rl", "device": device,
            "iterations": args.iterations, "battles": args.battles,
            "sims": args.sims, "epochs": args.epochs,
            "batch_size": args.batch_size, "lr": args.lr,
            "hidden": args.hidden, "buffer": args.buffer,
            "eval_games": args.eval_games, "eval_sims": args.eval_sims,
            "gate": args.gate, "max_turns": args.max_turns,
            "eval_max_turns": args.eval_max_turns, "draw_margin": args.draw_margin,
            "workers": args.workers, "eval_workers": eval_workers,
            "leaf_batch_size": args.leaf_batch_size,
            "weight_decay": args.weight_decay,
            "dropout": args.dropout,
            "mirror_frac": args.mirror_frac,
            "run_name": run_name,
        }
        logger = RunLogger(log_dir, params=log_params)

        # 创建对局技能日志写入器
        battle_log = BattleLogWriter(log_dir, run_id=logger.run_id)

    _log(f"设备: {device}")

    _log("加载精灵技能数据...")
    sprite_skills = _load_sprite_skills()
    _log(f"  可用精灵: {len(sprite_skills)}")

    factory = SimFactory()
    hidden = tuple(int(h) for h in args.hidden.split(","))

    # ── RL 模式 ──
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.core.replay_buffer import DictReplayBuffer

    if args.resume:
        _log(f"加载模型: {args.resume}")
        model = ModularBattleNet.load(args.resume, device=device)
    elif args.base_model and Path(args.base_model).exists():
        _log(f"加载基座模型: {args.base_model}")
        model = ModularBattleNet.load(args.base_model, device=device)
    else:
        if args.base_model:
            _log(f"基座模型未找到: {args.base_model}，使用随机初始化")
        model = ModularBattleNet(
            trunk_dim=hidden[0] if hidden else 256,
            num_blocks=4,
            dropout=args.dropout,
            vocab_size=VOCAB_SIZE,
            with_attention=True,
        )
    model.to(device)
    _log(f"模型类型: ModularBattleNet (模块化+残差+注意力)")
    _log(f"模型参数量: {model.num_params:,}")

    # 最优模型副本（门控基准）
    best_model = _clone_model(model, device)
    Path(checkpoints_dir).mkdir(parents=True, exist_ok=True)
    best_ckpt = f"{checkpoints_dir}/model_rl_best.pt"

    # 经验回放缓冲：预分配 numpy 数组，按 sample 粒度循环覆盖。
    # 容量 = buffer × battles × expected_turns_per_game
    # expected_turns_per_game ≈ max_turns // 6（实际数据：200局 ≈3000样本 ≈15回合/局）
    expected_tpg = max(1, args.max_turns // 6)
    buffer_capacity = args.buffer * args.battles * expected_tpg
    replay = DictReplayBuffer(capacity=max(10000, buffer_capacity))
    best_iteration: int | None = None

    # ── 全局学习率衰减 + 预热 ──
    # optimizer 和 scheduler 在训练循环外部管理，跨所有 iteration 持续衰减。
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    total_steps = args.iterations * args.epochs
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=total_steps, eta_min=args.lr * 0.01,
    )

    for iteration in range(1, args.iterations + 1):
        iteration_started = time.time()
        _log(f"\n{'=' * 60}")
        _log(f"RL 迭代 {iteration}/{args.iterations}")
        _log(f"{'=' * 60}")

        # 镜像对局：前 N% 迭代中所有对局均为镜像阵容（每迭代判定一次，非局级）
        all_mirror = args.mirror_frac > 0 and iteration <= int(args.iterations * args.mirror_frac)
        if all_mirror:
            _log("  镜像迭代：全部对局使用相同阵容")

        # ── 切换轮次：学习率预热（Warmup） ──
        # 镜像迭代结束后即为数据分布切换点（从镜像到随机阵容），
        # 在此轮拉升学习率并重建 scheduler，给剩余全局衰减一个大冲击。
        if args.mirror_frac > 0:
            transition_iteration = int(args.iterations * args.mirror_frac) + 1
            if iteration == transition_iteration:
                warmup_lr = args.lr * 0.8
                for param_group in optimizer.param_groups:
                    param_group["lr"] = warmup_lr
                remaining_steps = (args.iterations - iteration + 1) * args.epochs
                scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                    optimizer, T_max=remaining_steps, eta_min=args.lr * 0.01,
                )
                _log(
                    f"\n  [Phase Shift] 镜像迭代结束，切换数据分布，执行学习率预热 (Warmup)\n"
                    f"  当前 LR 拉升至 {warmup_lr:.2e}\n"
                )

        # ── 自我博弈收集数据（用当前最优模型产生对局，质量更稳） ──
        temp = args.temperature * (0.9 ** (iteration - 1))  # 温度递减
        # 镜像迭代：降低 sims 加速回合推进，减少 timeout（相同阵容不确定性低）
        effective_sims = max(args.sims, 50) if all_mirror else args.sims
        use_parallel = args.batched_inference and args.workers > 1
        mode = f"{args.workers} workers + 批量推理" if use_parallel else "串行"
        _log(f"自我博弈 ({args.battles} 局, {effective_sims} sims, T={temp:.2f}, {mode})...")
        t0 = time.time()
        if use_parallel:
            X, P, M, v, reason_counts = collect_rl_samples_parallel(
                best_model, factory, sprite_skills,
                num_battles=args.battles,
                num_workers=args.workers,
                device=device,
                inference_batch_size=args.inference_batch_size,
                inference_timeout_ms=args.inference_timeout_ms,
                num_simulations=effective_sims,
                max_turns=args.max_turns,
                draw_margin=args.draw_margin,
                temperature=temp,
                root_noise=args.root_noise,
                progress_every=args.progress_every,
                stall_timeout_s=args.worker_stall_timeout,
                battle_log_writer=battle_log,
                gamma=args.gamma,
                tanh_k=args.tanh_k,
                leaf_batch_size=args.leaf_batch_size,
                mirror=all_mirror,
            )
        else:
            X, P, M, v, reason_counts = collect_rl_samples(
                best_model, factory, sprite_skills,
                num_battles=args.battles,
                num_simulations=effective_sims,
                device=device,
                max_turns=args.max_turns,
                draw_margin=args.draw_margin,
                temperature=temp,
                root_noise=args.root_noise,
                progress_every=args.progress_every,
                battle_log_writer=battle_log,
                gamma=args.gamma,
                tanh_k=args.tanh_k,
                leaf_batch_size=args.leaf_batch_size,
                mirror=all_mirror,
            )
        selfplay_sec = time.time() - t0
        rate = len(X) / selfplay_sec if selfplay_sec > 0 else 0.0
        _log(f"  完成: {len(X)} 样本, {selfplay_sec:.1f}s ({rate:.0f} 样本/s)")

        pos, neg, zero = int((v > 0).sum()), int((v < 0).sum()), int((v == 0).sum())
        _log(f"  结果分布: 本方赢={pos}  本方输={neg}  平={zero}")
        _log(f"  终局原因(局): {format_reason_counts(reason_counts)}")

        if len(X) == 0:
            _log("  本轮无样本，跳过。")
            if logger is not None:
                logger.record_iteration({
                    "iteration": iteration, "samples": 0, "skipped": True,
                })
            continue

        total_games = sum(reason_counts.values())
        decisive_games = sum(
            n for k, n in reason_counts.items() if not k.endswith("_draw")
        )
        draw_ratio = (zero / len(v)) if len(v) else 0.0

        # ── 合并回放缓冲 ──
        pushed = replay.push_batch(X, P, M, v)
        _log(f"  回放缓冲: {len(replay)} 样本 (本轮 +{pushed})")

        # ── 训练（候选 = 在 best 基础上继续训练） ──
        _log(f"训练 ({args.epochs} epochs)...")
        t0 = time.time()
        history = train_rl(
            model, replay, args.epochs, args.batch_size,
            device, optimizer=optimizer, scheduler=scheduler,
        )
        train_sec = time.time() - t0
        _log(f"  完成: {train_sec:.1f}s")
        final_metrics = history[-1] if history else {}
        best_val_acc = max((h["val_acc"] for h in history), default=0.0)

        # ── 门控评估：候选 vs 最优 ──
        win_rate = None
        promoted = False
        eval_sec = 0.0
        checkpoint_sec = 0.0
        if args.eval_games > 0:
            eval_mode = f"{eval_workers} workers + 批量推理" if eval_workers > 1 else "串行"
            print(
                f"门控评估（候选 vs 最优, {args.eval_games} 局, "
                f"{args.eval_sims} sims, {eval_mode}）..."
            )
            t0 = time.time()
            if eval_workers > 1:
                win_rate = evaluate_parallel(
                    model, best_model, factory, sprite_skills,
                    n_games=args.eval_games,
                    num_workers=eval_workers,
                    device=device,
                    inference_batch_size=args.inference_batch_size,
                    inference_timeout_ms=args.inference_timeout_ms,
                    num_simulations=args.eval_sims,
                    max_turns=args.eval_max_turns,
                    draw_margin=args.draw_margin,
                    progress_every=args.progress_every,
                    stall_timeout_s=args.worker_stall_timeout,
                    leaf_batch_size=args.leaf_batch_size,
                    early_stop_gate=args.gate,
                )
            else:
                win_rate = evaluate(
                    model, best_model, factory, sprite_skills,
                    n_games=args.eval_games, num_simulations=args.eval_sims, device=device,
                    max_turns=args.eval_max_turns,
                    draw_margin=args.draw_margin,
                    leaf_batch_size=args.leaf_batch_size,
                    early_stop_gate=args.gate,
                )
            eval_sec = time.time() - t0
            _log(f"  候选胜率: {win_rate:.2%}  ({eval_sec:.1f}s)")
            if win_rate >= args.gate:
                best_model.load_state_dict(model.state_dict())
                t_save = time.time()
                best_model.save(best_ckpt)
                checkpoint_sec += time.time() - t_save
                promoted = True
                best_iteration = iteration
                _log(f"  ✓ 候选晋升为新最优，已保存: {best_ckpt}")
            else:
                # 回滚：候选退回到当前最优，避免越练越差
                model.load_state_dict(best_model.state_dict())
                _log("  ✗ 未达门控阈值，回滚到最优模型")
        else:
            # 未启用门控：直接把候选当作最优（用于产生下一轮自我博弈）
            best_model.load_state_dict(model.state_dict())
            best_iteration = iteration

        # ── 保存检查点 ──
        ckpt = f"{checkpoints_dir}/model_rl_iter{iteration}.pt"
        t_save = time.time()
        model.save(ckpt)
        checkpoint_sec += time.time() - t_save
        _log(f"  检查点已保存: {ckpt}")

        if logger is not None:
            iteration_sec = time.time() - iteration_started
            accounted_sec = selfplay_sec + train_sec + eval_sec + checkpoint_sec
            other_sec = max(0.0, iteration_sec - accounted_sec)
            phase_seconds = {
                "selfplay": round(selfplay_sec, 1),
                "train": round(train_sec, 1),
                "eval": round(eval_sec, 1),
                "checkpoint": round(checkpoint_sec, 1),
                "other": round(other_sec, 1),
            }
            phase_percent = {
                k: round((v / iteration_sec) * 100, 1) if iteration_sec > 0 else 0.0
                for k, v in phase_seconds.items()
            }
            logger.record_iteration({
                "iteration": iteration,
                "samples": int(len(X)),
                "win": pos, "loss": neg, "draw": zero,
                "draw_ratio": round(draw_ratio, 4),
                "total_games": total_games,
                "decisive_games": decisive_games,
                "reason_counts": reason_counts,
                "buffer_samples": int(len(replay)),
                "selfplay_sec": round(selfplay_sec, 1),
                "samples_per_sec": round(rate, 1),
                "train_sec": round(train_sec, 1),
                "eval_sec": round(eval_sec, 1),
                "checkpoint_sec": round(checkpoint_sec, 1),
                "other_sec": round(other_sec, 1),
                "iteration_sec": round(iteration_sec, 1),
                "phase_seconds": phase_seconds,
                "phase_percent": phase_percent,
                "epochs": args.epochs,
                "final_train_v_loss": round(final_metrics.get("train_v_loss", 0.0), 4),
                "final_val_v_loss": round(final_metrics.get("val_v_loss", 0.0), 4),
                "final_train_p_loss": round(final_metrics.get("train_p_loss", 0.0), 4),
                "final_val_p_loss": round(final_metrics.get("val_p_loss", 0.0), 4),
                "final_val_acc": round(final_metrics.get("val_acc", 0.0), 4),
                "best_val_acc": round(best_val_acc, 4),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "gate": args.gate,
                "promoted": promoted,
            })

    best_model.save(f"{checkpoints_dir}/model_rl.pt")
    _log(f"\n最终模型（最优）: {checkpoints_dir}/model_rl.pt")

    if logger is not None:
        logger.finalize(best_iteration=best_iteration)
    if battle_log is not None:
        battle_log.close()
        _log(f"  对局技能日志: {battle_log.path}")


if __name__ == "__main__":
    main()
