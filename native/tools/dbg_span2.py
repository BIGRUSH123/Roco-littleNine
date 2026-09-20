"""dbg_span2 — 打印 turn=N 起的日志行（含指定关键词过滤）。

用法：env\\python.exe native/tools/dbg_span2.py <log> <turn> <keyword> [行数]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
turn = sys.argv[2]
kw = sys.argv[3]
n = int(sys.argv[4]) if len(sys.argv) > 4 else 24
raw = log.read_bytes()
text = None
for enc in ("utf-16", "utf-8", "gbk"):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue
lines = (text or "").splitlines()
start = next((i for i, l in enumerate(lines) if f"actions] turn={turn}" in l), None)
if start is None:
    print("turn not found")
else:
    shown = 0
    for i in range(start, min(len(lines), start + 40)):
        print(i, lines[i][:140])
        shown += 1
        if shown >= n:
            break
