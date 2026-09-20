"""dbg_skill — 打印 spec 中某精灵的某个技能原始 JSON。

用法：env\\python.exe native/tools/dbg_skill.py <spec> <player:A|B> <sprite_idx> <skill_idx>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
sp = spec["players"][pi]["sprites"][int(sys.argv[3])]
sk = sp["skills"][int(sys.argv[4])]
print(json.dumps(sk, ensure_ascii=False)[:600])
