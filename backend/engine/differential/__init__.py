"""differential — 引擎差异对拍基础设施。

以当前 Python 引擎为 oracle，录制固定种子对局的逐回合事件与状态摘要，
供 Rust 移植阶段逐位对拍，保证效果行为与洛克王国世界一致不被破坏。
"""

from .recorder import build_seeded_battle, run_recorded, state_digest
from .compare import compare_fixtures

__all__ = [
    'build_seeded_battle',
    'run_recorded',
    'state_digest',
    'compare_fixtures',
]
