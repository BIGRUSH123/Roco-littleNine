"""VM Mutation types — the only output the VM produces.

All dataclasses are frozen (immutable). The VM produces a Journal (list of
Mutation) and the engine replays it against mutable battle state.
"""

from dataclasses import dataclass, field
from typing import Union


@dataclass(frozen=True)
class StatChange:
    """Permanent stat modification applied to a sprite."""
    target: str       # "sprite_self" | "sprite_opp"
    stat: str         # "atk" | "def" | "sp_atk" | "sp_def" | "speed" | ...
    steps: int        # positive = buff, negative = debuff
    scope: str = "battlefield"  # "battlefield" | "persistent" | "permanent"
    # Optional metadata for engine-side resolution
    source: str | None = None         # trait/skill name for replace-mode clearing
    element: str | None = None        # element filter ("火", "each", etc.)
    per_element: int | None = None    # max per element when element="each"
    on_next: bool = False             # defer to next matching skill use
    if_type: str | None = None        # "attack" | "defense" | "status"
    skill_filter: str | None = None   # "attack" | "defense" | "status" | "all" | "others" | "adjacent" | "bare_*"
    skill_where: dict | None = None   # per-skill conditional {"q": ..., "op": ..., "value": ...}


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
    scope: str = "battlefield"  # "battlefield" | "persistent" | "permanent"
    mode: str = "set" # "set" | "add" | "multiply"
    # Optional metadata for engine-side resolution
    name: str | None = None           # exact skill name or devotion name
    source: str | None = None         # trait/skill name for replace-mode clearing
    element: str | None = None        # element filter ("火", "each", etc.)
    per_element: int | None = None    # max per element when element="each"
    on_next: bool = False             # defer to next matching skill use
    if_type: str | None = None        # "attack" | "defense" | "status"
    skill_filter: str | None = None   # "attack" | "defense" | "status" | "all" | "others" | "adjacent" | "bare_*"
    skill_where: dict | None = None   # per-skill conditional {"q": ..., "op": ..., "value": ...}
    ttl: int = 0                      # remaining turns (0 = no expiry)
    then: list | None = None          # chained effects (devotion then-block)


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
    action: str = "apply"   # "apply" | "dispel" | "steal" | "convert"
    ratio: float = 1.0      # convert: abnormal→mark conversion ratio
    source_abnormal: str | None = None  # convert: source abnormal name


@dataclass(frozen=True)
class AbnormalChange:
    """Abnormal status stack change on a sprite."""
    target: str       # "sprite_self" | "sprite_opp"
    name: str         # abnormal name, e.g. "中毒"
    delta: int        # stacks to add (positive) or set-to-zero (special sentinel)
    scope: str = "battlefield"  # "battlefield" | "persistent" | "permanent"


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
    source: str | None = None  # only dispel effects from this source


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
class EffectDelta:
    """Add delta to all matching effects (positive/negative) on a target."""
    target: str       # "sprite_self" | "sprite_opp"
    what: str         # "positive" | "negative"
    delta: int        # stacks/steps to add


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
class BurstGrant:
    """Grant burst effects to matching skills."""
    target: str                         # "sprite_self"
    skill_where: dict | None = None     # per-skill conditional filter
    skill_filter: str | None = None     # "attack" | "defense" | "status" | "all"
    effects: tuple = ()                 # burst effect dicts to execute on first_action
    source: str = ""                    # trait name


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
class TeamCounterDelta:
    """Write to a team-level counter."""
    target: str        # "own" | "opp"
    key: str           # counter key name
    delta: int         # +1 or -1


@dataclass(frozen=True)
class LivesDelta:
    """Modify player lives (魔力)."""
    target_team: str   # "own" | "opp"
    delta: int         # +1 (奉献) or -1 (魔力消耗)


@dataclass(frozen=True)
class ScheduleEntry:
    """Register delayed effects for a future turn (RISC: defer)."""
    turns: int
    at: str = "turn_start"    # "turn_start" | "turn_end"
    then: list = field(default_factory=list)  # IR effects to execute at the delayed time


@dataclass(frozen=True)
class InheritEffectsMutation:
    """Transfer effects between sprites on switch."""
    source_key: str             # "self" | "target"
    target_key: str             # "enemy_new" | sprite_ref
    scope: str = "battlefield"
    via_pending: bool = False   # route through battle.pending_effects
    effects: tuple = ()         # fixed effects to apply to incoming sprite
    inherit_stat_effects: bool = False  # copy dynamic stat effects


@dataclass(frozen=True)
class TransformMutation:
    """Transform a sprite's species and optionally skills."""
    species: str
    skills: tuple[str, ...] | None = None
    reset_hp: bool = False
    reset_energy: bool = False


@dataclass(frozen=True)
class TraitInteractionMutation:
    """Suppress, remove, or copy a trait."""
    action: str             # "suppress" | "remove" | "copy"
    target: str             # sprite ref
    copy_from: str | None = None
    new_ability: str | None = None


@dataclass(frozen=True)
class GainSkillsMutation:
    """Grant temporary skills to a sprite from a skill pool."""
    count: int = 1
    exclude_carried: bool = True
    source: str = "learnset"   # "learnset" | "global"
    target: str = "sprite_self"


@dataclass
class CounterRegister:
    """Register a persistent counter/watcher on the skill."""
    name: str | None = None
    cond: dict | None = None     # Trigger condition
    then: list = field(default_factory=list)  # IR effects to execute
    scope: str = "persistent"
    listen: frozenset | None = None  # explicit trigger set (None = auto-infer)
    threshold: int = 1            # fire every N triggers
    reset_on_fire: bool = True    # reset counter after firing


# Union of all mutation types the VM can produce
Mutation = Union[
    StatChange, ModifierInjection, Damage, Heal, EnergyChange,
    MarkChange, AbnormalChange, WeatherSet, Dispel, Steal, Tick,
    Double, EffectDelta, Charge, Escape, Return, Lock, Interrupt, Exchange,
    Reset, Redirect, Replay, Borrow, CounterRegister, BurstGrant,
    TeamCounterDelta, LivesDelta, ScheduleEntry,
    InheritEffectsMutation, TransformMutation, TraitInteractionMutation,
    GainSkillsMutation,
]

# Journal is an ordered list of mutations
Journal = list[Mutation]
