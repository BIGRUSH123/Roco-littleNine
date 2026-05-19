"""tests/conftest.py — 测试根配置

确保项目根目录在 sys.path 中，使 from backend.xxx import ... 可用。
"""

import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))
