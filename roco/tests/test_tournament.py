"""roco/tests/test_tournament.py — TournamentRunner tests."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

import json
import tempfile

import pytest

from roco.ai.agent import RandomAgent
from roco.ai.observation import ActionKind
from roco.tournament import (
    MatchResult,
    TournamentResult,
    TournamentRunner,
    load_agent,
)

# ── agent stubs for testing ──


class AlwaysGather:
    """Always picks gather."""
    name = "GatherBot"

    def select_action(self, state, legal_actions):
        for a in legal_actions:
            if a.kind == ActionKind.GATHER:
                return a
        return legal_actions[0]


class AlwaysSkill0:
    """Always picks first skill action."""
    name = "SkillBot"

    def select_action(self, state, legal_actions):
        for a in legal_actions:
            if a.kind == ActionKind.SKILL:
                return a
        return legal_actions[0]


class CrashBot:
    """Crashes on first call, then works."""
    name = "CrashBot"
    _crashed = False

    def select_action(self, state, legal_actions):
        if not self._crashed:
            self._crashed = True
            raise RuntimeError("simulated crash")
        for a in legal_actions:
            if a.kind == ActionKind.GATHER:
                return a
        return legal_actions[0]


# ── TournamentResult ──


def test_result_matrix():
    r = TournamentResult(agents=["A", "B", "C"], rounds=1)
    r.results = [
        MatchResult("A", "B", "A", 10),
        MatchResult("A", "C", "C", 12),
        MatchResult("B", "A", "B", 8),
        MatchResult("B", "C", "draw", 15),
        MatchResult("C", "A", "C", 11),
        MatchResult("C", "B", "C", 9),
    ]
    m = r.matrix
    assert m[("A", "B")] == "W"  # A (home) beat B
    assert m[("A", "C")] == "L"  # C (away) beat A
    assert m[("B", "A")] == "W"  # B (home) beat A
    assert m[("B", "C")] == "D"  # draw
    assert m[("C", "A")] == "W"  # C (home) beat A
    assert m[("C", "B")] == "W"  # C (home) beat B


def test_result_scores():
    r = TournamentResult(agents=["A", "B", "C"], rounds=1)
    r.results = [
        MatchResult("A", "B", "A", 5),
        MatchResult("B", "A", "B", 5),
        MatchResult("C", "A", "C", 5),
        MatchResult("C", "B", "C", 5),
    ]
    scores = r.scores
    assert scores["A"] == 1
    assert scores["B"] == 1
    assert scores["C"] == 2


def test_result_rankings():
    r = TournamentResult(agents=["A", "B", "C"], rounds=1)
    r.results = [
        MatchResult("A", "B", "A", 5),
        MatchResult("B", "A", "B", 5),
        MatchResult("C", "A", "C", 5),
        MatchResult("C", "B", "C", 5),
    ]
    rankings = r.rankings
    assert rankings[0][0] == "C"
    assert rankings[0][1] == 2


def test_format_matrix():
    r = TournamentResult(agents=["Alice", "Bob"], rounds=1)
    r.results = [
        MatchResult("Alice", "Bob", "Alice", 5),
        MatchResult("Bob", "Alice", "Alice", 7),
    ]
    text = r.format_matrix()
    assert "Alice" in text
    assert "Bob" in text
    assert "W" in text
    assert "L" in text


# ── TournamentRunner ──


def test_runner_two_agents():
    agents = [AlwaysGather(), AlwaysSkill0()]
    runner = TournamentRunner(agents, rounds=1)
    result = runner.run()
    assert result.completed == 2
    assert len(result.results) == 2
    assert len(result.matrix) == 2


def test_runner_rounds():
    agents = [AlwaysGather(), AlwaysSkill0()]
    runner = TournamentRunner(agents, rounds=2)
    result = runner.run()
    # 2 agents * (2-1) * 2 rounds = 4 matches
    assert result.completed == 4
    assert len(result.results) == 4


def test_runner_error_isolation():
    agents = [CrashBot(), AlwaysGather()]
    runner = TournamentRunner(agents, rounds=1)
    result = runner.run()
    # CrashBot crashes first game (home), wins second (away, already crashed once)
    assert result.completed == 2
    assert len(result.results) == 2


def test_runner_checkpoint_roundtrip():
    agents = [AlwaysGather(), AlwaysSkill0()]
    runner = TournamentRunner(agents, rounds=1)

    cp_path = str(Path(tempfile.gettempdir()) / "test_tournament_cp.json")

    try:
        result = runner.run(checkpoint_path=cp_path)
        assert result.completed == 2

        data = json.loads(Path(cp_path).read_text(encoding="utf-8"))
        assert data["completed"] == 2
        assert len(data["results"]) == 2

        # Resume
        agents2 = [AlwaysGather(), AlwaysSkill0()]
        runner2 = TournamentRunner.from_checkpoint(cp_path, agents2)
        assert runner2.result.completed == 2
        assert len(runner2.result.results) == 2
    finally:
        Path(cp_path).unlink(missing_ok=True)


def test_runner_verbose_off():
    agents = [AlwaysGather(), AlwaysSkill0()]
    runner = TournamentRunner(agents, rounds=1, verbose=False)
    result = runner.run()
    assert result.completed == 2


def test_runner_random_vs_self():
    """RandomAgent vs itself — should produce results with no crashes."""
    agents = [RandomAgent(), RandomAgent()]
    runner = TournamentRunner(agents, rounds=1, verbose=False)
    result = runner.run()
    assert result.completed == 2
    assert len(result.results) == 2
    for r in result.results:
        assert r.error == ""


def test_runner_format_matrix_empty():
    r = TournamentResult(agents=[], rounds=1)
    assert "(no agents)" in r.format_matrix()


# ── load_agent ──


def test_load_agent_from_roco_ai():
    agent = load_agent("roco.ai.agent")
    assert hasattr(agent, "name")
    assert callable(agent.select_action)


def test_load_agent_invalid():
    with pytest.raises((ImportError, ValueError)):
        load_agent("nonexistent_module_xyz")
