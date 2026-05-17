"""VM Mutation types — the only output the VM produces.

All dataclasses are frozen (immutable). The VM produces a Journal (list of
Mutation) and the engine replays it against mutable battle state.
"""

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class StatChange:
    """Permanent stat modification applied to a sprite."""
    target: str       # "sprite_self" | "sprite_opp"
    stat: str         # "atk" | "def" | "sp_atk" | "sp_def" | "speed" | ...
    steps: int        # positive = buff, negative = debuff
    scope: str        # "battlefield" | "persistent" | "permanent"


@dataclass(frozen=True)
class ModifierInjection:
    """Internal modifier for the skill pipeline (not applied to sprite state).

    Engine collects these during power/mult phases and feeds them into
    the damage formula. Examples: power_mult, damage_mult, damage_reduction,
    energy_cost modifications.
    """
    target: str       # "skill_off_0" | "sprite_self" | ...
    stat: str         # "power_mult" | "damage_mult" | "damage_reduction" | "energy_cost" | ...
    value: float
    scope: str        # "battlefield" | "persistent" | "permanent"
    mode: str = "set" # "set" | "add" | "multiply"


@dataclass(frozen=True)
class Damage:
    """Final computed damage to apply to a sprite."""
    target: str       # "sprite_opp" | "sprite_self"
    amount: int
    element: str
    type: str         # "物攻" | "魔攻"


@dataclass(frozen=True)
class Heal:
    """HP restoration."""
    target: str
    amount: int


@dataclass(frozen=True)
class EnergyChange:
    """Energy gain or loss."""
    target: str
    delta: int        # positive = gain, negative = lose


@dataclass(frozen=True)
class MarkChange:
    """Mark stack change on a team."""
    target_team: str  # "own" | "opp"
    name: str         # mark name
    delta: int        # positive = add stacks, negative = remove


@dataclass(frozen=True)
class AbnormalChange:
    """Abnormal status stack change on a sprite."""
    target: str       # "sprite_self" | "sprite_opp"
    name: str         # abnormal name, e.g. "中毒"
    delta: int        # stacks to add (positive) or set-to-zero (special sentinel)


@dataclass(frozen=True)
class WeatherSet:
    """Weather change."""
    weather: str
    turns: int


@dataclass(frozen=True)
class Dispel:
    """Remove effects from a target."""
    target: str
    what: str         # "positive" | "negative" | "abnormal" | "mark"
    name: str | None = None
    limit: int | None = None
    type_limit: int | None = None


@dataclass(frozen=True)
class Steal:
    """Steal effects/energy from a target."""
    from_target: str  # "sprite_opp" | "team_opp"
    what: str         # "positive" | "mark" | "energy"
    name: str | None = None
    amount: int | None = None


@dataclass(frozen=True)
class Tick:
    """Trigger abnormal tick damage on a target."""
    target: str
    abnormal_name: str


@dataclass(frozen=True)
class Double:
    """Double a mark/effect on a target."""
    target: str
    what: str
    name: str | None = None


@dataclass(frozen=True)
class Charge:
    """Enter charging state."""
    target: str       # "sprite_self"


@dataclass(frozen=True)
class Escape:
    """Remove self from field (switch/escape)."""
    target: str
    inherit: bool = False
    urgent: bool = False
    then: list | None = None  # Effects to execute after escape


@dataclass(frozen=True)
class Return:
    """Return to field after escape/switch."""
    target: str


@dataclass(frozen=True)
class Lock:
    """Lock opponent from switching."""
    target: str
    turns: int


@dataclass(frozen=True)
class Interrupt:
    """Interrupt the target's current action."""
    target: str


@dataclass(frozen=True)
class Exchange:
    """Exchange something between sprites (HP ratio, effects, skills)."""
    target: str
    what: str         # "hp_ratio" | "effects" | "skills" | "adjacent_skills"


@dataclass(frozen=True)
class Reset:
    """Reset a stat to base value."""
    target: str
    stat: str


@dataclass(frozen=True)
class Redirect:
    """Redirect the next action to a different target."""
    target: str


@dataclass(frozen=True)
class Replay:
    """Replay a previously used skill or burst skill."""
    from_: str        # "sprite_self" | "team_burst" (from_ avoids Python keyword)
    skill_filter: dict | None = None


@dataclass(frozen=True)
class Borrow:
    """Borrow properties from the opponent's current skill."""
    from_skill: str   # "skill_opp_current"


@dataclass(frozen=True)
class CounterRegister:
    """Register a persistent counter/watcher on the skill."""
    name: str | None
    cond: dict        # Trigger condition (shared COND_EVAL table)
    then: list        # IR effects to execute when triggered
    scope: str        # "battlefield" | "persistent" | "permanent"


# Union of all mutation types the VM can produce
Mutation = Union[
    StatChange, ModifierInjection, Damage, Heal, EnergyChange,
    MarkChange, AbnormalChange, WeatherSet, Dispel, Steal, Tick,
    Double, Charge, Escape, Return, Lock, Interrupt, Exchange,
    Reset, Redirect, Replay, Borrow, CounterRegister,
]

# Journal is an ordered list of mutations
Journal = list[Mutation]
