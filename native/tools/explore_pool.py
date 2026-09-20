# -*- coding: utf-8 -*-
"""导出训练精灵池的面板/属性/技能池，用于挑选原型队。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
from backend.sim.factory import SimFactory
from backend.common.formulas import StatsCalc

factory = SimFactory()
rows = []
for name, skills in SPRITE_RANDOM_POOL.items():
    species = factory.sprite_db.get(name)
    if species is None:
        rows.append({"name": name, "missing": True})
        continue
    fs = StatsCalc().compute(species).final_stats
    rows.append({
        "name": name,
        "elements": list(species.elements),
        "hp": fs["hp"], "atk": fs["atk"], "def": fs["def"],
        "spa": fs["sp_atk"], "spd": fs["sp_def"], "spe": fs["speed"],
        "bulk": fs["hp"] + fs["def"] + fs["sp_def"],
        "offense": max(fs["atk"], fs["sp_atk"]),
        "skills": skills,
    })

rows.sort(key=lambda r: -r.get("offense", 0))
out = Path("native/tools/_pool_dump.json")
out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"pool={len(rows)} sprites -> {out}")
