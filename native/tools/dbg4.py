"""临时调试：检查 A 队 sprite 1 的特性加载效果。"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
s1 = spec["players"][0]["sprites"][1]
print("sprite1 name:", s1["name"], "ability:", s1["ability"], "ability_id:", s1["ability_id"])
tj = Path("data/traits") / f"{s1['ability']}.json"
if tj.exists():
    data = json.loads(tj.read_text(encoding="utf-8"))
    for e in data.get("effects", []):
        print("effect:", json.dumps(e, ensure_ascii=False)[:300])
else:
    print("trait json missing:", tj)
