"""Tests for Pass 3: SkillValidatePass."""
import json
from pathlib import Path

from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.skill_validate import VALID_SCOPES, SkillValidatePass
from backend.vm.compiler.skill_compiler import SkillCompiler
from backend.vm.ir_skill import AbnormalOp, HitOp, MultModOp, ResetOp
from backend.vm.ir_values import Literal

_DATA_SKILLS = Path(__file__).resolve().parents[4] / "data" / "skills"


class TestSkillValidatePass:
    """Tests for the SkillValidatePass."""

    def test_valid_mult_mod_passes(self):
        """Valid MultModOp passes validation."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            MultModOp(target="sprite_self", attr="damage_reduction",
                      value=Literal(value=0.7))
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
            MultModOp(target="invalid_target", attr="damage_reduction",
                      value=Literal(value=1))
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "target" in ctx.errors[0].message

    def test_invalid_stat(self):
        """Invalid stat produces error (ResetOp carries stat validation)."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            ResetOp(target="skill_off_0", stat="nonexistent_stat")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "stat" in ctx.errors[0].message

    def test_invalid_scope(self):
        """Invalid scope produces error."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            MultModOp(target="sprite_self", attr="damage_reduction",
                      value=Literal(value=1),
                      scope="invalid_scope")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 1
        assert "scope" in ctx.errors[0].message

    def test_valid_scope_persistent(self):
        """Valid scope 'persistent' passes."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            MultModOp(target="sprite_self", attr="damage_reduction",
                      value=Literal(value=1),
                      scope="persistent")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_valid_scope_permanent(self):
        """Valid scope 'permanent' passes."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            MultModOp(target="sprite_self", attr="damage_reduction",
                      value=Literal(value=1),
                      scope="permanent")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_valid_target_team_opp(self):
        """team_opp target is valid."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            MultModOp(target="team_opp", attr="damage_reduction",
                      value=Literal(value=1))
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_valid_target_skill_off_0(self):
        """skill_off_0 target is valid."""
        ctx = CompilerContext(raw={})
        ctx.ir = [
            MultModOp(target="skill_off_0", attr="power_mult",
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


class TestScopeTurnIsValid:
    """`scope: "turn"` 必须被 accept（曾因 VALID_SCOPES 重复定义被后者覆盖而丢失）。

    回归背景：skill_validate.py 里 VALID_SCOPES 曾定义两次，后一份（不含 "turn"）
    生效 → 数据里 19 个文件（轮班/透镜实验/侵蚀/基因编辑/冷光源/热成像/顺风…）
    的 `"scope": "turn"` 会让技能编译直接抛 CompilationError。
    """

    def test_valid_scopes_contains_turn(self):
        assert "turn" in VALID_SCOPES

    def test_scope_turn_passes_validation(self):
        ctx = CompilerContext(raw={})
        ctx.ir = [
            MultModOp(target="sprite_self", attr="damage_reduction",
                      value=Literal(value=1), scope="turn")
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_scope_turn_inside_when_block_passes(self):
        """`when` 嵌套块内的 scope:"turn" 同样被递归校验（IR op 路径覆盖）。"""
        from backend.vm.ir_skill import PowerModOp, WhenBlock
        from backend.vm.ir_values import Literal as _Lit
        ctx = CompilerContext(raw={})
        ctx.ir = [
            WhenBlock(
                cond={"cond": "counter_succeeded"},
                then=(PowerModOp(target="skill_off_0", attr="power",
                                 delta=_Lit(value=50), scope="turn"),),
            )
        ]
        SkillValidatePass().process(ctx)
        assert len(ctx.errors) == 0

    def test_data_skills_with_turn_scope_compile(self):
        """钉住真实数据：带 scope:"turn" 的技能必须编译通过（端到端回归）。"""
        compiler = SkillCompiler()
        for path in sorted(_DATA_SKILLS.glob("*.json")):
            if path.name.startswith("_"):
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            if '"turn"' not in json.dumps(raw, ensure_ascii=False):
                continue
            # 不抛 CompilationError 即为通过
            compiler.compile(raw)

