"""my_agent.py — minimal BattleAgent example (~15 lines of actual code).

Run it:
    python -m roco.tournament examples/my_agent.py roco.ai.agent --rounds 10
"""

from roco.ai.observation import Action, ActionKind, BattleObservation


class DamageAgent:
    """Pick the highest-damage skill. If none available, gather."""

    name = "DamageAgent"

    def select_action(self, state: BattleObservation, legal_actions: list[Action]) -> Action:
        # Filter to usable skill actions
        skills = [a for a in legal_actions if a.kind == ActionKind.SKILL]
        if skills:
            return skills[0]  # pick first available skill
        # Fall back to gather
        for a in legal_actions:
            if a.kind == ActionKind.GATHER:
                return a
        return legal_actions[0]


# Module-level instance for auto-discovery
agent = DamageAgent()
