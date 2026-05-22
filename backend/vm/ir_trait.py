"""特性条件 IR 节点 — PathCond + FnCond + AndCond/OrCond/NotCond."""
from __future__ import annotations

from dataclasses import dataclass, field

from .ir_values import IRValue


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
    conditions: tuple[TraitCondition, ...]


@dataclass(frozen=True)
class OrCond:
    conditions: tuple[TraitCondition, ...]


@dataclass(frozen=True)
class NotCond:
    condition: TraitCondition


TraitCondition = PathCond | FnCond | AndCond | OrCond | NotCond
