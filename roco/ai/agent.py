"""roco.ai.agent — BattleAgent protocol and reference implementations."""

from __future__ import annotations

import random
from typing import Protocol

from roco.ai.observation import Action, BattleObservation


class BattleAgent(Protocol):
    """AI opponent protocol (duck-typing, not ABC).

    Any object implementing this interface can enter a tournament.
    """

    name: str

    def select_action(
        self,
        state: BattleObservation,
        legal_actions: list[Action],
    ) -> Action: ...


class RandomAgent:
    """Reference agent that picks a random legal action.

    Useful as a baseline for tournament comparisons and as a
    minimum-viable example of the BattleAgent protocol.
    """

    name = "Random"

    def select_action(
        self,
        state: BattleObservation,
        legal_actions: list[Action],
    ) -> Action:
        if not legal_actions:
            return Action.passthrough()
        return random.choice(legal_actions)
