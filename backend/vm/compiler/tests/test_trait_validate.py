"""Tests for Pass 3: TraitValidatePass."""
import pytest
from backend.vm.compiler.passes.trait_validate import TraitValidatePass
from backend.vm.compiler.context import CompilerContext
from backend.vm.ir_trait import TraitTrigger, PathCond
from backend.vm.ir_values import Literal


@pytest.fixture
def validate_pass():
    return TraitValidatePass()


def _make_ctx(ir: list) -> CompilerContext:
    return CompilerContext(raw={}, ir=ir, meta={"name": "test"})


class TestValidateHooks:
    """Hook validation tests."""

    def test_valid_hooks_pass(self, validate_pass):
        for hook in ("entry", "leave", "defend", "modifier", "counter_success",
                     "damage", "skill_use", "faint", "ko_enemy", "turn_start",
                     "turn_end", "energy_change", "gain_effect", "inflict",
                     "enemy_leave", "abnormal_tick", "take_damage"):
            ctx = _make_ctx([TraitTrigger(on=hook)])
            result = validate_pass.apply(ctx)
            assert len(result.errors) == 0, f"Hook '{hook}' should pass validation"

    def test_empty_on_produces_error(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(on="")])
        result = validate_pass.apply(ctx)
        assert len(result.errors) > 0

    def test_unknown_hook_produces_warning(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(on="some_invalid_hook")])
        result = validate_pass.apply(ctx)
        # Unknown hooks produce warnings, not errors
        assert len(result.warnings) > 0
        assert len(result.errors) == 0


class TestValidateEffectsMode:
    """Effects mode validation tests."""

    def test_valid_modes_pass(self, validate_pass):
        for mode in ("accumulate", "replace"):
            ctx = _make_ctx([TraitTrigger(on="entry", effects_mode=mode)])
            result = validate_pass.apply(ctx)
            assert len(result.errors) == 0, f"Mode '{mode}' should pass"

    def test_invalid_mode_produces_error(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(on="entry", effects_mode="invalid_mode")])
        result = validate_pass.apply(ctx)
        assert len(result.errors) > 0

    def test_conditional_replace_without_clear_condition(self, validate_pass):
        """conditional_replace requires clear_condition."""
        ctx = _make_ctx([TraitTrigger(
            on="entry",
            effects_mode="conditional_replace",
            clear_condition=None,
        )])
        result = validate_pass.apply(ctx)
        assert len(result.errors) > 0
        assert any("clear_condition" in e.message for e in result.errors)

    def test_conditional_replace_with_clear_condition(self, validate_pass):
        """conditional_replace with clear_condition should pass."""
        ctx = _make_ctx([TraitTrigger(
            on="entry",
            effects_mode="conditional_replace",
            clear_condition=PathCond(path=["battle"], op="eq", value=Literal("rain")),
        )])
        result = validate_pass.apply(ctx)
        assert len(result.errors) == 0

    def test_replace_without_clear_condition_passes(self, validate_pass):
        """replace mode doesn't need clear_condition."""
        ctx = _make_ctx([TraitTrigger(
            on="entry",
            effects_mode="replace",
            clear_condition=None,
        )])
        result = validate_pass.apply(ctx)
        assert len(result.errors) == 0


class TestValidateDelay:
    """Delay validation tests."""

    def test_negative_delay_produces_error(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(on="entry", delay=-1)])
        result = validate_pass.apply(ctx)
        assert len(result.errors) > 0

    def test_zero_delay_passes(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(on="entry", delay=0)])
        result = validate_pass.apply(ctx)
        assert len(result.errors) == 0

    def test_positive_delay_passes(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(on="entry", delay=3)])
        result = validate_pass.apply(ctx)
        assert len(result.errors) == 0


class TestEmptyTrigger:
    """Warning for triggers with no actionable fields."""

    def test_empty_trigger_warns(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(on="entry")])
        result = validate_pass.apply(ctx)
        assert len(result.warnings) > 0

    def test_trigger_with_effects_no_warn(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(
            on="entry",
            effects=(Literal(0),),
        )])
        result = validate_pass.apply(ctx)
        assert not any("no effects" in w for w in result.warnings)

    def test_trigger_with_flags_no_warn(self, validate_pass):
        ctx = _make_ctx([TraitTrigger(
            on="entry",
            flags={"_pending": True},
        )])
        result = validate_pass.apply(ctx)
        assert not any("no effects" in w for w in result.warnings)
