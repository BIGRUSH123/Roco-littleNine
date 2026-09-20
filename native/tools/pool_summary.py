# -*- coding: utf-8 -*-
"""打印精灵池紧凑摘要：按输出/坦度/速度/工具度排序，用于原型队选人。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
rows = json.loads(Path("native/tools/_pool_dump.json").read_text(encoding="utf-8"))
rows = [r for r in rows if not r.get("missing")]

def util(r):
    atk = sum(1 for s in r["skills"] if s and ("击" in s or "波" in s or "光" in s))
    return 0  # placeholder

lines = []
for r in rows:
    sk = ",".join(r["skills"][:8])
    lines.append(
        f"{r['name']:<14} {'/'.join(r['elements']):<5} "
        f"atk={r['atk']:>3} spa={r['spa']:>3} spe={r['spe']:>3} "
        f"hp={r['hp']:>3} def={r['def']:>3} spd={r['spd']:>3} "
        f"bulk={r['bulk']:>4} | {sk}"
    )

out = Path("native/tools/_pool_summary.txt")
out.write_text("\n".join(lines), encoding="utf-8")
print(f"{len(lines)} rows -> {out}")
