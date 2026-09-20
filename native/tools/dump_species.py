"""dump_species — 导出 data/sprites/*.json 为 native/species_db.json。

索引语义镜像 backend/common/sprite_db.py：
- by_display: "name（form）"（form 为空则 "name"）→ entry
- by_number: number → [entry, ...]（保序，py 用文件遍历序；此处用文件名排序）

用法：env\\python.exe native/tools/dump_species.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "sprites"
DEST = ROOT / "native" / "species_db.json"


def main() -> None:
    by_display: dict[str, dict] = {}
    by_number: dict[str, list[dict]] = {}
    n = 0
    for jf in sorted(SRC.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = str(data.get("name", "")).strip()
        if not name:
            continue
        form = str(data.get("form", "")).strip()
        number = str(data.get("number", "")).strip()
        attrs = data.get("attributes", [])
        if not isinstance(attrs, list):
            attrs = [str(attrs)]
        bl_skills = data.get("bloodline_skills", {})
        if not isinstance(bl_skills, dict):
            bl_skills = {}
        entry = {
            "name": name,
            "form": form,
            "number": number,
            "hp": int(data.get("hp", 0)),
            "atk": int(data.get("atk", 0)),
            "sp_atk": int(data.get("sp_atk", 0)),
            "def": int(data.get("def", 0)),
            "sp_def": int(data.get("sp_def", 0)),
            "speed": int(data.get("speed", 0)),
            "attributes": [str(a).strip() for a in attrs if str(a).strip()],
            "ability": str(data.get("ability", "")).strip(),
            "ability_id": int(data.get("ability_id", 0) or 0),
            "pre_species": str(data.get("pre_species", "")).strip(),
            "bloodline_skills": {str(k): int(v) for k, v in bl_skills.items()},
        }
        display = f"{name}（{form}）" if form else name
        if display not in by_display:
            by_display[display] = entry
        by_number.setdefault(number, []).append(entry)
        n += 1
    out = {"by_display": by_display, "by_number": by_number}
    DEST.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {DEST} ({n} species, {len(by_number)} numbers)")


if __name__ == "__main__":
    main()
