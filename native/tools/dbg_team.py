"""dbg_team — 解码打印双方阵容（含技能列表）。

用法：env\\python.exe native/tools/dbg_team.py <spec>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for pi, pl in enumerate(spec["players"]):
    for i, sp in enumerate(pl["sprites"]):
        skills = [s["name"] for s in sp["skills"]]
        print(f"{'AB'[pi]}[{i}] {sp['name']} ({sp['ability']}) skills={skills}")
