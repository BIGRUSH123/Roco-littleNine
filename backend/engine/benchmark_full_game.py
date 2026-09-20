"""benchmark_full_game — 整局对局基准（完整路径 + headless 路径）。

用法（项目根）：
    D:\\projects\\Roco-LittleNine\\env\\python.exe -m backend.engine.benchmark_full_game [--battles 20] [--seeds 1-20]

输出每场耗时与回合/秒，用于优化前后对比。固定种子，可复现。
"""

from __future__ import annotations

import argparse
import random
import statistics
import time

from backend.sim.agent import RuleAgent

from .differential.recorder import build_seeded_battle
from .differential.generate_fixtures import parse_seeds


def bench_full(battles: list[int]) -> None:
    times: list[float] = []
    turn_counts: list[int] = []
    for seed in battles:
        battle, a, b, _meta = build_seeded_battle(seed)
        battle.verbose = False  # 基准排除控制台打印 I/O
        t0 = time.perf_counter()
        battle.run(a, b)
        dt = time.perf_counter() - t0
        times.append(dt)
        turn_counts.append(battle.turn)
    _report('full (Battle.run + 快照序列化)', battles, times, turn_counts)


def bench_headless(battles: list[int]) -> None:
    """headless：无事件记录、无快照（MCTS 仿真同路径）。"""
    times: list[float] = []
    turn_counts: list[int] = []
    for seed in battles:
        battle, a, b, _meta = build_seeded_battle(seed)
        battle.player_a.active_index = a.choose_lead(battle)
        battle.player_b.active_index = b.choose_lead(battle)
        battle._invalidate_ctx_team_cache()
        t0 = time.perf_counter()
        turns = 0
        while not battle.is_finished and turns < 300:
            battle.execute_turn_headless(a, b)
            turns += 1
        times.append(time.perf_counter() - t0)
        turn_counts.append(turns)
    _report('headless (execute_turn_headless)', battles, times, turn_counts)


def _report(label: str, seeds: list[int], times: list[float], turns: list[int]) -> None:
    total_t = sum(times)
    total_turns = sum(turns)
    print(f'\n=== {label} ===')
    print(f'  场数: {len(seeds)}  总耗时: {total_t:.2f}s  总回合: {total_turns}')
    print(f'  单场均值: {statistics.mean(times):.3f}s  中位: {statistics.median(times):.3f}s  '
          f'最大: {max(times):.3f}s')
    print(f'  吞吐: {total_turns / total_t:.2f} 回合/秒  '
          f'({total_t / max(total_turns, 1) * 1000:.1f} ms/回合)')


def main() -> None:
    parser = argparse.ArgumentParser(description='整局对局基准')
    parser.add_argument('--battles', type=int, default=20)
    parser.add_argument('--seeds', default='')
    parser.add_argument('--mode', default='both', choices=['full', 'headless', 'both'])
    args = parser.parse_args()

    if args.seeds:
        seeds = parse_seeds(args.seeds)
    else:
        random.seed(20260917)
        seeds = random.sample(range(1, 100000), args.battles)

    if args.mode in ('full', 'both'):
        bench_full(seeds)
    if args.mode in ('headless', 'both'):
        bench_headless(seeds)


if __name__ == '__main__':
    main()
