"""Fixed-seed evaluation for RL checkpoints.

Examples:
    python -m backend.engine.ai.evaluate_checkpoints --checkpoint-dir checkpoints/formal_v1 --games 20 --sims 32 --device cuda
    python -m backend.engine.ai.evaluate_checkpoints --checkpoints checkpoints/formal_v1/model_rl_iter13.pt checkpoints/formal_v1/model_rl_best.pt --reference checkpoints/formal_v1/model_rl_iter1.pt
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import torch

from backend.engine.ai.battle_log import extract_battle_summary
from backend.engine.ai.core.evaluator import TorchEvaluator
from backend.engine.ai.core.mcts import NetworkPolicyAgent
from backend.engine.ai.core.model import ModularBattleNet
from backend.engine.ai.core.outcome import (
    DEFAULT_DRAW_MARGIN,
    DEFAULT_EVAL_MAX_TURNS,
    battle_outcome_a,
    eval_score_for_candidate,
)
from backend.engine.ai.train import MCTSAgent, _load_sprite_skills, _random_item, _random_teams
from backend.sim.agent import RuleAgent
from backend.sim.factory import SimFactory


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _checkpoint_sort_key(path: Path) -> tuple[int, str]:
    if path.name == "model_rl_best.pt":
        return (10**9, path.name)
    match = re.search(r"iter(\d+)", path.stem)
    if match:
        return (int(match.group(1)), path.name)
    return (10**8, path.name)


def _discover_checkpoints(checkpoint_dir: Path) -> list[Path]:
    paths = sorted(checkpoint_dir.glob("model_rl_iter*.pt"), key=_checkpoint_sort_key)
    best = checkpoint_dir / "model_rl_best.pt"
    if best.exists():
        paths.append(best)
    return paths


def _score_ci(score: float, games: int) -> tuple[float, float]:
    if games <= 0:
        return (0.0, 0.0)
    # Conservative normal approximation. Draws are treated as 0.5 scores.
    se = math.sqrt(max(score * (1.0 - score), 0.0) / games)
    return (max(0.0, score - 1.96 * se), min(1.0, score + 1.96 * se))


def _load_model(path: Path, device: str) -> ModularBattleNet:
    model = ModularBattleNet.load(str(path), device=device)
    model.eval()
    return model


def _build_battle(factory: SimFactory, sprite_skills: dict[str, list[str]], seed: int):
    _set_seed(seed)
    team_a, team_b = _random_teams(factory, sprite_skills)
    p1 = factory.build_player("A", team_a, item=_random_item())
    p2 = factory.build_player("B", team_b, item=_random_item())
    return factory.build_battle(p1, p2)


def _play_model_vs_model(
    *,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    candidate_model: ModularBattleNet,
    reference_model: ModularBattleNet,
    seed: int,
    game_index: int,
    sims: int,
    max_turns: int,
    draw_margin: float,
    device: str,
    game_timeout_s: float,
) -> dict[str, Any]:
    battle = _build_battle(factory, sprite_skills, seed)
    cand_is_a = game_index % 2 == 0
    model_a = candidate_model if cand_is_a else reference_model
    model_b = reference_model if cand_is_a else candidate_model
    eval_a = TorchEvaluator(model_a, device)
    eval_b = TorchEvaluator(model_b, device)
    opp_a = NetworkPolicyAgent(evaluator=eval_b, greedy=True)
    opp_b = NetworkPolicyAgent(evaluator=eval_a, greedy=True)
    agent_a = MCTSAgent(
        "A", battle.player_a, factory, opp_a, sims,
        temperature=0.0, root_noise=0.0, record=False,
        evaluator=eval_a, opp_greedy=True, max_turns=max_turns,
    )
    agent_b = MCTSAgent(
        "B", battle.player_b, factory, opp_b, sims,
        temperature=0.0, root_noise=0.0, record=False,
        evaluator=eval_b, opp_greedy=True, max_turns=max_turns,
    )
    return _finish_game(
        battle=battle,
        agent_a=agent_a,
        agent_b=agent_b,
        seed=seed,
        candidate_is_a=cand_is_a,
        max_turns=max_turns,
        draw_margin=draw_margin,
        game_timeout_s=game_timeout_s,
    )


def _play_model_vs_rule(
    *,
    factory: SimFactory,
    sprite_skills: dict[str, list[str]],
    candidate_model: ModularBattleNet,
    seed: int,
    game_index: int,
    sims: int,
    max_turns: int,
    draw_margin: float,
    device: str,
    game_timeout_s: float,
) -> dict[str, Any]:
    battle = _build_battle(factory, sprite_skills, seed)
    cand_is_a = game_index % 2 == 0
    evaluator = TorchEvaluator(candidate_model, device)
    rule_a = RuleAgent("A", battle.player_a)
    rule_b = RuleAgent("B", battle.player_b)
    if cand_is_a:
        agent_a = MCTSAgent(
            "A", battle.player_a, factory, rule_b, sims,
            temperature=0.0, root_noise=0.0, record=False,
            evaluator=evaluator, opp_greedy=True, max_turns=max_turns,
        )
        agent_b = rule_b
    else:
        agent_a = rule_a
        agent_b = MCTSAgent(
            "B", battle.player_b, factory, rule_a, sims,
            temperature=0.0, root_noise=0.0, record=False,
            evaluator=evaluator, opp_greedy=True, max_turns=max_turns,
        )
    return _finish_game(
        battle=battle,
        agent_a=agent_a,
        agent_b=agent_b,
        seed=seed,
        candidate_is_a=cand_is_a,
        max_turns=max_turns,
        draw_margin=draw_margin,
        game_timeout_s=game_timeout_s,
    )


def _finish_game(
    *,
    battle,
    agent_a,
    agent_b,
    seed: int,
    candidate_is_a: bool,
    max_turns: int,
    draw_margin: float,
    game_timeout_s: float,
) -> dict[str, Any]:
    started = time.monotonic()
    turn = 0
    timed_out = False
    while not battle.is_finished and turn < max_turns:
        battle.execute_turn(agent_a, agent_b)
        turn += 1
        if time.monotonic() - started >= game_timeout_s:
            timed_out = True
            break

    outcome_a, end_reason = battle_outcome_a(
        battle, max_turns, draw_margin=draw_margin,
    )
    if timed_out:
        end_reason = "timeout"
    score = eval_score_for_candidate(outcome_a, candidate_is_a)
    summary = extract_battle_summary(battle, end_reason)
    return {
        "seed": seed,
        "candidate_side": "A" if candidate_is_a else "B",
        "score": score,
        "winner": summary["winner"],
        "end_reason": end_reason,
        "turns": summary["turns"],
        "teams": summary["teams"],
    }


def evaluate_checkpoint(
    *,
    checkpoint: Path,
    reference: Path | None,
    opponent: str,
    seeds: list[int],
    sims: int,
    max_turns: int,
    draw_margin: float,
    device: str,
    game_timeout_s: float,
) -> dict[str, Any]:
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    candidate = _load_model(checkpoint, device)
    reference_model = _load_model(reference, device) if reference is not None else None

    games: list[dict[str, Any]] = []
    for game_index, seed in enumerate(seeds):
        if opponent == "rule":
            result = _play_model_vs_rule(
                factory=factory,
                sprite_skills=sprite_skills,
                candidate_model=candidate,
                seed=seed,
                game_index=game_index,
                sims=sims,
                max_turns=max_turns,
                draw_margin=draw_margin,
                device=device,
                game_timeout_s=game_timeout_s,
            )
        else:
            if reference_model is None:
                raise ValueError("--reference is required when --opponent model")
            result = _play_model_vs_model(
                factory=factory,
                sprite_skills=sprite_skills,
                candidate_model=candidate,
                reference_model=reference_model,
                seed=seed,
                game_index=game_index,
                sims=sims,
                max_turns=max_turns,
                draw_margin=draw_margin,
                device=device,
                game_timeout_s=game_timeout_s,
            )
        games.append(result)

    score = sum(g["score"] for g in games) / len(games) if games else 0.0
    ci_low, ci_high = _score_ci(score, len(games))
    reasons = Counter(g["end_reason"] for g in games)
    sides = Counter(g["candidate_side"] for g in games)
    wins = sum(1 for g in games if g["score"] == 1.0)
    draws = sum(1 for g in games if g["score"] == 0.5)
    losses = sum(1 for g in games if g["score"] == 0.0)
    return {
        "checkpoint": str(checkpoint),
        "reference": str(reference) if reference is not None else None,
        "opponent": opponent,
        "games": len(games),
        "sims": sims,
        "max_turns": max_turns,
        "score": score,
        "ci95": [ci_low, ci_high],
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "avg_turns": (sum(g["turns"] for g in games) / len(games)) if games else 0.0,
        "reason_counts": dict(reasons),
        "side_counts": dict(sides),
        "game_results": games,
    }


def _print_table(results: list[dict[str, Any]]) -> None:
    print()
    print("checkpoint                         score   ci95          W/D/L      avgT  reasons")
    print("-" * 92)
    for result in results:
        name = Path(result["checkpoint"]).name
        ci = result["ci95"]
        reasons = ",".join(f"{k}:{v}" for k, v in sorted(result["reason_counts"].items()))
        record = f"{result['wins']}/{result['draws']}/{result['losses']}"
        print(
            f"{name:<34} "
            f"{result['score']:.3f}   "
            f"[{ci[0]:.3f},{ci[1]:.3f}]  "
            f"{record:>9}  "
            f"{result['avg_turns']:>5.1f}  "
            f"{reasons}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RL checkpoints on fixed seeds")
    parser.add_argument("--checkpoint-dir", type=Path, default=None)
    parser.add_argument("--checkpoints", type=Path, nargs="*", default=None)
    parser.add_argument("--reference", type=Path, default=None)
    parser.add_argument("--opponent", choices=("model", "rule"), default="rule")
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260607)
    parser.add_argument("--sims", type=int, default=32)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_EVAL_MAX_TURNS)
    parser.add_argument("--draw-margin", type=float, default=DEFAULT_DRAW_MARGIN)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--game-timeout-s", type=float, default=120.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.checkpoints:
        checkpoints = list(args.checkpoints)
    elif args.checkpoint_dir:
        checkpoints = _discover_checkpoints(args.checkpoint_dir)
    else:
        raise SystemExit("Provide --checkpoint-dir or --checkpoints")
    if not checkpoints:
        raise SystemExit("No checkpoints found")
    if args.opponent == "model" and args.reference is None:
        raise SystemExit("--reference is required with --opponent model")

    seeds = [args.seed + i for i in range(args.games)]
    started = time.time()
    results = []
    for idx, checkpoint in enumerate(checkpoints, start=1):
        print(f"[{idx}/{len(checkpoints)}] evaluating {checkpoint}", flush=True)
        result = evaluate_checkpoint(
            checkpoint=checkpoint,
            reference=args.reference,
            opponent=args.opponent,
            seeds=seeds,
            sims=args.sims,
            max_turns=args.max_turns,
            draw_margin=args.draw_margin,
            device=args.device,
            game_timeout_s=args.game_timeout_s,
        )
        results.append(result)

    payload = {
        "params": {
            "opponent": args.opponent,
            "reference": str(args.reference) if args.reference is not None else None,
            "games": args.games,
            "seed": args.seed,
            "sims": args.sims,
            "max_turns": args.max_turns,
            "draw_margin": args.draw_margin,
            "device": args.device,
        },
        "elapsed_sec": round(time.time() - started, 3),
        "results": results,
    }
    _print_table(results)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.output}")


if __name__ == "__main__":
    main()
