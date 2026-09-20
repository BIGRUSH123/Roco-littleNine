# -*- coding: utf-8 -*-
"""native/tools/diag_pool_boss.py — 分析首领形态文件对池子的影响。

池子规则: number 出现在"非首领条目"的 pre_species 中 → 该编号整条排除
（设计初衷: 存在更高形态时只保留最终形态）。本脚本按编号分组，
对比"有首领形态文件的编号"是否留在池中，定位规则误触发范围。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

POOL = json.loads(
    Path("backend/engine/ai/data/sprite_random_pool.json").read_text(encoding="utf-8"))
SPRITES_DIR = Path("data/sprites")

rows = []
for path in SPRITES_DIR.glob("*.json"):
    if path.stem.startswith("_"):
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    rows.append({
        "name": data.get("name", path.stem),
        "form": data.get("form", "") or "普通",
        "number": str(data.get("number", "")).strip(),
        "pre": str(data.get("pre_species", "")).strip(),
    })

by_number: dict[str, list[dict]] = {}
for r in rows:
    by_number.setdefault(r["number"], []).append(r)

boss_numbers = [num for num, es in by_number.items()
                if any(e["form"] == "首领形态" for e in es)]
self_ref = [num for num, es in by_number.items()
            if any(e["pre"] == num and e["form"] != "首领形态" for e in es)]

pool_numbers = {e["number"] for e in rows if e["name"] in POOL}
out: list[str] = []
out.append(f"文件 {len(rows)} 个, 编号 {len(by_number)} 个; 池中 {len(POOL)} 只")
out.append(f"有首领形态文件的编号: {len(boss_numbers)}")
out.append(f"存在『自引用 pre_species』变体的编号: {len(self_ref)}")
out.append("")


def entry_str(e: dict) -> str:
    return f"{e['name']}·{e['form']}·pre={e['pre'] or '-'}"


def numkey(num: str) -> int:
    return int(num) if num.isdigit() else 0


out.append("=== 有首领形态、整条编号不在池中 ===")
for num in sorted(boss_numbers, key=numkey):
    if num in pool_numbers:
        continue
    es = by_number[num]
    names = sorted({e["name"] for e in es})
    out.append(f"  #{num} {names}")
    for e in es:
        out.append(f"      {entry_str(e)}")
out.append("")

out.append("=== 存在『自引用 pre_species 的外观变体』的编号（规则误触发源）===")
for num in sorted(self_ref, key=numkey):
    es = by_number[num]
    poisoned = [e for e in es if e["pre"] == num and e["form"] != "首领形态"]
    normal = [e for e in es if e["pre"] != num and e["form"] != "首领形态"]
    out.append(f"  #{num} 在池中={num in pool_numbers}  "
               f"自引用条目={[entry_str(e) for e in poisoned]}")
    out.append(f"       被连带排除的普通形态={[entry_str(e) for e in normal]}")
out.append("")

out.append("=== 有首领形态、普通形态仍在池中（前 15）===")
shown = 0
for num in sorted(boss_numbers, key=numkey):
    if num not in pool_numbers:
        continue
    es = by_number[num]
    pool_names = [e["name"] for e in es if e["name"] in POOL]
    out.append(f"  #{num} 池内={pool_names} 条目={[entry_str(e) for e in es][:6]}")
    shown += 1
    if shown >= 15:
        break

text = "\n".join(out)
Path("native/tools/_pool_boss_diag.txt").write_text(text + "\n", encoding="utf-8")
print("written:", len(text), "chars")
