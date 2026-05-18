"""特性 IR 节点 — TraitEffect + TraitTrigger + TraitCondition."""
from __future__ import annotations
from dataclasses import dataclass, field
from .ir_values import IRValue, Literal as _Literal


# ── TraitCondition (path-based, different from skill conditions) ──

@dataclass(frozen=True)
class PathCond:
    path: list[str] = field(hash=False, compare=False)
    op: str
    value: IRValue


@dataclass(frozen=True)
class FnCond:
    name: str


@dataclass(frozen=True)
class AndCond:
    conditions: tuple["TraitCondition", ...]


@dataclass(frozen=True)
class OrCond:
    conditions: tuple["TraitCondition", ...]


@dataclass(frozen=True)
class NotCond:
    condition: "TraitCondition"


TraitCondition = PathCond | FnCond | AndCond | OrCond | NotCond


# ── Effect mutation operations ──

@dataclass(frozen=True)
class MutateEffectOp:
    target: str
    filter: dict = field(hash=False, compare=False)
    delta_steps: int = 0
    delta_stacks: int = 0


@dataclass(frozen=True)
class RemoveEffectOp:
    source: str
    target: str


# ── Engine injection operations ──

@dataclass(frozen=True)
class BattleSkillMutOp:
    filter: dict = field(hash=False, compare=False)
    field: str
    value: IRValue
    op: str = "set"
    target: str = "all"


@dataclass(frozen=True)
class UseModifierOp:
    key: str
    value: IRValue
    op: str = "set"
    target: str = "modifiers"


@dataclass(frozen=True)
class ActionModifierOp:
    action: str
    slot: int | None = None
    slots: list[int] | None = field(default=None, hash=False, compare=False)
    force: str | None = None


# ── Delayed / cross-sprite operations ──

@dataclass(frozen=True)
class ScheduleOp:
    turns: int
    phase: str = "start"
    effects: tuple["TraitEffect", ...] = ()


@dataclass(frozen=True)
class InheritEffectsOp:
    scope: str = "battlefield"
    source_sprite: str = "self"
    target: str = "enemy_new"
    via_pending: bool = False


@dataclass(frozen=True)
class TeamCounterOp:
    key: str
    delta: int = 1
    target_team: str = "own"


@dataclass(frozen=True)
class TransformOp:
    species: str
    skills: list[str] | None = field(default=None, hash=False, compare=False)
    reset_hp: bool = False
    reset_energy: bool = False


@dataclass(frozen=True)
class TraitInteractionOp:
    action: str
    target: str
    copy_from: str | None = None
    new_ability: str | None = None


@dataclass(frozen=True)
class LivesOp:
    delta: int
    target_team: str = "own"


# ── Shared effect types (used by both skill VM and trait engine via effect_applier) ──

@dataclass(frozen=True)
class TraitStatEffect:
    kind: str = "stat"
    target: str = "self"
    stat: str = ""
    steps: IRValue = _Literal(0)
    scope: str = "battlefield"
    source: str = ""


@dataclass(frozen=True)
class TraitAbnormalEffect:
    kind: str = "abnormal"
    target: str = "opp"
    name: str = ""
    stacks: IRValue = _Literal(1)
    scope: str = "battlefield"
    source: str = ""


@dataclass(frozen=True)
class TraitMarkEffect:
    kind: str = "mark"
    name: str = ""
    stacks: int = 1
    mark_target: str = "opp_team"


@dataclass(frozen=True)
class TraitWeatherEffect:
    kind: str = "weather"
    weather: str = ""
    turns: int = 8


@dataclass(frozen=True)
class TraitSpecialEffect:
    kind: str = "special"
    name: str = ""
    value: IRValue | None = None
    amount: IRValue | None = None
    target: str = "self"
    target_team: str = "own"


TraitEffect = (
    TraitStatEffect | TraitAbnormalEffect | TraitMarkEffect
    | TraitWeatherEffect | TraitSpecialEffect
    | MutateEffectOp | RemoveEffectOp
    | ScheduleOp | InheritEffectsOp | TeamCounterOp
    | TransformOp | TraitInteractionOp | LivesOp
)


# ── Trigger + compiled trait ──

@dataclass(frozen=True)
class TraitTrigger:
    on: str
    condition: TraitCondition | None = None
    effects: tuple[TraitEffect, ...] = ()
    effects_mode: str = "accumulate"
    clear_condition: TraitCondition | None = None
    delay: int = 0
    delay_phase: str = "start"
    counter: str | None = None
    counter_op: str = "inc"
    counter_value: IRValue | None = None
    counter_trigger: dict | None = field(default=None, hash=False, compare=False)
    counter_reset: bool = False
    track: dict | None = field(default=None, hash=False, compare=False)
    use_modifiers: dict[str, dict] | None = field(default=None, hash=False, compare=False)
    battleskill_mut: tuple[BattleSkillMutOp, ...] = ()
    action_modifier: ActionModifierOp | None = None
    pending_effects: tuple[TraitEffect, ...] = ()
    flags: dict | None = field(default=None, hash=False, compare=False)
    team_counters: dict | None = field(default=None, hash=False, compare=False)


@dataclass(frozen=True)
class CompiledTrait:
    id: int
    name: str
    description: str
    triggers: tuple[TraitTrigger, ...]
