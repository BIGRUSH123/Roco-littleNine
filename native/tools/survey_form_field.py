# -*- coding: utf-8 -*-
"""native/tools/survey_form_field.py — 统计 form 字段取值分布，为迁移做准备。

输出: native/tools/_form_survey.txt
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SPRITES_DIR = Path("data/sprites")
rows = []
for p in SPRITES_DIR.glob("*.json"):
    if p.stem.startswith("_"):
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    rows.append({
        "file": p.name,
        "name": d.get("name", ""),
        "form": d.get("form", ""),
        "number": str(d.get("number", "")).strip(),
        "pre": str(d.get("pre_species", "")).strip(),
        "has_appearance_key": "appearance" in d,
        "keys": sorted(d.keys()),
    })

out: list[str] = []
out.append(f"文件总数: {len(rows)}")
forms = Counter(r["form"] for r in rows)
out.append(f"form 取值种类: {len(forms)}")
out.append("")
out.append("=== form 取值分布（前 40）===")
for val, cnt in forms.most_common(40):
    out.append(f"  {cnt:>4}  {val!r}")
out.append("")

# 首领相关取值
boss_like = sorted({r["form"] for r in rows if "首领" in r["form"]})
out.append(f"含『首领』字样的 form 取值: {boss_like}")
out.append("")

# 空 form 的条目
empty_form = [r for r in rows if not r["form"]]
out.append(f"form 为空: {len(empty_form)} 个文件")
nonempty = [r for r in rows if r["form"]]
out.append(f"form 非空: {len(nonempty)} 个文件")
out.append("")

# 字段集合（判断是否存在其它可用于区分阶段的字段）
key_sets = Counter(tuple(r["keys"]) for r in rows)
out.append("=== 字段集合分布 ===")
for ks, cnt in key_sets.most_common(10):
    out.append(f"  {cnt:>4}  {list(ks)}")

text = "\n".join(out)
Path("native/tools/_form_survey.txt").write_text(text + "\n", encoding="utf-8")
print("written", len(text))
