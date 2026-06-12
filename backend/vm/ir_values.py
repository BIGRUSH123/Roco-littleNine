"""共享 IRValue 类型 — Literal | Query | RefExpr."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Literal:
    """编译期已知的字面量。"""
    value: int | float | str | bool


@dataclass(frozen=True, slots=True)
class Query:
    """编译期已解析的寄存器查询。field 是 Ctx 属性名，运行时 O(1) getattr。"""
    field: str
    name: str | None = None
    scale: float = 1.0
    offset: int = 0
    per: float | None = None
    default: object = None
    sub_key_field: str = ""       # "" = normal; "mark_count_both" = sum own+opp
    pre_scale: float = 1.0        # applied BEFORE per (for derived queries like hp_missing_ratio)
    pre_offset: float = 0.0       # applied BEFORE per


@dataclass(frozen=True, slots=True)
class RefExpr:
    """编译期解析的路径表达式。用于特性 IR 的动态值。"""
    root: str
    path: list[str] = field(hash=False, compare=False)
    multiplier: float = 1.0
    offset: int = 0


IRValue = Literal | Query | RefExpr
