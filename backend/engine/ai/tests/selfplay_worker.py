"""backend/engine/ai/selfplay_worker.py — 多进程 self-play worker 入口

独立模块，避免 Windows spawn 时 re-import train 主入口造成副作用。
"""

from __future__ import annotations

import os
import pickle
import random
import time
import traceback

import numpy as np

from backend.engine.ai.core.evaluator import QueueModelEvaluator, QueuePolicyEvaluator
from backend.engine.ai.core.mcts import NUM_ACTIONS


def run_selfplay_worker(
    worker_id: int,
    seed: int,
    num_simulations: int,
    max_turns: int,
    draw_margin: float,
    temperature: float,
    root_noise: float,
    progress_every: int,
    game_timeout_s: float,
    gamma: float,
    tanh_k: float,
    leaf_batch_size: int,
    mirror: bool,
    task_queue,
    request_queue,
    reply_queue,
    result_queue,
    temp_dir: str,
) -> None:
    """子进程：动态领取单局 self-play 任务，推理经主进程批量 CUDA 服务。

    工作流：循环从 task_queue 领一局任务，打完把单局结果序列化到临时文件，
    只将文件路径通过 result_queue 传回主进程，避免 mp.Queue 底层管道缓冲区
    因海量 numpy 数据导致的死锁。

    result_queue 消息为 8 元组 (tag, worker_id, a, b, c, d, e, f)：
      ("battle", wid, filepath, end_reason, battle_summary, None, None, None)
          filepath: 临时 pickle 文件路径，内含 (states, P, M, v)
      ("done",   wid, None, None, None, None, None, None)
      ("error",  wid, traceback, None, None, None, None, None)
    """
    # 延迟导入，避免与 train 顶层循环依赖
    from backend.engine.ai.train import _load_sprite_skills, _play_one_rl_battle
    from backend.sim.factory import SimFactory

    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))

    evaluator = QueuePolicyEvaluator(worker_id, request_queue, reply_queue)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()

    battle_counter = 0
    try:
        pe = max(1, progress_every)
        done = 0
        try:
            while True:
                task = task_queue.get()
                if task is None:
                    break
                battle_idx = int(task)
                battle_started = time.monotonic()
                states, probs, masks, outcomes, end_reason, battle_summary = _play_one_rl_battle(
                    factory, sprite_skills, evaluator,
                    num_simulations, max_turns, temperature, root_noise,
                    draw_margin=draw_margin,
                    game_timeout_s=game_timeout_s,
                    gamma=gamma, tanh_k=tanh_k,
                    leaf_batch_size=leaf_batch_size,
                    mirror=mirror,
                )
                # states 现在是 list[dict[str, np.ndarray]]，无需 stack
                P = np.stack(probs).astype(np.float32) if probs else np.zeros((0, NUM_ACTIONS), dtype=np.float32)
                M = np.stack(masks).astype(np.float32) if masks else np.zeros((0, NUM_ACTIONS), dtype=np.float32)
                v = np.array(outcomes, dtype=np.float32) if outcomes else np.zeros((0,), dtype=np.float32)

                # 写入临时文件，避免 mp.Queue 传输海量 numpy 数据导致管道死锁
                filepath = os.path.join(temp_dir, f"battle_w{worker_id}_{battle_counter}.pkl")
                with open(filepath, "wb") as f:
                    pickle.dump((states, P, M, v), f, protocol=pickle.HIGHEST_PROTOCOL)
                result_queue.put(("battle", worker_id, filepath, end_reason, battle_summary, None, None, None))

                battle_counter += 1
                done += 1
        finally:
            result_queue.put(("done", worker_id, None, None, None, None, None, None))
    except Exception:  # noqa: BLE001
        result_queue.put(("error", worker_id, traceback.format_exc(), None, None, None, None, None))


def run_evaluate_worker(
    worker_id: int,
    seed: int,
    num_simulations: int,
    max_turns: int,
    draw_margin: float,
    progress_every: int,
    game_timeout_s: float,
    leaf_batch_size: int,
    task_queue,
    request_queue,
    candidate_reply_q,
    best_reply_q,
    result_queue,
) -> None:
    """子进程：动态领取单局门控评估任务，候选/最优推理经主进程批量服务。

    task_queue 中每个任务是全局 game_index（决定先后手交替）；领到 None 退出。
    candidate_reply_q / best_reply_q 分别为两个模型分配独立回复队列，
    避免共享 mp.Queue 导致 Windows pipe 竞态死锁。

    result_queue 消息为 4 元组 (tag, worker_id, score, error)：
      ("game",  wid, score, None)  单局候选得分
      ("done",  wid, None, None)   该 worker 已领完退出
      ("error", wid, None, traceback)  worker 异常
    """
    # 延迟导入，避免与 train 顶层循环依赖
    from backend.engine.ai.train import _load_sprite_skills, _play_one_eval_game
    from backend.sim.factory import SimFactory

    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))

    candidate_eval = QueueModelEvaluator(worker_id, "candidate", request_queue, candidate_reply_q, reply_timeout_s=60.0)
    best_eval = QueueModelEvaluator(worker_id, "best", request_queue, best_reply_q, reply_timeout_s=60.0)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()

    try:
        pe = max(1, progress_every)
        done = 0
        try:
            while True:
                task = task_queue.get()
                if task is None:
                    break
                game_index = int(task)
                battle_started = time.monotonic()
                score = _play_one_eval_game(
                    factory, sprite_skills, candidate_eval, best_eval,
                    game_index, num_simulations, max_turns,
                    draw_margin=draw_margin,
                    game_timeout_s=game_timeout_s,
                    leaf_batch_size=leaf_batch_size,
                )
                result_queue.put(("game", worker_id, float(score), None))

                done += 1
        finally:
            result_queue.put(("done", worker_id, None, None))
    except Exception:  # noqa: BLE001
        result_queue.put(("error", worker_id, None, traceback.format_exc()))
