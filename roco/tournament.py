"""roco.tournament — round-robin tournament with checkpoint/resume.

Usage:
    python -m roco.tournament path/to/agent_a.py path/to/agent_b.py --rounds 50
    python -m roco.tournament agent_a agent_b --rounds 100 --checkpoint state.json
"""

from __future__ import annotations

import importlib.util
import json
import signal
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import isabstract
from pathlib import Path

from roco.ai.agent import BattleAgent

# ── result types ──


@dataclass
class MatchResult:
    home: str
    away: str
    winner: str  # agent name or "draw"
    turns: int
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class TournamentResult:
    agents: list[str]
    rounds: int
    results: list[MatchResult] = field(default_factory=list)
    agent_errors: dict[str, int] = field(default_factory=dict)
    completed: int = 0
    total: int = 0

    @property
    def matrix(self) -> dict[tuple[str, str], str]:
        m: dict[tuple[str, str], str] = {}
        for r in self.results:
            key = (r.home, r.away)
            if key in m:
                continue
            if r.winner == r.home:
                m[key] = "W"
            elif r.winner == r.away:
                m[key] = "L"
            else:
                m[key] = "D"
        return m

    @property
    def scores(self) -> dict[str, int]:
        s: dict[str, int] = {a: 0 for a in self.agents}
        for r in self.results:
            if r.winner == r.home:
                s[r.home] += 1
            elif r.winner == r.away:
                s[r.away] += 1
        return s

    @property
    def rankings(self) -> list[tuple[str, int]]:
        return sorted(self.scores.items(), key=lambda x: -x[1])

    def format_matrix(self) -> str:
        """ASCII wins matrix."""
        if not self.agents:
            return "(no agents)"
        n = len(self.agents)
        w = max(len(a) for a in self.agents)
        lines = []
        header = " " * (w + 2) + "".join(f"{a:^{w+2}}" for a in self.agents)
        lines.append(header)
        lines.append(" " * (w + 2) + "".join("-" * (w + 2) for _ in range(n)))
        for i, home in enumerate(self.agents):
            row = f"{home:>{w}} |"
            for j, away in enumerate(self.agents):
                if i == j:
                    row += f"{'·':^{w+2}}"
                else:
                    cell = self.matrix.get((home, away), "?")
                    row += f"{cell:^{w+2}}"
            lines.append(row)
        return "\n".join(lines)

    @property
    def games_played(self) -> dict[str, int]:
        g: dict[str, int] = {a: 0 for a in self.agents}
        for r in self.results:
            g[r.home] = g.get(r.home, 0) + 1
            g[r.away] = g.get(r.away, 0) + 1
        return g

    def format_rankings(self) -> str:
        lines = ["", "Rank  Agent                      W  L  Pct"]
        lines.append("-" * 48)
        for i, (name, wins) in enumerate(self.rankings):
            total = self.games_played.get(name, 0)
            losses = total - wins
            pct = wins / max(total, 1)
            lines.append(f"{i+1:>4}  {name:<25} {wins:>3} {losses:>3} {pct:.3f}")
        return "\n".join(lines)


# ── agent loading ──


def load_agent(source: str) -> BattleAgent:
    """Load a BattleAgent from a .py file or registered module name.

    For .py files: import the file as a module and find the first
    object implementing the BattleAgent protocol (has 'name' and
    'select_action' callable).

    For module names: import by dotted path, same auto-discovery.
    """
    path = Path(source)
    if path.suffix == ".py" and path.exists():
        return _load_from_file(path)
    return _load_from_module(source)


def _load_from_file(path: Path) -> BattleAgent:
    spec = importlib.util.spec_from_file_location(
        path.stem, str(path.resolve())
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path} — the file is not a valid Python module. Check that the path exists and is a .py file.")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return _find_agent(mod, str(path))


def _load_from_module(name: str) -> BattleAgent:
    # try dotted path first, then relative to roco.ai
    for module_name in (name, f"roco.ai.{name}", f"roco.ai.agents.{name}"):
        try:
            mod = importlib.import_module(module_name)
            return _find_agent(mod, module_name)
        except ImportError:
            continue
    raise ImportError(f"Cannot import agent from {name!r} — module not found. Check that the path or module name is correct, and that the module exports an 'agent' instance.")


def _find_agent(mod, source_name: str) -> BattleAgent:
    # Look for explicit attribute
    for attr in ("agent", "Agent", "AGENT"):
        obj = getattr(mod, attr, None)
        result = _try_agent(obj)
        if result is not None:
            return result

    # Auto-discover: first object with name + select_action
    for name in dir(mod):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        result = _try_agent(obj)
        if result is not None:
            return result

    raise ValueError(f"No BattleAgent found in {source_name!r} — the module must expose an object with 'name' (str) and 'select_action' (callable). Export it as 'agent = YourAgent()' at module level.")


def _try_agent(obj) -> BattleAgent | None:
    """Accept an instance or a class, returning a usable agent or None."""
    if obj is None:
        return None
    if _is_battle_agent(obj):
        return obj
    if isinstance(obj, type) and not isabstract(obj):
        try:
            instance = obj()
            if _is_battle_agent(instance):
                return instance
        except Exception:
            pass
    return None


def _is_battle_agent(obj) -> bool:
    if obj is None:
        return False
    if isinstance(obj, type):
        return False
    try:
        name_val = getattr(obj, "name", None)
        if not isinstance(name_val, str):
            return False
        action_fn = getattr(obj, "select_action", None)
        return callable(action_fn)
    except Exception:
        return False


# ── default sprite lineups ──

_DEFAULT_TEAM_SPECS = [
    {"name": "草衣虫", "skills": ["猛烈撞击", "甩水", "防御"]},
]

# ── runner ──


class TournamentRunner:
    """Round-robin tournament runner with checkpoint/resume."""

    def __init__(
        self,
        agents: list[BattleAgent],
        rounds: int = 1,
        team_specs: list[dict] | None = None,
        verbose: bool = True,
        on_match_result: Callable[[MatchResult], None] | None = None,
    ):
        if len(agents) < 2:
            raise ValueError("Tournament requires at least 2 agents. Provide at least 2 agent files or module paths.")
        self.agents = agents
        self.rounds = rounds
        self.team_specs = team_specs or _DEFAULT_TEAM_SPECS
        self.verbose = verbose
        self._on_match_result = on_match_result

        self._pairs: list[tuple[int, int]] = []
        for _ in range(rounds):
            for i in range(len(agents)):
                for j in range(len(agents)):
                    if i != j:
                        self._pairs.append((i, j))

        self.result = TournamentResult(
            agents=[a.name for a in agents],
            rounds=rounds,
            total=len(self._pairs),
        )
        self._consecutive_errors: dict[str, int] = {a.name: 0 for a in agents}
        self._disqualified: set[str] = set()
        self._checkpoint_path: str | None = None
        self._interrupted = False

    # ── checkpoint ──

    def to_checkpoint(self) -> dict:
        return {
            "completed": self.result.completed,
            "agent_names": self.result.agents,
            "rounds": self.rounds,
            "results": [
                {
                    "home": r.home,
                    "away": r.away,
                    "winner": r.winner,
                    "turns": r.turns,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for r in self.result.results
            ],
            "agent_errors": self.result.agent_errors,
            "consecutive_errors": self._consecutive_errors,
            "disqualified": list(self._disqualified),
        }

    def save_checkpoint(self, path: str | None = None) -> None:
        p = path or self._checkpoint_path
        if not p:
            return
        data = self.to_checkpoint()
        Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def from_checkpoint(cls, path: str, agents: list[BattleAgent]) -> TournamentRunner:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        runner = cls(agents, rounds=data["rounds"])
        runner._checkpoint_path = path
        runner._disqualified = set(data.get("disqualified", []))
        runner._consecutive_errors = data.get("consecutive_errors", {})
        runner.result.agent_errors = data.get("agent_errors", {})

        for r in data.get("results", []):
            runner.result.results.append(MatchResult(
                home=r["home"], away=r["away"], winner=r["winner"],
                turns=r["turns"], error=r.get("error", ""),
                duration_ms=r.get("duration_ms", 0.0),
            ))
        runner.result.completed = data["completed"]
        return runner

    # ── run ──

    def run(self, checkpoint_path: str | None = None) -> TournamentResult:
        self._checkpoint_path = checkpoint_path

        signal.signal(signal.SIGINT, self._on_sigint)

        if self.verbose:
            n = len(self.agents)
            print(f"Tournament: {n} agents, {self.rounds} round(s), "
                  f"{len(self._pairs)} matches")
            print(f"Agents: {', '.join(a.name for a in self.agents)}")
            print()

        start_idx = self.result.completed

        for idx in range(start_idx, len(self._pairs)):
            i, j = self._pairs[idx]
            home_agent = self.agents[i]
            away_agent = self.agents[j]

            if home_agent.name in self._disqualified:
                self._record_forfeit(i, j)
                continue
            if away_agent.name in self._disqualified:
                self._record_forfeit(i, j, winner=home_agent.name)
                continue

            result = self._play_match(home_agent, away_agent, idx)
            self.result.results.append(result)
            self.result.completed += 1

            if self._on_match_result:
                self._on_match_result(result)

            if result.error:
                self.result.agent_errors[result.error] = \
                    self.result.agent_errors.get(result.error, 0) + 1

            if self.verbose:
                self._print_progress(idx, result)

            if checkpoint_path and (idx + 1) % 10 == 0:
                self.save_checkpoint(checkpoint_path)

            if self._interrupted:
                if checkpoint_path:
                    self.save_checkpoint(checkpoint_path)
                    print(f"\nCheckpoint saved to {checkpoint_path}")
                break

        if self.verbose and not self._interrupted:
            print(self.result.format_rankings())

        if checkpoint_path and not self._interrupted:
            self.save_checkpoint(checkpoint_path)

        signal.signal(signal.SIGINT, signal.SIG_DFL)
        return self.result

    def _play_match(
        self, home: BattleAgent, away: BattleAgent, idx: int
    ) -> MatchResult:
        from backend.sim.battle import Battle
        from backend.sim.factory import SimFactory
        from roco.bridge import adapt_agent

        t0 = time.perf_counter()
        try:
            factory = SimFactory()
            p1 = factory.build_player("A", self.team_specs)
            p2 = factory.build_player("B", self.team_specs)
            b = Battle(p1, p2, verbose=False)

            adapted_home = adapt_agent(home, "A")
            adapted_away = adapt_agent(away, "B")

            winner = b.run(adapted_home, adapted_away)
            result_winner = (
                home.name if winner == "A"
                else away.name if winner == "B"
                else "draw"
            )
            self._consecutive_errors[home.name] = 0
            self._consecutive_errors[away.name] = 0
            return MatchResult(
                home=home.name, away=away.name,
                winner=result_winner, turns=b.turn,
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception:
            err_agent = home.name
            self._consecutive_errors[err_agent] = self._consecutive_errors.get(err_agent, 0) + 1
            if self._consecutive_errors[err_agent] >= 3:
                self._disqualified.add(err_agent)
                print(f"\n  !! {err_agent} crashed 3 times — disqualified", file=sys.stderr)
            return MatchResult(
                home=home.name, away=away.name,
                winner=away.name, turns=0,
                error=traceback.format_exc(),
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

    def _record_forfeit(self, home_idx: int, away_idx: int, winner: str | None = None):
        home_name = self.agents[home_idx].name
        away_name = self.agents[away_idx].name
        if winner is None:
            winner = "draw"
        self.result.results.append(MatchResult(
            home=home_name, away=away_name, winner=winner, turns=0,
        ))
        self.result.completed += 1

    def _print_progress(self, idx: int, result: MatchResult):
        total = self.result.total
        pct = (idx + 1) / max(total, 1) * 100
        bar_w = 20
        filled = int(bar_w * (idx + 1) / max(total, 1))
        bar = "#" * filled + "-" * (bar_w - filled)
        err = f" !! {result.error[:40]}" if result.error else ""
        print(f"\r  [{bar}] {idx+1}/{total} ({pct:.0f}%)  "
              f"{result.home} vs {result.away} → {result.winner}  "
              f"({result.turns}t {result.duration_ms:.0f}ms){err}", end="")
        if idx + 1 >= total or result.error:
            print()

    def _on_sigint(self, signum, frame):
        self._interrupted = True
        print("\n\nInterrupted — saving checkpoint...", file=sys.stderr)


# ── CLI ──


def main(argv: list[str] | None = None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="roco.tournament",
        description="Round-robin BattleAgent tournament",
    )
    parser.add_argument(
        "agents", nargs="+",
        help="Agent .py files or module names",
    )
    parser.add_argument(
        "--rounds", type=int, default=1,
        help="Number of rounds per matchup (default: 1)",
    )
    parser.add_argument(
        "--checkpoint", type=str, default=None,
        help="Path to save/load checkpoint JSON",
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="Resume from checkpoint (loads agents from checkpoint metadata)",
    )
    args = parser.parse_args(argv)

    if args.resume:
        print(f"Resuming from {args.resume} ...")
        data = json.loads(Path(args.resume).read_text(encoding="utf-8"))
        agent_names = data["agent_names"]
        print(f"  Agents from checkpoint: {', '.join(agent_names)}")
        # When resuming, re-import agents by name from supplied args
        agents = [load_agent(a) for a in args.agents] if args.agents else []
        if not agents:
            print("Error: --resume requires agent paths to re-import. Provide the same agent files/modules used in the original run.", file=sys.stderr)
            sys.exit(1)
        runner = TournamentRunner.from_checkpoint(args.resume, agents)
        runner.verbose = True
    else:
        agents = []
        for src in args.agents:
            try:
                agent = load_agent(src)
                agents.append(agent)
                print(f"  Loaded: {agent.name} ← {src}")
            except Exception as exc:
                print(f"  SKIP {src}: {exc}", file=sys.stderr)
        if len(agents) < 2:
            print("Error: Tournament requires at least 2 agents. Make sure to provide at least 2 valid agent files or module paths.", file=sys.stderr)
            sys.exit(1)
        runner = TournamentRunner(agents, rounds=args.rounds)

    result = runner.run(checkpoint_path=args.checkpoint)
    print()
    print(result.format_matrix())

    # Write checkpoint at end
    if args.checkpoint:
        runner.save_checkpoint(args.checkpoint)
        print(f"\nFinal checkpoint: {args.checkpoint}")


if __name__ == "__main__":
    main()
