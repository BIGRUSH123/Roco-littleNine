"""dbg_lines — 打印日志中包含任一关键字组合的行（全文）。

用法：env\\python.exe native/tools/dbg_lines.py <log> <片段1> [片段2 ...] [--max N]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
args: list[str] = []
maxn = 10
it = iter(sys.argv[2:])
for a in it:
    if a == "--max":
        maxn = int(next(it))
        continue
    args.append(a)
raw = log.read_bytes()
text = None
for enc in ("utf-16", "utf-8", "gbk"):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue
n = 0
for line in (text or "").splitlines():
    if all(p in line for p in args):
        print(line)
        n += 1
        if n >= maxn:
            break
if n == 0:
    print("(no match)")
