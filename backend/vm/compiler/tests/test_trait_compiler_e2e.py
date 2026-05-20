"""End-to-end tests: compile ALL traits from data/traits/*.json."""
import json
from pathlib import Path

import pytest

from backend.vm.compiler.context import CompilationError
from backend.vm.compiler.trait_compiler import TraitCompiler
from backend.vm.ir_trait import CompiledTrait, TraitTrigger

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
TRAITS_DIR = PROJECT_ROOT / "data" / "traits"


@pytest.fixture
def compiler():
    return TraitCompiler()


@pytest.fixture
def all_traits():
    """Load all trait JSON files from data/traits/."""
    traits: dict[str, dict] = {}
    if not TRAITS_DIR.is_dir():
        pytest.skip(f"Traits directory not found: {TRAITS_DIR}")

    for fpath in sorted(TRAITS_DIR.glob("*.json")):
        # Skip metadata/index files
        if fpath.name.startswith("_"):
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            pytest.fail(f"Failed to read {fpath.name}: {e}")
        name = data.get("name", fpath.stem)
        traits[name] = data

    return traits


class TestCompileAllTraits:
    """Compile every trait JSON into CompiledTrait."""

    def test_compiles_all_traits(self, compiler, all_traits):
        errors: list[str] = []
        for name, data in all_traits.items():
            try:
                compiled = compiler.compile(data)
                assert compiled.name == data.get("name", "")
                assert isinstance(compiled.id, int)
                assert isinstance(compiled.triggers, tuple)
                # Each trigger must be a TraitTrigger
                for t in compiled.triggers:
                    assert isinstance(t, TraitTrigger), f"{name}: trigger not TraitTrigger"
                    assert t.on, f"{name}: trigger has no 'on' field"
            except CompilationError as e:
                errors.append(f"{name}: {e}")
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}: {e}")

        assert len(errors) == 0, (
            f"{len(errors)}/{len(all_traits)} traits failed:\n" +
            "\n".join(errors[:10]) +
            ("\n..." if len(errors) > 10 else "")
        )

    def test_all_traits_have_required_fields(self, all_traits):
        """Every trait JSON must have id, name, and either triggers or passive."""
        for name, data in all_traits.items():
            assert "id" in data, f"{name}: missing id"
            assert "name" in data, f"{name}: missing name"
            has_triggers = "triggers" in data
            has_passive = "passive" in data
            assert has_triggers or has_passive, f"{name}: missing triggers and passive"
            source = data.get("triggers", data.get("passive", []))
            assert isinstance(source, list), f"{name}: triggers/passive not a list"
            assert len(source) > 0, f"{name}: empty triggers/passive"

    def test_all_triggers_have_on_field(self, all_traits):
        """Every trigger/passive entry must have an on/cond field."""
        for name, data in all_traits.items():
            # Check triggers format
            for i, trigger in enumerate(data.get("triggers", [])):
                assert "on" in trigger, f"{name} triggers #{i}: missing 'on'"
            # Check passive format
            for i, entry in enumerate(data.get("passive", [])):
                # Direct mod ops don't need 'on' — they're entry triggers
                if entry.get("op") == "mod" and "when" not in entry:
                    continue
                when = entry.get("when", {})
                assert "cond" in when, f"{name} passive #{i}: missing when.cond"

    def test_compile_individual_known_traits(self, compiler):
        """Smoke test: compile representative traits individually."""
        traits_to_test = [
            {
                "id": 1, "name": "test_counter",
                "triggers": [{"on": "counter_success", "battleskill_mut": [
                    {"filter": {"is_attack": True}, "field": "power_mod", "op": "add", "value": 1.0}
                ]}],
            },
            {
                "id": 2, "name": "test_entry_stat",
                "triggers": [{"on": "entry", "effects_mode": "replace", "effects": [
                    {"kind": "stat", "stat": "atk", "steps": 5, "scope": "battlefield", "source": "test"}
                ]}],
            },
            {
                "id": 3, "name": "test_defend_modifier",
                "triggers": [{"on": "defend", "condition": {"path": "skill.element", "op": "eq", "value": "火"},
                              "use_modifiers": {"damage_mult": {"op": "set", "value": 0.5}}}],
            },
            {
                "id": 4, "name": "test_abnormal",
                "triggers": [{"on": "skill_use", "condition": {"kind": "and", "conditions": [
                    {"path": "skill.is_attack", "op": "eq", "value": True},
                    {"path": "skill.element", "op": "eq", "value": "毒"},
                ]}, "effects": [
                    {"kind": "abnormal", "name": "中毒", "stacks": 3, "target": "target", "source": "test"}
                ]}],
            },
            {
                "id": 5, "name": "test_flags_only",
                "triggers": [{"on": "turn_end", "condition": {"path": "self.energy", "op": "eq", "value": 0},
                              "flags": {"_escape_pending": True}}],
            },
            {
                "id": 6, "name": "test_conditional_replace",
                "triggers": [{"on": "entry", "effects_mode": "conditional_replace",
                              "clear_condition": {"path": "battle.turn", "op": "gt", "value": 5},
                              "effects": [{"kind": "stat", "stat": "sp_atk", "steps": 10, "source": "test"}]}],
            },
        ]

        for data in traits_to_test:
            compiled = compiler.compile(data)
            assert isinstance(compiled, CompiledTrait)
            assert compiled.name == data["name"]
            assert len(compiled.triggers) > 0
