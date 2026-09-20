# -*- coding: utf-8 -*-
"""native/tools/dump_sprite_files.py — 打印指定精灵文件的关键字段（原始 JSON）。

用法: python native/tools/dump_sprite_files.py 文件路径...
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
out = []
for arg in sys.argv[1:]:
    p = Path(arg)
    data = json.loads(p.read_text(encoding="utf-8"))
    keys = list(data.keys())
    out.append(f"=== {p.name} ===")
    out.append(f"  字段: {keys}")
    for k in ("name", "number", "form", "pre_species", "is_leader", "leader",
              "bloodline", "rarity", "element", "elements", "trait", "traits",
              "hp", "atk", "sp_atk", "def", "sp_def", "speed"):
        if k in data:
            v = data[k]
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)[:160]
            out.append(f"  {k} = {v}")
    n_sk = len(data.get("skills", []))
    out.append(f"  skills: {n_sk} 项; stone_skills: {len(data.get('stone_skills', []))} 项")
    out.append("")

text = "\n".join(out)
Path("native/tools/_sprite_dump.txt").write_text(text, encoding="utf-8")
print("written", len(text))
