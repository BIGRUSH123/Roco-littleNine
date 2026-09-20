"""dbg_find — 在 data/skills + data/traits JSON 中查找含指定片段的文件。

用法：env\\python.exe native/tools/dbg_find.py "<json片段>" [片段2 ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = Path(__file__).resolve().parents[2]
pats = sys.argv[1:]
files = list((root / "data/skills").glob("*.json")) + list((root / "data/traits").glob("*.json"))
for f in files:
    text = f.read_text(encoding="utf-8")
    if all(p in text for p in pats):
        print(f.name)
