"""dbg_grep_kw — 提取日志中含关键词的行（含回合上下文）。

用法：env\\python.exe native/tools/dbg_grep_kw.py <log路径> <关键词> [上限]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
kw = sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 40
lines = log.read_text(encoding="utf-8", errors="replace").splitlines()

out = []
current_turn = "?"
for ln in lines:
    if "rust actions" in ln:
        current_turn = ln.split("turn=")[1].split(" ")[0] if "turn=" in ln else "?"
    if kw in ln:
        out.append(f"[t{current_turn}] {ln}")
        if len(out) >= limit:
            break

dest = log.with_suffix(".kw.txt")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"written {len(out)} lines -> {dest}")
