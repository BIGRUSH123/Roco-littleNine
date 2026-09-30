"""Frozen checkpoint head-to-head, with matched search and auditable pairs.

Use --a <candidate> --b <frozen M0> --games 1200 --sims 100 --json-out <path>.
Both leaf weights default to zero. Explicit asymmetric settings are ablations.
More games improve precision at the chosen search budget; confirm rankings at
production search budget rather than assuming rankings survive a budget change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def checkpoint_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    from backend.engine.ai.determinism import ensure_hash_seed
    ensure_hash_seed()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="Candidate checkpoint (read only)")
    ap.add_argument("--b", required=True, help="Frozen reference / M0 checkpoint (read only)")
    ap.add_argument("--games", type=int, default=1200)
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--eval-max-turns", type=int, default=150)
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--seed", type=int, default=None, help="Legacy alias for roster and game seed")
    ap.add_argument("--roster-seed", type=int, default=None)
    ap.add_argument("--game-seed", type=int, default=None)
    ap.add_argument("--leaf-value-weight", type=float, default=0.0, help="Shared leaf weight")
    ap.add_argument("--candidate-leaf-weight", type=float, default=None)
    ap.add_argument("--best-leaf-weight", type=float, default=None)
    ap.add_argument("--stall-timeout-s", type=float, default=1200.0)
    ap.add_argument("--device", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    if args.games <= 0 or args.games % 2:
        ap.error("--games must be a positive even number")
    if args.sims <= 0 or args.workers <= 0 or args.stall_timeout_s <= 0:
        ap.error("--sims, --workers, --stall-timeout-s must be positive")
    if args.json_out and Path(args.json_out).resolve() in {Path(args.a).resolve(), Path(args.b).resolve()}:
        ap.error("--json-out must not overwrite a checkpoint")
    import torch

    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import (
        _EVAL_GAME_SEED,
        _EVAL_ROSTER_SEED,
        _load_sprite_skills,
        evaluate_parallel,
    )
    from backend.sim.factory import SimFactory

    dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    roster_seed = args.roster_seed if args.roster_seed is not None else (args.seed if args.seed is not None else _EVAL_ROSTER_SEED)
    game_seed = args.game_seed if args.game_seed is not None else (args.seed if args.seed is not None else _EVAL_GAME_SEED)
    w_a = args.leaf_value_weight if args.candidate_leaf_weight is None else args.candidate_leaf_weight
    w_b = args.leaf_value_weight if args.best_leaf_weight is None else args.best_leaf_weight
    if not 0 <= w_a <= 1 or not 0 <= w_b <= 1:
        ap.error("Leaf weights must lie in [0, 1]")
    hashes = {"a_sha256": checkpoint_sha256(args.a), "b_sha256": checkpoint_sha256(args.b)}
    net_a = ModularBattleNet.load(args.a, device=dev)
    net_b = ModularBattleNet.load(args.b, device=dev)
    factory = SimFactory()
    skills = _load_sprite_skills()
    t0 = time.time()
    result = evaluate_parallel(
        net_a, net_b, factory, skills,
        n_games=args.games, num_workers=args.workers, device=dev,
        inference_batch_size=256, inference_timeout_ms=5.0,
        num_simulations=args.sims, max_turns=args.eval_max_turns,
        draw_margin=0.15, progress_every=max(1, args.games // 12),
        stall_timeout_s=args.stall_timeout_s, leaf_batch_size=16,
        candidate_leaf_weight=w_a, best_leaf_weight=w_b,
        roster_seed=roster_seed, game_seed=game_seed, return_details=True,
    )
    result.update(a=args.a, b=args.b, **hashes, sims=args.sims,
                  minutes=round((time.time() - t0) / 60, 1),
                  python_hash_seed=os.environ.get("PYTHONHASHSEED"))
    print(f"[A/B] score={result['score']} ci95={result['ci95']} "
          f"W/D/L={result['wins']}/{result['draws']}/{result['losses']} "
          f"scored={result['scored_games']}/{result['requested_games']} "
          f"pairs={result['complete_pairs']} timeouts={result['timeout_games']}", flush=True)
    print(f"[A/B] {result['ci_method']}", flush=True)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if not result["complete"]:
        raise SystemExit("Incomplete evaluation: inspect counts/timeouts; do not treat as a completed gate")


if __name__ == "__main__":
    main()
