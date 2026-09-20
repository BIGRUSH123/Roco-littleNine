"""dbg_span3 — 打印 turn=N 到下一 turn 之间的日志行。

用法：env\\python.exe native/tools/dbg_span3.py <log> <turn>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
turn = sys.argv[2]
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
end = next((i for i, l in enumerate(lines) if i > (start or 0) and "actions] turn=" in l), len(lines))
for i in range(start, end):
    print(i, lines[i][:150])
