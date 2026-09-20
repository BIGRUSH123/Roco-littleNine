# -*- coding: utf-8 -*-
"""native/tools/diag_removed2.py — 诊断第二轮移除的 12 个名字的家族明细。

输出每个家族所有条目的 (name, form, appearance, number, pre) 与池内状态。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

NAMES = ["乌拉塔", "化蝶", "卡瓦重", "幽冥眼", "星光狮", "梦悠悠",
         "棋棋", "水泡壳", "海枝枝", "皇家狮鹫", "石冠王蜥", "遁地鼠"]
POOL = json.loads(Path("backend/engine/ai/data/sprite_random_pool.json").read_text(encoding="utf-8"))

groups: dict[str, list[dict]] = {}
for p in sorted(Path("data/sprites").glob("*.json")):
    if p.stem.startswith("_"):
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("name") in NAMES:
        groups.setdefault(d["name"], []).append(d)

out: list[str] = []
for name in NAMES:
    entries = groups.get(name, [])
    out.append(f"===== {name} =====")
    for d in entries:
        disp = f"{d['name']}（{d['appearance']}）" if d.get("appearance") else d["name"]
        out.append(
            f"  {d.get('name'):<8} form={d.get('form','') or '(空)':<12} "
            f"appearance={d.get('appearance','') or '(空)':<14} "
            f"number={d.get('number','')!s:<4} pre={d.get('pre_species','') or '-'!s:<5} "
            f"池中={disp in POOL}")
    # 同编号所有条目（看整个家族）
    numbers = {str(d.get("number", "")).strip() for d in entries}
    for num in sorted(numbers):
        siblings = []
        for p in sorted(Path("data/sprites").glob("*.json")):
            if p.stem.startswith("_"):
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            if str(d.get("number", "")).strip() == num:
                disp = f"{d['name']}（{d['appearance']}）" if d.get("appearance") else d["name"]
                siblings.append(f"{d['name']}·{d.get('form','') or '-'}·{d.get('appearance','') or '-'}·pre={d.get('pre_species','') or '-'}·池中={disp in POOL}")
        out.append(f"  [同编号 #{num}] " + " | ".join(siblings))
    out.append("")

text = "\n".join(out)
Path("native/tools/_removed2_diag.txt").write_text(text + "\n", encoding="utf-8")
print("written", len(text))
