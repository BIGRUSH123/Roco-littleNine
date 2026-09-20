"""dbg_species_fields — 检查 py Species 对象的 attributes/elements 字段。

用法：env\\python.exe native/tools/dbg_species_fields.py [精灵名]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.sim.species import Species  # noqa: E402
import inspect  # noqa: E402

print(inspect.getsourcefile(Species))
sig = [a for a in dir(Species) if not a.startswith("__")]
print("attrs:", sig[:40])

name = sys.argv[1] if len(sys.argv) > 1 else "獠牙猪"
# 从 data 构造一个
import json  # noqa: E402
data = json.loads((ROOT / "data" / "sprites" / "138_獠牙猪.json").read_text(encoding="utf-8"))
try:
    sp = Species.from_dict(data) if hasattr(Species, "from_dict") else Species(**{
        k: data[k] for k in ("number", "name") if k in data
    })
    print("attributes:", getattr(sp, "attributes", "<missing>"))
    print("elements:", getattr(sp, "elements", "<missing>"))
except Exception as e:
    print("construct failed:", e)
    print(inspect.signature(Species.__init__))
