"""Tests for Pass 2: AuraExpandPass."""
import pytest

from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.aura_expand import AuraExpandPass
from backend.vm.ir_trait import RemoveEffectOp, TraitAbnormalEffect, TraitTrigger


@pytest.fixture
def aura_pass():
    return AuraExpandPass()


def _make_ctx(data: dict, ir: list) -> CompilerContext:
    return CompilerContext(raw=data, ir=ir, meta={"name": data.get("name", ""), "id": data.get("id", 0)})


class TestAuraExpand:
    """Aura expansion tests."""

    def test_expands_aura_to_entry_leave_pair(self, aura_pass):
        """An aura trigger should produce entry + leave triggers."""
        data = {
            "name": "冰封光环",
            "triggers": [
                {
                    "on": "aura",
                    "aura": {
                        "name": "冰封",
                        "effects": [
                            {"kind": "abnormal", "name": "冻结", "stacks": 2},
                        ],
                        "target": "opponent_active",
                    },
                },
            ],
        }
        # Start with empty IR (the aura trigger hasn't been parsed yet by TraitParsePass)
        ctx = _make_ctx(data, [])
        result = aura_pass.apply(ctx)

        # Aura expansion should produce 2 triggers
        assert len(result.ir) == 2

        entry_trigger = result.ir[0]
        assert entry_trigger.on == "entry"
        assert len(entry_trigger.effects) == 1
        assert isinstance(entry_trigger.effects[0], TraitAbnormalEffect)
        assert entry_trigger.effects[0].name == "冻结"

        leave_trigger = result.ir[1]
        assert leave_trigger.on == "leave"
        assert len(leave_trigger.effects) == 1
        assert isinstance(leave_trigger.effects[0], RemoveEffectOp)
        assert leave_trigger.effects[0].source == "冰封"

    def test_aura_with_multiple_effects(self, aura_pass):
        """Aura with multiple effects should produce paired entry/leave triggers."""
        data = {
            "name": "双光环",
            "triggers": [
                {
                    "on": "aura",
                    "aura": {
                        "name": "双光",
                        "effects": [
                            {"kind": "stat", "stat": "atk", "steps": 2},
                            {"kind": "abnormal", "name": "中毒", "stacks": 3},
                        ],
                        "target": "opponent_active",
                    },
                },
            ],
        }
        ctx = _make_ctx(data, [])
        result = aura_pass.apply(ctx)

        assert len(result.ir) == 2
        entry = result.ir[0]
        assert entry.on == "entry"
        assert len(entry.effects) == 2

        leave = result.ir[1]
        assert leave.on == "leave"
        assert len(leave.effects) == 2
        assert all(isinstance(e, RemoveEffectOp) for e in leave.effects)

    def test_aura_inherits_effects_mode(self, aura_pass):
        """Entry trigger should inherit effects_mode from parent."""
        data = {
            "name": "替换光环",
            "triggers": [
                {
                    "on": "aura",
                    "effects_mode": "replace",
                    "aura": {
                        "name": "替换光",
                        "effects": [
                            {"kind": "stat", "stat": "def", "steps": 3},
                        ],
                        "target": "self",
                    },
                },
            ],
        }
        ctx = _make_ctx(data, [])
        result = aura_pass.apply(ctx)

        entry = result.ir[0]
        assert entry.effects_mode == "replace"

    def test_non_aura_triggers_pass_through(self, aura_pass):
        """Non-aura triggers should pass through unchanged."""
        data = {
            "name": "普通特性",
            "triggers": [
                {"on": "entry", "effects": []},
                {"on": "leave", "effects": []},
            ],
        }
        existing_triggers = [
            TraitTrigger(on="entry", effects=()),
            TraitTrigger(on="leave", effects=()),
        ]
        ctx = _make_ctx(data, existing_triggers)
        result = aura_pass.apply(ctx)

        assert len(result.ir) == 2
        assert result.ir[0] is existing_triggers[0]
        assert result.ir[1] is existing_triggers[1]

    def test_empty_aura_no_effects(self, aura_pass):
        """Aura with no effects should produce no triggers."""
        data = {
            "name": "空光环",
            "triggers": [
                {
                    "on": "aura",
                    "aura": {
                        "name": "空",
                        "effects": [],
                        "target": "opponent_active",
                    },
                },
            ],
        }
        ctx = _make_ctx(data, [])
        result = aura_pass.apply(ctx)
        assert len(result.ir) == 0
