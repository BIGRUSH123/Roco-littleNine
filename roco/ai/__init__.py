"""roco.ai — AI agent protocol and reference implementations."""

from roco.ai.agent import BattleAgent, RandomAgent
from roco.ai.observation import Action, BattleObservation, SpriteSnapshot

__all__ = [
    "BattleAgent",
    "RandomAgent",
    "Action",
    "BattleObservation",
    "SpriteSnapshot",
]
