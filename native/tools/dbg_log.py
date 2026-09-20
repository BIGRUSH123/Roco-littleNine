"""dbg_log — 打印日志中含某关键字的行的尾部（绕过控制台编码问题）。

用法：env\\python.exe native/tools/dbg_log.py <log> <keyword> [tail_chars] [max_lines]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
kw = sys.argv[2]
tail = int(sys.argv[3]) if len(sys.argv) > 3 else 80
maxl = int(sys.argv[4]) if len(sys.argv) > 4 else 8

raw = log.read_bytes()
text = None
for enc in ("utf-16", "utf-8", "gbk"):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue
if text is None:
    print("decode failed")
    sys.exit(1)

n = 0
for line in text.splitlines():
    if kw in line:
        print(f"[{len(line)}] ...{line[-tail:]}")
        n += 1
        if n >= maxl:
            break
