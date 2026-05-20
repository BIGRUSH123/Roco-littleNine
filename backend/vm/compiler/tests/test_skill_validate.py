"""Tests for Pass 3: SkillValidatePass."""
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.skill_validate import SkillValidatePass
from backend.vm.ir_skill import AbnormalOp, HitOp, ModOp
from backend.vm.ir_values import Literal


class TestSkillValidatePass:
    """Tests for the SkillValidatePass."""

    def test_valid_mod_passes(self):
        """Valid ModOp passes validation."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="sprite_self", stat="atk", value=Literal(value=1))
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_valid_hit_passes(self):
        """Valid HitOp passes validation."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            HitOp(power=Literal(value=100), type="物攻")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_invalid_target(self):
        """Invalid target produces error."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="invalid_target", stat="atk", value=Literal(value=1))
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "target" in ctx.errors[0].message

    def test_invalid_stat(self):
        """Invalid stat produces error."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="sprite_self", stat="nonexistent_stat",
                  value=Literal(value=1))
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "stat" in ctx.errors[0].message

    def test_invalid_scope(self):
        """Invalid scope produces error."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="sprite_self", stat="atk", value=Literal(value=1),
                  scope="invalid_scope")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "scope" in ctx.errors[0].message

    def test_valid_scope_persistent(self):
        """Valid scope 'persistent' passes."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="sprite_self", stat="atk", value=Literal(value=1),
                  scope="persistent")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_valid_scope_permanent(self):
        """Valid scope 'permanent' passes."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="sprite_self", stat="atk", value=Literal(value=1),
                  scope="permanent")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_valid_target_team_opp(self):
        """team_opp target is valid."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="team_opp", stat="mark_count",
                  value=Literal(value=1))
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_valid_target_skill_off_0(self):
        """skill_off_0 target is valid."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ModOp(target="skill_off_0", stat="power",
                  value=Literal(value=50))
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_invalid_hit_type(self):
        """Invalid hit type produces error."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            HitOp(power=Literal(value=100), type="invalid_type")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "skill_type" in ctx.errors[0].message

    def test_empty_ir_passes(self):
        """Empty IR passes validation."""
        ctx = CompilerContext(raw={})
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_abnormal_with_valid_args(self):
        """AbnormalOp with valid args passes."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            AbnormalOp(target="sprite_opp", name="中毒", stacks=2)
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0
