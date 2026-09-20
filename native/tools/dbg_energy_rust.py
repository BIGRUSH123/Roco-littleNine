"""dbg_energy_rust — 提取指定回合的 rust energy/exec 调试行。

用法：env\\python.exe native/tools/dbg_energy_rust.py <log路径> <turn>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

log = Path(sys.argv[1])
turn = sys.argv[2]
lines = log.read_text(encoding="utf-8", errors="replace").splitlines()

capture = False
out = []
for ln in lines:
    if "rust actions" in ln:
        capture = f"turn={turn} " in ln
        if capture:
            out.append(ln)
    elif capture and any(k in ln for k in ("rust energy", "rust exec-vm", "rust perm persist")):
        out.append(ln)

dest = log.with_suffix(".erg.txt")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"written {len(out)} lines -> {dest}")
