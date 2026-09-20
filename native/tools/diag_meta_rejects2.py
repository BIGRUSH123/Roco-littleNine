# -*- coding: utf-8 -*-
"""探针 3：无法解析的精灵名 → db 候选 + 技能集匹配可能性。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.common.nature import NATURE_TABLE  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

db = SimFactory().sprite_db
items = json.loads((_ROOT / "backend/engine/ai/data/scraped_teams.json")
                   .read_text(encoding="utf-8"))["raw"]["items"]

by_skill: dict[str, set[str]] = defaultdict(set)
for n, sks in SPRITE_RANDOM_POOL.items():
    for s in sks:
        by_skill[s].add(n)

bad = []
for it in items:
    for b in (it.get("snapshot") or {}).get("builds") or []:
        raw = (b.get("name") or "").strip()
        if raw in SPRITE_RANDOM_POOL:
            continue
        sp = db.get(raw)
        if sp is not None and sp.display_name() in SPRITE_RANDOM_POOL:
            continue
        bad.append((raw, [s for s in (b.get("selectedSkillNames") or []) if s]))

lines = []
seen = set()
for raw, skills in bad:
    if raw in seen:
        continue
    seen.add(raw)
    # 候选：db 中名字包含该名前 2-3 字
    stem = raw.replace(" ", "")
    cands = [d for d in db._by_display if len(stem) >= 2 and stem[:2] in d][:8]
    # 技能匹配
    from collections import Counter
    c: Counter = Counter()
    for s in skills:
        for n in by_skill.get(s, ()):
            c[n] += 1
    top = c.most_common(4)
    lines.append(f"原始名={raw!r}  技能={skills}")
    lines.append(f"    db 前缀候选: {cands}")
    lines.append(f"    技能匹配: {top}")

# 精灵名里带性格词/角色词的统计
nature_words = list(NATURE_TABLE)
lines += ["", "== 名字含性格词/角色词 =="]
for raw, skills in bad:
    hit = [w for w in nature_words if w in raw] + [w for w in ("物攻", "魔攻", "速度", "纯肉", "肉", "极") if w in raw]
    if hit:
        lines.append(f"  {raw!r} → {hit}")

Path(_ROOT / "_meta_rejects2.txt").write_text("\n".join(lines), encoding="utf-8")
print(f"无法解析名 {len(seen)} 种 → _meta_rejects2.txt")
