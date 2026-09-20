# -*- coding: utf-8 -*-
"""探针 6：首领形态候选数量分布（动作 17-21 只有 5 个槽位）。"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.sim.factory import SimFactory  # noqa: E402
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402

db = SimFactory().sprite_db
lines = []
by_family: dict[str, list] = {}
for path in db._by_number.values():
    for p in path:
        s = db._read_one(p)
        if s is None or "首领" not in (s.form or ""):
            continue
        by_family.setdefault(s.number, []).append(s)

counts = Counter()
lines.append(f"有首领形态的编号族: {len(by_family)}")
for num, boss_list in sorted(by_family.items()):
    counts[len(boss_list)] += 1
lines.append(f"每族首领形态数分布: {sorted(counts.items())}")
lines.append("")
lines.append("== 多首领形态族（>1）==")
for num, boss_list in sorted(by_family.items()):
    if len(boss_list) > 1:
        base = db._by_number[num][0]
        b = db._read_one(base)
        lines.append(f"  #{num} {b.name if b else '?'} 基础外观={getattr(b, 'appearance', '')!r} → "
                     + ", ".join(f"{s.name}（{s.appearance}）" for s in boss_list))
        # 同编号全部条目
        alls = [db._read_one(p) for p in db._by_number[num]]
        lines.append("     同编号全部: " + ", ".join(
            f"{s.name}[form={s.form!r},app={s.appearance!r}]" for s in alls if s))

out = _ROOT / "_boss_variants.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"首领族 {len(by_family)}；每族形态数分布 {sorted(counts.items())} → _boss_variants.txt")
