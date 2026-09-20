"""dbg_sprite — 打印 spec 中某精灵的完整定义（不含技能 effects）。

用法：env\\python.exe native/tools/dbg_sprite.py <spec_path> <player:A|B> <idx>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
sp = spec["players"][pi]["sprites"][int(sys.argv[3])]
d = {k: v for k, v in sp.items() if k != "skills"}
print(json.dumps(d, ensure_ascii=False, indent=1))
print("skills:", [s["name"] for s in sp.get("skills", [])])
