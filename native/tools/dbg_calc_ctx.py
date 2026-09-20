"""dbg_calc_ctx — 提取指定回合之后的 rust calc 调试行。

用法：env\\python.exe native/tools/dbg_calc_ctx.py <log路径> <turn>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
turn = sys.argv[2]
lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
# 文本可能来自 UTF-16/UTF-8 混合，先按 utf-8 errors=replace 读过
start = None
for i, ln in enumerate(lines):
    if "rust actions" in ln and f"turn={turn} " in ln:
        start = i
        break
if start is None:
    print("turn marker not found")
    raise SystemExit(0)
for ln in lines[start:]:
    if "rust actions" in ln:
        marker = ln
        break
import re
out = []
capture = False
for ln in lines:
    if "rust actions" in ln:
        capture = f"turn={turn} " in ln
    if capture and ("rust calc" in ln or "rust actions" in ln or "rust exec-vm" in ln):
        out.append(ln)
for ln in out[:40]:
    print(ln)
