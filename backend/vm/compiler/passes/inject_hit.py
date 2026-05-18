"""Pass 2: InjectHitPass — inject implicit HitOp for attack-type skills."""
from __future__ import annotations

from backend.vm.ir_skill import HitOp, SkillIROp
from backend.vm.ir_values import Literal
from backend.vm.compiler.context import CompilerContext

_ATTACK_TYPES = frozenset({"物攻", "魔攻", "动态攻击"})


class InjectHitPass:
    """Injects a HitOp for attack skills that have power > 0 but no explicit HitOp.

    This pass runs after SkillParsePass. It searches the IR for existing
    HitOp nodes; if none found and the skill is an attack type with power > 0,
    it prepends a HitOp to the IR list.
    """

    def process(self, ctx: CompilerContext) -> None:
        raw = ctx.raw

        # Only for attack-type skills
        skill_type = raw.get("skill_type", "")
        if skill_type not in _ATTACK_TYPES:
            return

        # Only when power > 0
        power = raw.get("power", 0)
        if not power or power <= 0:
            return

        # Check if HitOp already exists in IR
        if any(isinstance(op, HitOp) for op in ctx.ir):
            return

        # Inject HitOp at the beginning
        element = raw.get("element")
        combo = raw.get("combo", 1)
        hit = HitOp(
            power=Literal(value=power),
            type=skill_type,
            element=element,
            combo=combo,
        )
        ctx.ir.insert(0, hit)
