"""dbg_find_skill — 打印指定名字的技能定义 JSON。

用法：env\\python.exe native/tools/dbg_find_skill.py 齿轮扭矩
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

name = sys.argv[1]
seen = set()
for f in sorted((ROOT / "data" / "related").glob("*精灵技能.json")):
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(data, list):
        continue
    for s in data:
        if isinstance(s, dict) and s.get("name") == name:
            key = json.dumps(s, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                print(f"--- {f.name}")
                print(json.dumps(s, ensure_ascii=False, indent=1))
