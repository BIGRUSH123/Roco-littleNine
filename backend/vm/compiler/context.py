"""Compiler context — shared state across the 4-pass compilation pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompileError:
    """A single compilation error with source location."""
    op_index: int
    message: str
    field: str | None = None


@dataclass
class CompilerContext:
    """Mutable context threaded through each compiler pass.

    Passes read ctx.raw (input JSON), write to ctx.ir (output IR list),
    and append errors/warnings for diagnostic reporting.
    """
    raw: dict
    ir: list = field(default_factory=list)
    errors: list[CompileError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class CompilationError(Exception):
    """Raised when compilation produces one or more errors."""
    def __init__(self, errors: list[CompileError]):
        self.errors = errors
        msg = f"{len(errors)} compilation error(s):\n"
        msg += "\n".join(
            f"  [{e.op_index}] {e.message}" + (f" (field={e.field})" if e.field else "")
            for e in errors[:10]
        )
        if len(errors) > 10:
            msg += f"\n  ... and {len(errors) - 10} more"
        super().__init__(msg)
