"""Auditable paired-match scores; no Torch dependency.

The sampling unit is a roster pair, not an individual game. The reported
interval is a conservative 95% Hoeffding bound on independent pair means;
it remains nondegenerate even for all-draw or all-win small samples.
"""
from __future__ import annotations

import math


class PairedEvaluation:
    def __init__(self, requested_games: int):
        if requested_games < 0:
            raise ValueError("requested_games must be nonnegative")
        self.requested_games = requested_games
        self.records: dict[int, dict] = {}

    def add(self, game_index: int, score: float | None, **metadata) -> None:
        if not 0 <= game_index < self.requested_games:
            raise ValueError("game_index outside requested suite")
        if game_index in self.records:
            raise ValueError("duplicate game_index")
        if score is not None and score not in (0.0, 0.5, 1.0):
            raise ValueError("score must be 0, 0.5, 1, or None")
        timed_out = metadata.get("end") == "wall" or metadata.get("end_reason") == "timeout"
        self.records[game_index] = {
            **metadata, "game_index": game_index,
            "score": None if timed_out else score, "timed_out": timed_out,
        }

    def summary(self, *, stopped_early: bool = False, **configuration) -> dict:
        eligible: list[dict] = []
        pairs = []
        for i in range(0, self.requested_games - 1, 2):
            a, b = self.records.get(i), self.records.get(i + 1)
            if a is not None and b is not None and a["score"] is not None and b["score"] is not None:
                eligible.extend((a, b))
                pairs.append((a["score"] + b["score"]) / 2)
        score = sum(pairs) / len(pairs) if pairs else None
        half = math.sqrt(math.log(40) / (2 * len(pairs))) if pairs else None
        ci = [max(0.0, score - half), min(1.0, score + half)] if pairs and not stopped_early else None
        records = [self.records[i] for i in sorted(self.records)]
        timeouts = sum(r["timed_out"] for r in records)
        return {
            **configuration,
            "requested_games": self.requested_games,
            "completed_games": len(records),
            "valid_games": sum(r["score"] is not None for r in records),
            "scored_games": len(eligible), "complete_pairs": len(pairs),
            "excluded_games": len(records) - len(eligible),
            "missing_games": self.requested_games - len(records),
            "timeout_games": timeouts,
            "missing_game_indices": [i for i in range(self.requested_games) if i not in self.records],
            "timeout_game_indices": [r["game_index"] for r in records if r["timed_out"]],
            "scored_game_indices": [r["game_index"] for r in eligible],
            "score": score, "ci95": ci,
            "ci_method": ("not reported: adaptive gate stopping" if stopped_early else
                          "Hoeffding bound on independent roster-pair means (conservative)"),
            "wins": sum(r["score"] == 1.0 for r in eligible),
            "draws": sum(r["score"] == 0.5 for r in eligible),
            "losses": sum(r["score"] == 0.0 for r in eligible),
            "pair_scores": pairs, "game_results": records,
            "stopped_early": stopped_early,
            "complete": len(eligible) == self.requested_games and self.requested_games > 0,
        }


def evaluation_return(result: dict, return_details: bool = False) -> float | dict:
    """Legacy gate callers must never promote an incomplete/timeout suite.

    A mathematically settled early-stop gate is safe, but its partial score is
    descriptive only; callers requesting details can see the explicit status.
    """
    if return_details:
        return result
    if result["score"] is None:
        return float("nan")
    if not result["complete"] and not (result["stopped_early"] and not result["timeout_games"]):
        return float("nan")
    return result["score"]
