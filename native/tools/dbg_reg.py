"""dbg_reg — 打印日志中 t19→t20 区间的注册表快照与候选/注销行。

用法：env\\python.exe native/tools/dbg_reg.py <log> <turn_from> <turn_to>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
t0, t1 = sys.argv[2], sys.argv[3]
raw = log.read_bytes()
text = None
for enc in ("utf-16", "utf-8", "gbk"):
    try:
        text = raw.decode(enc)
        break
    except UnicodeDecodeError:
        continue
lines = (text or "").splitlines()
start = next((i for i, l in enumerate(lines) if f"actions] turn={t0}" in l), 0)
end = next((i for i, l in enumerate(lines) if f"actions] turn={t1}" in l), len(lines))
for l in lines[start:end]:
    if any(k in l for k in ("registry]", "candidates] trigger=post_entry", "unregister")):
        print(l[:160])
