"""dbg_species — 对比 py 与 rust 种族数据中某精灵的属性。

用法：env\\python.exe native/tools/dbg_species.py <精灵名>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

name = sys.argv[1]

hits = []
for f in sorted((ROOT / "data" / "sprites").glob("*.json")):
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(d, dict) and d.get("name") == name:
        hits.append(d)
for d in hits:
    print(
        f"py {d.get('number')}: hp={d.get('hp')} atk={d.get('atk')} def={d.get('def')} "
        f"sp_atk={d.get('sp_atk')} sp_def={d.get('sp_def')} speed={d.get('speed')}"
    )

db_path = ROOT / "native" / "tools" / "species_db.json"
if db_path.exists():
    db = json.loads(db_path.read_text(encoding="utf-8"))
    items = db.items() if isinstance(db, dict) else enumerate(db)
    for _, d in items:
        if isinstance(d, dict) and d.get("name") == name:
            print(
                f"ru {d.get('number')}: hp={d.get('hp')} atk={d.get('atk')} def={d.get('def')} "
                f"sp_atk={d.get('sp_atk')} sp_def={d.get('sp_def')} speed={d.get('speed')}"
            )
            break
