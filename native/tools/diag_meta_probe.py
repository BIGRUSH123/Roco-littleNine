# -*- coding: utf-8 -*-
"""探针：给定精灵+血脉，输出池技能集、血脉技能名、allowed 集合的差异。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.common.skill_trait_ids import SKILL_ID_TO_NAME  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

db = SimFactory().sprite_db
items = json.loads((_ROOT / "backend/engine/ai/data/scraped_teams.json")
                   .read_text(encoding="utf-8"))["raw"]["items"]

targets = {"铠甲虫", "电球咩咩", "巨鼓象", "锤头鹳", "化蝶（幽冥眼的样子）"}
seen = set()
lines = []
for it in items:
    for b in (it.get("snapshot") or {}).get("builds") or []:
        raw = (b.get("name") or "").strip()
        sp = db.get(raw)
        disp = raw if raw in SPRITE_RANDOM_POOL else (sp.display_name() if sp else None)
        if disp not in targets or disp in seen:
            continue
        seen.add(disp)
        bl = ((b.get("bloodline") or {}).get("name") or "")
        blkey = bl.replace("血脉", "")
        species = db.get(disp)
        blmap = dict(species.bloodline_skills or {}) if species else {}
        blid = blmap.get(blkey)
        blname = SKILL_ID_TO_NAME.get(int(blid)) if blid is not None else None
        pset = set(SPRITE_RANDOM_POOL.get(disp, []))
        lines.append(f"{disp}  站点血脉名={bl!r} → key={blkey!r}")
        lines.append(f"  species={species.name if species else None} appearance={getattr(species,'appearance',None)}")
        lines.append(f"  bloodline_skills keys={sorted(blmap)}")
        lines.append(f"  该血脉技 id={blid} name={blname}")
        lines.append(f"  selectedSkillNames={b.get('selectedSkillNames')}")
        lines.append(f"  池技能({len(pset)})= {sorted(pset)}")
        lines.append(f"  缺失= {[s for s in (b.get('selectedSkillNames') or []) if s not in pset]}")
        lines.append("")

Path(_ROOT / "_meta_probe.txt").write_text("\n".join(lines), encoding="utf-8")
print(f"探针 {len(seen)} 只 → _meta_probe.txt")
