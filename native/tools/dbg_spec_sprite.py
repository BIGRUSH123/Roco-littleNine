"""dbg_spec_sprite — 打印 spec 中指定精灵的嵌入数据。

用法：env\\python.exe native/tools/dbg_spec_sprite.py <spec_id> <A|B> <idx>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sid = sys.argv[1]
pi = 0 if sys.argv[2].upper() == "A" else 1
si = int(sys.argv[3])
spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
sp = spec["players"][pi]["sprites"][si]
print(json.dumps({k: v for k, v in sp.items() if k != "skills"}, ensure_ascii=False, indent=1))
