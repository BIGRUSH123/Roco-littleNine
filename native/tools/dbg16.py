"""临时调试16：dump A2 海枝果的 ability 与 trait JSON。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
a2 = spec["players"][0]["sprites"][2]
print("A2 name:", a2.get("name"), "ability:", a2.get("ability"), "ability_id:", a2.get("ability_id"))
tj = Path("data/traits") / f"{a2.get('ability')}.json"
if tj.exists():
    data = json.loads(tj.read_text(encoding="utf-8"))
    print(json.dumps(data, ensure_ascii=False, indent=1))
else:
    print("trait json missing:", tj)
    # 找同 ID
    for p in Path("data/traits").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("id") == a2.get("ability_id"):
            print("found by id:", p.name)
            print(json.dumps(d, ensure_ascii=False, indent=1))
            break
