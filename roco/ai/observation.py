"""roco.ai.observation — immutable observation types passed to AI agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActionKind(str, Enum):
    SKILL = "skill"
    SWITCH = "switch"
    GATHER = "gather"
    ITEM = "item"
    PASS = "pass"


@dataclass(frozen=True)
class Action:
    """A legal action the agent may choose.

    PASS is always legal and serves as a graceful fallback when no
    other action is viable.
    """

    kind: ActionKind = ActionKind.PASS
    skill_index: int = 0
    switch_index: int = -1

    @classmethod
    def skill(cls, index: int) -> Action:
        return cls(kind=ActionKind.SKILL, skill_index=index)

    @classmethod
    def switch(cls, index: int) -> Action:
        return cls(kind=ActionKind.SWITCH, switch_index=index)

    @classmethod
    def gather(cls) -> Action:
        return cls(kind=ActionKind.GATHER)

    @classmethod
    def item(cls) -> Action:
        return cls(kind=ActionKind.ITEM)

    @classmethod
    def passthrough(cls) -> Action:
        return cls(kind=ActionKind.PASS)


@dataclass(frozen=True)
class SpriteSnapshot:
    """Publicly visible sprite state (no hidden information)."""

    name: str
    current_hp: int
    max_hp: int
    current_ep: int
    max_ep: int
    element: str = ""
    status: list[str] = field(default_factory=list)
    buffs: dict[str, int] = field(default_factory=dict)
    available_skills: list[str] = field(default_factory=list)
    is_fainted: bool = False


@dataclass(frozen=True)
class BattleObservation:
    """Complete observable battle state from one agent's perspective."""

    my_sprite: SpriteSnapshot
    opp_sprite: SpriteSnapshot
    weather: str = ""
    field_effects: list[str] = field(default_factory=list)
    turn_number: int = 0
    legal_actions: list[Action] = field(default_factory=list)
    my_team: list[SpriteSnapshot] = field(default_factory=list)
    opp_team_size: int = 0
