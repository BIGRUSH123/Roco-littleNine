"""dbg_grep — 提取日志中 rust calc 全行（不截断）。

用法：env\\python.exe native/tools/dbg_grep.py <log路径> <turn> [行数]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
turn = sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 40
lines = log.read_text(encoding="utf-8", errors="replace").splitlines()

capture = False
count = 0
out = []
for ln in lines:
    if "rust actions" in ln:
        capture = f"turn={turn} " in ln
        if capture:
            out.append(ln)
    elif capture and "rust calc" in ln:
        out.append(ln)
        count += 1
        if count >= limit:
            break

dest = log.with_suffix(".grep.txt")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"written {len(out)} lines -> {dest}")
