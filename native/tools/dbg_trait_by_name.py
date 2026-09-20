"""dbg_trait_by_name — 通过精灵特性名定位 trait JSON 并打印。

用法：env\\python.exe native/tools/dbg_trait_by_name.py 盲拧
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

name = sys.argv[1]
ids = json.loads((ROOT / "data" / "traits" / "_ids.json").read_text(encoding="utf-8"))
entry = None
for k, v in ids.get("by_id", {}).items():
    if v.get("name") == name:
        entry = (k, v)
        break
if entry is None:
    by_name = ids.get("by_name", {})
    entry = ("by_name", by_name.get(name))
print("index entry:", entry)
if entry and isinstance(entry[1], dict):
    fname = entry[1].get("file") or entry[1].get("filename")
    if fname:
        p = ROOT / "data" / "traits" / fname
        print("file:", p)
        print(p.read_text(encoding="utf-8"))
