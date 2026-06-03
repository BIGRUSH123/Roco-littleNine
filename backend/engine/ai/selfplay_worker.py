"""backend/engine/ai/selfplay_worker.py — 多进程 self-play worker 入口

独立模块，避免 Windows spawn 时 re-import train 主入口造成副作用。
"""

from __future__ import annotations

import random
import time
import traceback

import numpy as np

from backend.engine.ai.evaluator import QueueModelEvaluator, QueuePolicyEvaluator
from backend.engine.ai.mcts import NUM_ACTIONS


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
    task_queue,
    request_queue,
    reply_queue,
    result_queue,
) -> None:
    """子进程：动态领取单局 self-play 任务，推理经主进程批量 CUDA 服务。

    工作流：循环从 task_queue 领一局任务，打完把**单局**结果 put 回 result_queue，
    再领下一局；领到 None 哨兵则退出。这样慢局只拖住自己，不会阻塞其他 worker。

    result_queue 消息为 6 元组 (tag, worker_id, a, b, c, d)：
      ("battle", wid, X, P, v, end_reason)  单局样本
      ("done",   wid, None, None, None, None)  该 worker 已领完退出
      ("error",  wid, traceback, None, None, None)  worker 异常
    """
    # 延迟导入，避免与 train 顶层循环依赖
    from backend.engine.ai.train import _load_sprite_skills, _play_one_rl_battle
    from backend.sim.factory import SimFactory

    random.seed(seed)
    np.random.seed(seed % (2**32 - 1))

    evaluator = QueuePolicyEvaluator(worker_id, request_queue, reply_queue)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()

    try:
        pe = max(1, progress_every)
        done = 0
        while True:
            task = task_queue.get()
            if task is None:
                break
            battle_started = time.monotonic()
            states, probs, outcomes, end_reason, battle_summary = _play_one_rl_battle(
                factory, sprite_skills, evaluator,
                num_simulations, max_turns, temperature, root_noise,
                draw_margin=draw_margin,
                game_timeout_s=game_timeout_s,
            )
            if states:
                X = np.stack(states).astype(np.float32)
                P = np.stack(probs).astype(np.float32)
                v = np.array(outcomes, dtype=np.float32)
            else:
                X = np.zeros((0, 446), dtype=np.float32)
                P = np.zeros((0, NUM_ACTIONS), dtype=np.float32)
                v = np.zeros((0,), dtype=np.float32)
            result_queue.put(("battle", worker_id, X, P, v, end_reason, battle_summary))

            done += 1
            if done % pe == 0:
                elapsed = time.monotonic() - battle_started
                print(
                    f"  [worker {worker_id}] 已完成 {done} 局, "
                    f"本局 {len(v)} 样本 {elapsed:.1f}s",
                    flush=True,
                )
        result_queue.put(("done", worker_id, None, None, None, None))
    except Exception:  # noqa: BLE001
        result_queue.put(("error", worker_id, traceback.format_exc(), None, None, None))


def run_evaluate_worker(
    worker_id: int,
    seed: int,
    num_simulations: int,
    max_turns: int,
    draw_margin: float,
    progress_every: int,
    task_queue,
    request_queue,
    reply_queue,
    result_queue,
) -> None:
    """子进程：动态领取单局门控评估任务，候选/最优推理经主进程批量服务。

    task_queue 中每个任务是全局 game_index（决定先后手交替）；领到 None 退出。
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

    candidate_eval = QueueModelEvaluator(worker_id, "candidate", request_queue, reply_queue, reply_timeout_s=60.0)
    best_eval = QueueModelEvaluator(worker_id, "best", request_queue, reply_queue, reply_timeout_s=60.0)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()

    try:
        pe = max(1, progress_every)
        done = 0
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
            )
            result_queue.put(("game", worker_id, float(score), None))

            done += 1
            if done % pe == 0:
                elapsed = time.monotonic() - battle_started
                print(
                    f"    [eval worker {worker_id}] 已完成 {done} 局 "
                    f"(本局得分 {score:.1f}, {elapsed:.1f}s)",
                    flush=True,
                )
        result_queue.put(("done", worker_id, None, None))
    except Exception:  # noqa: BLE001
        result_queue.put(("error", worker_id, None, traceback.format_exc()))
