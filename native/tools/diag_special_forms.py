# -*- coding: utf-8 -*-
"""native/tools/diag_special_forms.py — 对每个受影响的编号，摊开全部条目面板。

判断依据：
  - 自引用 pre_species (pre == 自己的 number) → 数据标记"同编号的特殊/强化形态"
  - 面板对比 → 判断它是否真的更强（首领/觉醒），还是与普通形态同级（仅外观）

输出: native/tools/_special_forms.txt
"""
from __future__ import annotations

import json
from pathlib import Path

NUMBERS = ["11", "20", "40", "65", "78", "120", "189", "190", "191", "192", "238"]
SPRITES_DIR = Path("data/sprites")

rows = []
for path in SPRITES_DIR.glob("*.json"):
    if path.stem.startswith("_"):
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    rows.append(data)

out: list[str] = []
for num in NUMBERS:
    entries = [d for d in rows if str(d.get("number", "")).strip() == num]
    out.append(f"===== #{num} =====")
    for d in sorted(entries, key=lambda x: (x.get("name", ""), x.get("form", ""))):
        pre = str(d.get("pre_species", "")).strip()
        self_ref = "★自引用" if pre == num else ("(普通)" if not pre or int(pre) < int(num) else "(指向他号)")
        stats = (f"hp={d.get('hp')} atk={d.get('atk')} spa={d.get('sp_atk')} "
                 f"def={d.get('def')} spd={d.get('sp_def')} spe={d.get('speed')}")
        out.append(f"  {d.get('name', '?'):<10} form={d.get('form', '') or '(空)':<14} "
                   f"pre={pre or '-':<4}{self_ref:<10} {stats}")
    out.append("")

text = "\n".join(out)
Path("native/tools/_special_forms.txt").write_text(text, encoding="utf-8")
print("written", len(text))
