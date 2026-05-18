"""Engine wrapper layer — bridges the pure VM with mutable battle state.

Public API:
    from engine import SkillLoader, SkillRecord
    from engine import build_ctx
    from engine import JournalReplayer, Observer, ObserverRegistry
    from engine import BattleVMEngine, SkillExecutionResult
"""

from .skill_loader import SkillLoader, SkillRecord
from .snapshot import build_ctx
from .replayer import JournalReplayer
from .observer import Observer, ObserverRegistry
from .battle import BattleVMEngine, SkillExecutionResult
