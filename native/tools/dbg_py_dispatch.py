"""dbg_py_dispatch — 打印 _DISPATCH[StatChange] 指向的函数与源码。

用法：env\\python.exe native/tools/dbg_py_dispatch.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.replayer import JournalReplayer  # noqa: E402
from backend.vm.journal import StatChange  # noqa: E402

h = JournalReplayer._DISPATCH.get(StatChange)
print("handler:", h)
print("name:", getattr(h, "__name__", None))
print("module:", getattr(h, "__module__", None))
try:
    print(inspect.getsource(h))
except Exception as e:
    print("no source:", e)

print("--- _apply_stat_change source ---")
print(inspect.getsource(JournalReplayer._apply_stat_change))
