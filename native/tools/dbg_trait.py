"""dbg_trait — 按 id 或名字打印特性 JSON。

用法：env\\python.exe native/tools/dbg_trait.py <id|name>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

root = Path(__file__).resolve().parents[2]
target = sys.argv[1]
if target.isdigit():
    ids = json.loads((root / "data/traits/_ids.json").read_text(encoding="utf-8"))
    if isinstance(ids, dict):
        # 可能是 {name: id} 或 {id: [name, ...]}
        name = None
        for k, v in ids.items():
            if isinstance(v, int) and v == int(target):
                name = k
                break
            if isinstance(v, list) and int(target) in v:
                name = k
                break
            if isinstance(v, list) and v and str(target) in [str(x) for x in v]:
                name = k
                break
        if name is None and target in ids:
            name = ids[target]
        if isinstance(name, list):
            print("candidates:", name)
            sys.exit(0)
        print("name:", name)
        target = name
p = root / "data/traits" / f"{target}.json"
print(p.read_text(encoding="utf-8"))
