"""scripts/demo.py — terminal tournament demo (30s viral clip).

Produces a visually compelling tournament run suitable for screen recording.
Multiple agents, progress bar, ASCII win matrix, rankings.

Usage:
    python scripts/demo.py                 # run the full demo
    python scripts/demo.py --quick         # fast mode (fewer rounds, for testing)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))

from roco.ai.observation import Action, ActionKind, BattleObservation
from roco.tournament import TournamentRunner, load_agent


# ── Extra demo agents ──


class HealBot:
    """Prioritizes defense/heal skills over attacking."""

    name = "HealBot"

    def select_action(self, state: BattleObservation, legal_actions: list[Action]) -> Action:
        hp_pct = state.my_sprite.current_hp / max(state.my_sprite.max_hp, 1)
        # Low HP: look for defense skills or switch
        if hp_pct < 0.4:
            for a in legal_actions:
                if a.kind == ActionKind.SWITCH:
                    return a
        # Prefer defense/gather over attacking
        for a in legal_actions:
            if a.kind == ActionKind.GATHER:
                return a
        for a in legal_actions:
            if a.kind == ActionKind.SKILL:
                return a
        return legal_actions[0]


class AggroBot:
    """Always attacks with highest priority."""

    name = "AggroBot"

    def select_action(self, state: BattleObservation, legal_actions: list[Action]) -> Action:
        for a in legal_actions:
            if a.kind == ActionKind.SKILL:
                return a
        for a in legal_actions:
            if a.kind == ActionKind.GATHER:
                return a
        return legal_actions[0]


# ── main ──


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tournament demo")
    parser.add_argument("--quick", action="store_true", help="Fast mode")
    parser.add_argument("--rounds", type=int, default=10, help="Rounds per matchup")
    args = parser.parse_args()

    rounds = 3 if args.quick else args.rounds

    print("═" * 60)
    print("  格斗小九 (Roco) — Battle VM Tournament Demo")
    print("═" * 60)

    # Load agents
    agents = [
        HealBot(),
        AggroBot(),
        load_agent("roco.ai.agent"),  # RandomAgent
    ]

    print(f"\n  Agents: {', '.join(a.name for a in agents)}")
    print(f"  Rounds per matchup: {rounds}")
    print(f"  Total matches: {rounds * len(agents) * (len(agents) - 1)}")
    print()

    # Run tournament
    t0 = time.perf_counter()
    runner = TournamentRunner(agents, rounds=rounds)
    result = runner.run()
    elapsed = time.perf_counter() - t0

    # Results (rankings already printed by runner when verbose=True)
    print(result.format_matrix())
    print()
    print(f"  Completed in {elapsed:.1f}s "
          f"({result.completed} matches, "
          f"{elapsed / max(result.completed, 1) * 1000:.1f}ms/match)")
    print()

    # Perf note
    if elapsed < 30:
        print(f"  [OK] Demo fits in 30s window ({elapsed:.1f}s) — ready to record!")
    else:
        print(f"  [!] Demo took {elapsed:.1f}s — use --rounds N to reduce")

    print("═" * 60)
    print("  To record: asciinema rec demo.cast --command 'python scripts/demo.py'")
    print("  Convert to GIF: asciicast2gif demo.cast docs/demo.gif")
    print("═" * 60)


if __name__ == "__main__":
    main()
