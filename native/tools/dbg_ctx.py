"""dbg_ctx — 打印日志中匹配行的上下文（前后 N 行）。

用法：env\\python.exe native/tools/dbg_ctx.py <log> "<keyword>" [before] [after] [skip]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
kw = sys.argv[2]
before = int(sys.argv[3]) if len(sys.argv) > 3 else 5
after = int(sys.argv[4]) if len(sys.argv) > 4 else 2
skip = int(sys.argv[5]) if len(sys.argv) > 5 else 0

raw = log.read_bytes()
text = None
for enc in ("utf-16", "utf-8", "gbk"):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue
lines = text.splitlines()
hits = 0
for i, line in enumerate(lines):
    if kw in line:
        hits += 1
        if hits <= skip:
            continue
        for j in range(max(0, i - before), min(len(lines), i + after + 1)):
            mark = ">>" if j == i else "  "
            print(f"{mark} {lines[j][:150]}")
        print("---")
        if hits >= skip + 3:
            break
