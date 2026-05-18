"""技能 IR 节点 — 21 op + WhenBlock + SkillCondition."""
from __future__ import annotations
from dataclasses import dataclass, field
from .ir_values import IRValue


# ── Condition ──

@dataclass(frozen=True)
class CondExpr:
    cond: str
    params: dict[str, object] = field(default_factory=dict)

    def __hash__(self):
        return hash((self.cond, str(sorted(self.params.items()))))

@dataclass(frozen=True)
class AndCond:
    conditions: tuple['SkillCondition', ...]

@dataclass(frozen=True)
class OrCond:
    conditions: tuple['SkillCondition', ...]

@dataclass(frozen=True)
class NotCond:
    condition: 'SkillCondition'

SkillCondition = CondExpr | AndCond | OrCond | NotCond


# ── When block ──

@dataclass(frozen=True)
class WhenBranch:
    cond: SkillCondition
    then: tuple['SkillIROp', ...]

@dataclass(frozen=True)
class WhenBlock:
    cond: SkillCondition
    then: tuple['SkillIROp', ...]
    else_: tuple['SkillIROp', ...] = ()
    elif_: tuple[WhenBranch, ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0


# ── Op nodes (21) ──

@dataclass(frozen=True)
class ModOp:
    target: str
    stat: str
    value: IRValue
    mode: str = "set"
    scope: str = "battlefield"
    steps: int = 0
    on_next: bool = False
    per_hit: bool = False
    skill_filter: str | None = None
    skill_where: dict | None = None
    if_type: str | None = None
    element: str | None = None
    per_element: int | None = None
    name: str | None = None
    delay: int = 0
    ttl: int = 0
    cooldown: int = 0
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class HitOp:
    power: IRValue
    type: str
    element: str | None = None
    combo: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class MarkOp:
    target: str
    name: str
    stacks: int = 1
    value: IRValue | None = None
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class AbnormalOp:
    target: str
    name: str
    stacks: int = 1
    scope: str = "battlefield"
    heal_pct: float = 0.0
    energy_gain: int = 0
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class WeatherOp:
    weather: str
    turns: int = 8
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class DispelOp:
    target: str
    what: str
    name: str | None = None
    limit: int | None = None
    type_limit: int | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class StealOp:
    target: str
    what: str
    name: str | None = None
    amount: int = 0
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class TickOp:
    target: str
    name: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class DoubleOp:
    target: str
    what: str
    name: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ChargeOp:
    target: str = "sprite_self"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class EscapeOp:
    target: str
    inherit: bool = False
    urgent: bool = False
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ReturnOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class LockOp:
    target: str
    turns: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class InterruptOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ExchangeOp:
    what: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ResetOp:
    target: str
    stat: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class RedirectOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ReplayOp:
    from_: str
    skill_filter: dict | None = None
    what: str = ""
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class BorrowOp:
    from_: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class CountOp:
    name: str = ""
    when: SkillCondition | None = None
    then: tuple['SkillIROp', ...] = ()
    scope: str = "persistent"
    feeds: str = ""
    needs: str = ""
    priority: int = 0


SkillIROp = (
    ModOp | HitOp | MarkOp | AbnormalOp | WeatherOp |
    DispelOp | StealOp | TickOp | DoubleOp | ChargeOp |
    EscapeOp | ReturnOp | LockOp | InterruptOp |
    ExchangeOp | ResetOp | RedirectOp | ReplayOp |
    BorrowOp | CountOp | WhenBlock
)
