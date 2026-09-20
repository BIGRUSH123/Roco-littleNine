"""dbg_skill2 — 在 spec 全阵容中找技能并打印原始 JSON。

用法：env\\python.exe native/tools/dbg_skill2.py <spec> <技能名> [精灵名过滤]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
name = sys.argv[2]
only = sys.argv[3] if len(sys.argv) > 3 else ""
for pi, pl in enumerate(spec["players"]):
    for i, sp in enumerate(pl["sprites"]):
        if only and sp["name"] != only:
            continue
        for si, sk in enumerate(sp["skills"]):
            if sk["name"] == name:
                print(f"{'AB'[pi]}[{i}].{si} {name}:")
                print(json.dumps(sk, ensure_ascii=False, indent=1)[:700])
