"""TraitCompiler — 3-pass compilation pipeline (Parse -> AuraExpand -> Validate)."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

from backend.vm.ir_trait import CompiledTrait
from backend.vm.compiler.context import CompilerContext, CompilationError
from backend.vm.compiler.passes.trait_parse import TraitParsePass
from backend.vm.compiler.passes.aura_expand import AuraExpandPass
from backend.vm.compiler.passes.trait_validate import TraitValidatePass


class TraitCompiler:
    """Compile a trait JSON dict into a frozen CompiledTrait.

    Pipeline:
      1. TraitParsePass   — JSON triggers → TraitTrigger IR
      2. AuraExpandPass   — aura definitions → entry+leave pairs
      3. TraitValidatePass — hook/field whitelist validation
    """

    def __init__(self, passes=None):
        self.passes = passes or [
            TraitParsePass(),
            AuraExpandPass(),
            TraitValidatePass(),
        ]

    def compile(self, data: dict) -> CompiledTrait:
        """Compile a single trait JSON dict into a CompiledTrait.

        Args:
            data: Raw trait JSON dict (with id, name, description, triggers).

        Returns:
            CompiledTrait with frozen IR triggers.

        Raises:
            CompilationError: If any compilation pass produces errors.
        """
        ctx = CompilerContext(
            raw=data,
            ir=[],
            errors=[],
            warnings=[],
            meta={
                "name": data.get("name", ""),
                "id": data.get("id", 0),
            },
        )

        for p in self.passes:
            ctx = p.apply(ctx)

        if ctx.errors:
            raise CompilationError(ctx.errors)

        return CompiledTrait(
            id=data.get("id", 0),
            name=data.get("name", ""),
            description=data.get("description", ""),
            triggers=tuple(ctx.ir),
        )

    def compile_all(self, data_dir: str) -> dict[str, CompiledTrait]:
        """Compile all trait JSON files in a directory.

        Args:
            data_dir: Path to directory containing trait *.json files.

        Returns:
            Dict mapping trait name to CompiledTrait.
        """
        results: dict[str, CompiledTrait] = {}
        errors: dict[str, str] = {}
        root = Path(data_dir)
        if not root.is_dir():
            return results

        for fpath in sorted(root.glob("*.json")):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                errors[fpath.name] = f"Failed to read: {e}"
                continue

            name = data.get("name", fpath.stem)
            try:
                compiled = self.compile(data)
                results[name] = compiled
            except CompilationError as e:
                errors[name] = str(e)
            except Exception as e:
                errors[name] = f"Unexpected error: {e}"

        # If there were errors, collect them for reporting (non-fatal for compile_all)
        if errors:
            import warnings as _warnings
            _warnings.warn(
                f"compile_all: {len(errors)} traits failed out of {len(results) + len(errors)} total"
            )

        return results
