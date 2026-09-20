# -*- coding: utf-8 -*-
"""探针 2：直接调 Resolver，检查血脉技能是否被 allowed_skills 接受。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

spec = importlib.util.spec_from_file_location("smt", _ROOT / "native/tools/scrape_meta_teams.py")
smt = importlib.util.module_from_spec(spec)
sys.modules["smt"] = smt
spec.loader.exec_module(smt)

from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402

res = smt.Resolver(SPRITE_RANDOM_POOL)
cases = [
    ("巨鼓象", {"name": "普通血脉", "attribute": "普通"}, "休息回复"),
    ("电球咩咩", {"name": "翼血脉", "attribute": "翼"}, "风墙"),
    ("化蝶（幽冥眼的样子）", {"name": "幻血脉", "attribute": "幻"}, "超维投射"),
    ("锤头鹳", {"name": "水血脉", "attribute": "水"}, "水弹枪"),
]
lines = []
for name, blraw, skill in cases:
    bl = res.bloodline_of({"bloodline": blraw})
    allowed = res.allowed_skills(name, bl)
    lines.append(f"{name}: bloodline_of={bl!r} 血脉技={res.bloodline_skill_name(name, bl)!r} "
                 f"skill={skill!r} 允许={skill in allowed} 池内={skill in res.pool_skill_sets.get(name, set())}")
    if skill not in allowed:
        lines.append(f"    索引名存在={name in SPRITE_RANDOM_POOL}  db解析={res.factory.sprite_db.get(name) is not None}")
        lines.append(f"    血脉技能表={dict((res.factory.sprite_db.get(name).bloodline_skills or {})) if res.factory.sprite_db.get(name) else None}")
Path(_ROOT / "_meta_probe2.txt").write_text("\n".join(lines), encoding="utf-8")
print("probe2 -> _meta_probe2.txt")
