"""dbg_ids_lookup — 通过名字在 _ids.json 找 id，再找 trait 文件并打印。

用法：env\\python.exe native/tools/dbg_ids_lookup.py <特性名>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

name = sys.argv[1]
ids = json.loads((ROOT / "data" / "traits" / "_ids.json").read_text(encoding="utf-8"))
tid = ids.get("ids", {}).get(name)
print(f"{name} -> id {tid}")
by_id = ids.get("by_id", {})
entry = by_id.get(str(tid)) if tid else None
if entry:
    fname = entry.get("file") or entry.get("filename")
    print("entry:", entry)
    if fname:
        p = ROOT / "data" / "traits" / fname
        print("file:", p)
        print(p.read_text(encoding="utf-8"))
else:
    # 名字即文件名
    p = ROOT / "data" / "traits" / f"{name}.json"
    if p.exists():
        print("file:", p)
        print(p.read_text(encoding="utf-8"))
    else:
        print("no file found")
