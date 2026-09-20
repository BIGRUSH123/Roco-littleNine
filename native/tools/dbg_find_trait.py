"""dbg_find_trait — 打印指定名字的特性 JSON。

用法：env\\python.exe native/tools/dbg_find_trait.py 盲拧
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

name = sys.argv[1]
p = ROOT / "data" / "traits" / f"{name}.json"
if p.exists():
    print(p.read_text(encoding="utf-8"))
else:
    print("not found:", p)
