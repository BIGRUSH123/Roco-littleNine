"""Engine wrapper layer — bridges the pure VM with mutable battle state.

Public API:
    from engine import SkillCompiler, CompiledSkill
    from engine import build_ctx
    from engine import JournalReplayer, Observer, ObserverRegistry
    from engine import BattleVMEngine, SkillExecutionResult
"""

from backend.vm.compiler.skill_compiler import SkillCompiler
from backend.vm.ir_skill import CompiledSkill

from .battle import BattleVMEngine, SkillExecutionResult
from .observer import Observer, ObserverRegistry
from .replayer import JournalReplayer
from .snapshot import build_ctx
