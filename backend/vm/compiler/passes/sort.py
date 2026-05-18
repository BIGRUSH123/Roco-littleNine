"""Pass 4: SortPass — topological sort SkillIROp list by execution phase.

Sorts effects into phases based on feeds/needs declarations, using the
same _PHASE constant from backend.vm.sort for consistency.
"""
from __future__ import annotations

from backend.vm.ir_skill import SkillIROp, WhenBlock
from backend.vm.sort import _PHASE, _DEFAULT_PHASE
from backend.vm.compiler.context import CompilerContext


class SortPass:
    """Sort IR ops by execution phase while preserving relative order.

    Effects are bucketed into phases (cost, power, mult, default, counter,
    turn_end) and then concatenated. Within each phase, effects with higher
    priority execute first; ties preserve original order.
    """

    def process(self, ctx: CompilerContext) -> None:
        if not ctx.ir:
            return

        # Tag each op with (original_index, phase, -priority)
        tagged = [
            (i, self._phase_of(op), -self._priority_of(op), op)
            for i, op in enumerate(ctx.ir)
        ]

        # Sort by (phase, -priority, original_index)
        tagged.sort(key=lambda x: (x[1], x[2], x[0]))

        ctx.ir = [op for _, _, _, op in tagged]

    @staticmethod
    def _phase_of(op: SkillIROp) -> int:
        """Return the execution phase index for an IR op."""
        feeds = getattr(op, "feeds", "")
        if feeds and feeds in _PHASE:
            return _PHASE[feeds]
        needs = getattr(op, "needs", "")
        if needs and needs in _PHASE:
            return _PHASE[needs]
        return _DEFAULT_PHASE

    @staticmethod
    def _priority_of(op: SkillIROp) -> int:
        """Return the priority of an IR op, defaulting to 0."""
        return getattr(op, "priority", 0)
