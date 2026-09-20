"""dbg_find2 — 在指定目录内查找含全部片段的 JSON 文件（并打印片段上下文）。

用法：env\\python.exe native/tools/dbg_find2.py <dir:traits|skills> <片段...> [--show]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = Path(__file__).resolve().parents[2]
where = sys.argv[1]
pats = [a for a in sys.argv[2:] if a != "--show"]
show = "--show" in sys.argv
d = root / "data" / where
for f in sorted(d.glob("*.json")):
    if f.name == "_ids.json":
        continue
    text = f.read_text(encoding="utf-8")
    if all(p in text for p in pats):
        print(f"=== {f.name} ({len(text)}B)")
        if show:
            print(text[:800])
