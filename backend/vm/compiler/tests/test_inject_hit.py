"""Tests for Pass 2: InjectHitPass."""
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.inject_hit import InjectHitPass
from backend.vm.compiler.passes.skill_parse import SkillParsePass
from backend.vm.ir_skill import HitOp, ModOp
from backend.vm.ir_values import Literal


class TestInjectHitPass:
    """Tests for the InjectHitPass."""

    def _parse_and_inject(self, data: dict) -> CompilerContext:
        ctx = CompilerContext(raw=data)
        SkillParsePass().process(ctx)
        InjectHitPass().process(ctx)
        return ctx

    def test_injects_hit_for_attack_skill(self):
        """Attack skill with power > 0 gets HitOp injected."""
        data = {
            "skill_type": "物攻",
            "power": 90,
            "element": "冰",
            "combo": 1,
            "effects": [
                {"op": "mod", "target": "sprite_opp", "stat": "speed", "steps": -3}
            ]
        }
        ctx = self._parse_and_inject(data)
        assert len(ctx.errors) == 0
        # HitOp should be first, followed by the mod
        assert len(ctx.ir) == 2
        assert isinstance(ctx.ir[0], HitOp)
        assert ctx.ir[0].power == Literal(value=90)
        assert ctx.ir[0].type == "物攻"
        assert ctx.ir[0].element == "冰"
        assert isinstance(ctx.ir[1], ModOp)

    def test_no_inject_for_zero_power(self):
        """Skill with power 0 does not get HitOp."""
        data = {
            "skill_type": "物攻",
            "power": 0,
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 1}
            ]
        }
        ctx = self._parse_and_inject(data)
        assert len(ctx.ir) == 1
        assert not isinstance(ctx.ir[0], HitOp)

    def test_no_inject_for_status_skill(self):
        """Status skill does not get HitOp."""
        data = {
            "skill_type": "状态",
            "power": 50,
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 3}
            ]
        }
        ctx = self._parse_and_inject(data)
        assert len(ctx.ir) == 1
        assert isinstance(ctx.ir[0], ModOp)

    def test_no_inject_for_defense_skill(self):
        """Defense skill does not get HitOp."""
        data = {
            "skill_type": "防御",
            "power": 70,
            "effects": [
                {"op": "mod", "target": "sprite_self", "stat": "damage_reduction",
                 "value": 0.7}
            ]
        }
        ctx = self._parse_and_inject(data)
        assert len(ctx.ir) == 1
        assert isinstance(ctx.ir[0], ModOp)

    def test_no_duplicate_hit_injection(self):
        """If HitOp already exists, don't inject another."""
        data = {
            "skill_type": "魔攻",
            "power": 100,
            "effects": [
                {"op": "hit", "power": 100, "type": "魔攻"}
            ]
        }
        ctx = self._parse_and_inject(data)
        # Should only have the original HitOp
        hits = [op for op in ctx.ir if isinstance(op, HitOp)]
        assert len(hits) == 1

    def test_no_power_field_defaults_to_zero(self):
        """Skill without power field defaults to 0, no injection."""
        data = {
            "skill_type": "物攻",
            "effects": []
        }
        ctx = self._parse_and_inject(data)
        assert len(ctx.ir) == 0

    def test_attack_skill_empty_effects(self):
        """Attack skill with power > 0 but empty effects still gets HitOp."""
        data = {
            "skill_type": "物攻",
            "power": 60,
            "element": "地",
            "effects": []
        }
        ctx = self._parse_and_inject(data)
        assert len(ctx.ir) == 1
        assert isinstance(ctx.ir[0], HitOp)
        assert ctx.ir[0].power == Literal(value=60)
