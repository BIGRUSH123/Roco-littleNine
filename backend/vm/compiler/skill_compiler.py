"""SkillCompiler — orchestrates the 4-pass compilation pipeline."""
from __future__ import annotations

import json
import os
from pathlib import Path

from backend.vm.ir_skill import CompiledSkill
from backend.vm.compiler.context import CompilerContext, CompilationError
from backend.vm.compiler.passes.skill_parse import SkillParsePass
from backend.vm.compiler.passes.inject_hit import InjectHitPass
from backend.vm.compiler.passes.skill_validate import SkillValidatePass
from backend.vm.compiler.passes.sort import SortPass


class SkillCompiler:
    """4-pass skill compilation pipeline: Parse -> InjectHit -> Validate -> Sort."""

    def __init__(self, passes=None):
        self.passes = passes or [
            SkillParsePass(),
            InjectHitPass(),
            SkillValidatePass(),
            SortPass(),
        ]

    def compile(self, data: dict) -> CompiledSkill:
        """Compile a single skill JSON dict into a frozen CompiledSkill.

        Raises CompilationError if any pass produces errors.
        """
        ctx = CompilerContext(raw=data)

        for p in self.passes:
            p.process(ctx)

        if ctx.errors:
            raise CompilationError(ctx.errors)

        # Build frozen CompiledSkill from parsed data
        return CompiledSkill(
            id=data.get("id", 0),
            name=data.get("name", ""),
            element=data.get("element", ""),
            skill_type=data.get("skill_type", ""),
            power=data.get("power", 0),
            energy_cost=data.get("energy_cost", 0),
            priority=data.get("priority", 0),
            combo=data.get("combo", 1),
            counter=data.get("counter", ""),
            effects=tuple(ctx.ir),
            description=data.get("description", ""),
            tag=data.get("tag", ""),
            use_devotion=data.get("use_devotion", False),
            usable_while_charging=data.get("usable_while_charging", False),
            position_locked=data.get("position_locked", False),
        )

    def compile_all(self, data_dir: str) -> dict[str, CompiledSkill]:
        """Compile all skill JSON files in a directory.

        Returns a dict mapping skill name -> CompiledSkill.
        Skips special files (_ids.json, _index.json).
        """
        results: dict[str, CompiledSkill] = {}
        errors: dict[str, CompilationError] = {}
        path = Path(data_dir)

        for file_path in sorted(path.glob("*.json")):
            # Skip index/id mapping files
            if file_path.name.startswith("_"):
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                compiled = self.compile(data)
                results[data["name"]] = compiled
            except CompilationError as e:
                errors[file_path.name] = e
            except Exception as e:
                errors[file_path.name] = CompilationError([
                    type("CompileError", (), {"op_index": -1, "message": str(e), "field": None})()
                ])

        if errors:
            names = list(errors.keys())[:10]
            raise CompilationError([
                type("CompileError", (), {
                    "op_index": -1,
                    "message": f"{name}: {errors[name]}",
                    "field": None,
                })()
                for name in names
            ])

        return results
