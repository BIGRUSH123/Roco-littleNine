"""Evaluation protocol: pairs, failed games, uncertainty and exact settings."""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engine.ai.core.eval_statistics import PairedEvaluation, evaluation_return


def test_pair_counts_out_of_order_timeout_and_odd_tail():
    result = PairedEvaluation(7)
    result.add(3, 0.0)
    result.add(0, 1.0)
    result.add(2, 1.0, end="wall")
    result.add(1, 0.5)
    result.add(6, 1.0)
    summary = result.summary()
    assert summary["requested_games"] == 7
    assert summary["completed_games"] == 5
    assert summary["valid_games"] == 4
    assert summary["scored_games"] == 2
    assert summary["complete_pairs"] == 1
    assert summary["timeout_games"] == 1
    assert summary["excluded_games"] == 3
    assert summary["missing_games"] == 2
    assert summary["score"] == 0.75
    assert (summary["wins"], summary["draws"], summary["losses"]) == (1, 1, 0)
    assert summary["pair_scores"] == [0.75]
    assert math.isnan(evaluation_return(summary))
    assert evaluation_return(summary, True) is summary


def test_empty_result_is_missing_not_zero_or_half():
    summary = PairedEvaluation(0).summary()
    assert summary["score"] is None
    assert summary["ci95"] is None
    assert math.isnan(evaluation_return(summary))


def test_bounds_use_pair_count_and_do_not_collapse_on_identical_scores():
    result = PairedEvaluation(4)
    for game_index in range(4):
        result.add(game_index, 0.5)
    summary = result.summary()
    assert summary["ci95"][0] < 0.5 < summary["ci95"][1]
    assert summary["complete"]
    assert evaluation_return(summary) == 0.5
    assert summary["complete_pairs"] == 2
    assert "pair" in summary["ci_method"]


def test_duplicate_and_invalid_scores_rejected():
    result = PairedEvaluation(2)
    result.add(0, 0.5)
    with pytest.raises(ValueError, match="duplicate"):
        result.add(0, 0.5)
    with pytest.raises(ValueError, match="score"):
        result.add(1, float("nan"))
    with pytest.raises(ValueError, match="outside"):
        result.add(2, 1)


def test_early_gate_has_no_fixed_sample_confidence_claim():
    result = PairedEvaluation(10)
    result.add(0, 1.0)
    result.add(1, 1.0)
    summary = result.summary(stopped_early=True)
    assert summary["ci95"] is None
    assert evaluation_return(summary) == 1.0


def test_serial_evaluation_preserves_settings_and_excludes_timeout(monkeypatch):
    from backend.engine.ai import train

    monkeypatch.setattr(train, "TorchEvaluator", lambda model, device: model)
    monkeypatch.setattr(train, "_paired_eval_tasks", lambda *args, **kw: [(i, (i // 2,)) for i in range(4)])
    seen = []

    def fake_game(*args, **kwargs):
        seen.append(kwargs)
        kwargs["stats_out"].update(end="wall" if args[4] == 2 else "finished")
        return None if args[4] == 2 else 1.0

    monkeypatch.setattr(train, "_play_one_eval_game", fake_game)
    model = SimpleNamespace(eval=lambda: None)
    detail = train.evaluate(model, model, None, {}, 4, 7, "cpu", verbose=False,
                            roster_seed=11, game_seed=22, candidate_leaf_weight=0.0,
                            best_leaf_weight=0.5, return_details=True)
    assert detail["score"] == 1.0
    assert detail["scored_games"] == 2
    assert detail["completed_games"] == 4
    assert detail["timeout_games"] == 1
    assert detail["roster_seed"] == 11 and detail["game_seed"] == 22
    assert seen[0]["best_leaf_weight"] == 0.5
    assert seen[0]["candidate_leaf_weight"] == 0.0
    assert math.isnan(train.evaluate(model, model, None, {}, 4, 7, "cpu", verbose=False))


def test_checkpoint_evaluation_reuses_rosters_and_reports_actual_pairs(monkeypatch):
    from backend.engine.ai import evaluate_checkpoints as module

    monkeypatch.setattr(module, "SimFactory", lambda: None)
    monkeypatch.setattr(module, "_load_sprite_skills", dict)
    monkeypatch.setattr(module, "_load_model", lambda *args: None)
    monkeypatch.setattr(module, "_paired_eval_tasks", lambda *args, **kw: [(0, "same"), (1, "same"), (2, "other"), (3, "other")])
    seen = []

    def game(**kwargs):
        seen.append(kwargs)
        index = kwargs["game_index"]
        return {"score": None if index == 2 else 0.5, "seed": kwargs["seed"],
                "candidate_side": "AB"[index % 2], "turns": 1,
                "end_reason": "timeout" if index == 2 else "decisive_a"}

    monkeypatch.setattr(module, "_play_model_vs_rule", game)
    result = module.evaluate_checkpoint(checkpoint=Path("candidate"), reference=None,
        opponent="rule", seeds=[10, 11, 12, 13], sims=7, max_turns=4,
        draw_margin=0.15, device="cpu", game_timeout_s=1, roster_seed=99)
    assert seen[0]["matchup"] == seen[1]["matchup"] == "same"
    assert result["games"] == result["scored_games"] == 2
    assert result["timeout_games"] == 1
    assert result["score"] == 0.5
    assert result["roster_seed"] == 99


def test_explicit_game_seed_reproducible_for_python_numpy_torch():
    import random

    import numpy as np
    import torch

    from backend.engine.ai.train import _seed_eval_game

    def values():
        return random.random(), float(np.random.random()), float(torch.rand(()))

    _seed_eval_game(4, 91)
    expected = values()
    _seed_eval_game(4, 91)
    assert expected == values()
    _seed_eval_game(4, 92)
    assert expected != values()


def test_ab_cli_defaults_and_explicit_seeds(monkeypatch, tmp_path):
    import sys

    from backend.engine.ai import determinism, train
    from backend.engine.ai.core.model import ModularBattleNet
    from native.tools import ab_net_vs_net as cli

    checkpoint = tmp_path / "m0.pt"
    checkpoint.write_bytes(b"frozen")
    monkeypatch.setattr(determinism, "ensure_hash_seed", lambda: None)
    monkeypatch.setattr(ModularBattleNet, "load", lambda *args, **kwargs: object())
    monkeypatch.setattr(train, "_load_sprite_skills", dict)
    calls = []

    def evaluate(*args, **kwargs):
        calls.append(kwargs)
        result = PairedEvaluation(2)
        result.add(0, 1.0)
        result.add(1, 0.0)
        return result.summary()

    monkeypatch.setattr(train, "evaluate_parallel", evaluate)
    monkeypatch.setattr(sys, "argv", ["ab_net_vs_net", "--a", str(checkpoint), "--b", str(checkpoint),
                                    "--games", "2", "--seed", "31", "--game-seed", "41"])
    cli.main()
    assert calls[0]["candidate_leaf_weight"] == calls[0]["best_leaf_weight"] == 0.0
    assert calls[0]["roster_seed"] == 31
    assert calls[0]["game_seed"] == 41
    assert calls[0]["return_details"] is True
    assert checkpoint.read_bytes() == b"frozen"


def test_single_game_wall_timeout_returns_none(monkeypatch):
    from backend.engine.ai import train

    battle = SimpleNamespace(is_finished=False, player_a=object(), player_b=object())
    battle.execute_turn = lambda *args: None
    monkeypatch.setattr(train, "_build_eval_battle", lambda *args: battle)
    monkeypatch.setattr(train, "NetworkPolicyAgent", lambda **kwargs: object())
    monkeypatch.setattr(train, "MCTSAgent", lambda *args, **kwargs: object())
    clock = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(train.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(train, "battle_outcome_a", lambda *args, **kwargs: pytest.fail("timeout cannot be scored"))
    stats = {}
    score = train._play_one_eval_game(None, {}, None, None, 0, 1, 10,
                                     matchup=(), game_timeout_s=1.0, stats_out=stats)
    assert score is None
    assert stats["end"] == "wall"
