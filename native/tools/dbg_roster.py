"""dbg_roster — 打印 spec 的双方阵容（名字/特性 id）。

用法：env\\python.exe native/tools/dbg_roster.py <spec_path>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for k, pl in zip(("A", "B"), spec["players"]):
    print(k)
    for i, sp in enumerate(pl["sprites"]):
        print(f"  [{i}] {sp['name']} ability={sp['ability']} id={sp['ability_id']}")
