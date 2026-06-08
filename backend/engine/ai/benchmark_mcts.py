"""Small repeatable benchmark for the AI MCTS hot path.

Run with:
    python -m backend.engine.ai.benchmark_mcts --simulations 16 --mcts-repeats 3
"""

from __future__ import annotations

import argparse
import json
import random
import time
from typing import Any

import numpy as np
import torch

from backend.engine.ai.core.encoder import encode_battle_state
from backend.engine.ai.core.evaluator import TorchEvaluator
from backend.engine.ai.core.mcts import get_valid_actions, mcts_search
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.train import _load_sprite_skills
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory


def _fixed_battle(factory: SimFactory):
    sprite_skills = _load_sprite_skills()
    names = sorted(sprite_skills.keys())
    if len(names) < 4:
        raise RuntimeError("Need at least four sprites with skills for the benchmark")

    a, b, c, d = names[:4]
    team_a = [
        {"name": a, "skills": sprite_skills[a][:4]},
        {"name": c, "skills": sprite_skills[c][:4]},
    ]
    team_b = [
        {"name": b, "skills": sprite_skills[b][:4]},
        {"name": d, "skills": sprite_skills[d][:4]},
    ]
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    return factory.build_battle(p1, p2)


def _rate(seconds: float, units: int) -> float:
    if seconds <= 0:
        return float("inf")
    return units / seconds


def run_benchmark(
    *,
    encode_iters: int = 100,
    eval_iters: int = 100,
    mcts_repeats: int = 3,
    simulations: int = 16,
    device: str = "cpu",
    seed: int = 1,
    torch_threads: int | None = None,
    leaf_batch_size: int = 1,
) -> dict[str, Any]:
    """Benchmark encode, model evaluation, and MCTS on one deterministic position."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch_threads is not None:
        torch.set_num_threads(torch_threads)

    factory = SimFactory()
    battle = _fixed_battle(factory)
    model = ModularBattleNet().to(device)
    model.eval()
    evaluator = TorchEvaluator(model, device=device)

    # Warm the model and data caches before timing.
    state = encode_battle_state(battle)
    _, mask = get_valid_actions(battle.player_a, battle)
    evaluator.evaluate(state, mask)

    t0 = time.perf_counter()
    for _ in range(encode_iters):
        state = encode_battle_state(battle)
    encode_seconds = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(eval_iters):
        evaluator.evaluate(state, mask)
    eval_seconds = time.perf_counter() - t0

    opponent = RuleAgent("B", battle.player_b)
    t0 = time.perf_counter()
    for _ in range(mcts_repeats):
        mcts_search(
            battle,
            model,
            factory,
            opponent,
            num_simulations=simulations,
            device=device,
            root_noise=0.0,
            max_turns=20,
            evaluator=evaluator,
            leaf_batch_size=leaf_batch_size,
        )
    mcts_seconds = time.perf_counter() - t0
    total_simulations = simulations * mcts_repeats

    return {
        "device": device,
        "seed": seed,
        "leaf_batch_size": leaf_batch_size,
        "encode": {
            "iters": encode_iters,
            "seconds": encode_seconds,
            "iters_per_sec": _rate(encode_seconds, encode_iters),
        },
        "evaluate": {
            "iters": eval_iters,
            "seconds": eval_seconds,
            "iters_per_sec": _rate(eval_seconds, eval_iters),
        },
        "mcts": {
            "repeats": mcts_repeats,
            "simulations": simulations,
            "total_simulations": total_simulations,
            "seconds": mcts_seconds,
            "simulations_per_sec": _rate(mcts_seconds, total_simulations),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark AI MCTS hot-path throughput")
    parser.add_argument("--encode-iters", type=int, default=100)
    parser.add_argument("--eval-iters", type=int, default=100)
    parser.add_argument("--mcts-repeats", type=int, default=3)
    parser.add_argument("--simulations", type=int, default=16)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--torch-threads", type=int, default=None)
    parser.add_argument("--leaf-batch-size", type=int, default=1)
    args = parser.parse_args()

    result = run_benchmark(
        encode_iters=args.encode_iters,
        eval_iters=args.eval_iters,
        mcts_repeats=args.mcts_repeats,
        simulations=args.simulations,
        device=args.device,
        seed=args.seed,
        torch_threads=args.torch_threads,
        leaf_batch_size=args.leaf_batch_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
