# -*- coding: utf-8 -*-
"""探针 5：gameTeamCode 的「魔法」行分布 + 首领血脉队伍的魔法选择。

用来判断站点阵容是否指定道具（进化之力 / 愿力），供 meta_teams 记录 item。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

items = json.loads((_ROOT / "backend/engine/ai/data/scraped_teams.json")
                   .read_text(encoding="utf-8"))["raw"]["items"]
lines = []
magic = Counter()
boss_magic = Counter()
n_code = 0
for it in items:
    t = (it.get("snapshot") or {}).get("team") or {}
    code = t.get("gameTeamCode") or ""
    if code:
        n_code += 1
    m = re.search(r"魔法[：:]\s*([^\n#]*)", code)
    has_boss = any(((b.get("bloodline") or {}).get("name") == "首领血脉")
                   for b in (it.get("snapshot") or {}).get("builds") or [])
    if m:
        val = m.group(1).strip()
        magic[val] += 1
        if has_boss:
            boss_magic[val] += 1
    else:
        magic["<无魔法行>"] += 1
        if has_boss:
            boss_magic["<无魔法行>"] += 1

lines.append(f"含 gameTeamCode 的队伍 {n_code}/{len(items)}")
lines.append(f"魔法行分布: {magic.most_common()}")
lines.append(f"首领血脉队伍的魔法行: {boss_magic.most_common()}")

# 道具名 → 本项目 Item 名
from backend.sim.player import Item  # noqa: E402

lines.append(f"本项目道具: 进化之力={Item.leader()} 愿力={Item.wish()}")
try:
    from backend.engine.ai.train import _random_item  # noqa: E402
    import inspect  # noqa: E402
    lines.append("_random_item 源码:\n" + inspect.getsource(_random_item))
except Exception as exc:  # noqa: BLE001
    lines.append(f"_random_item 读取失败: {exc}")

# 技能行与 builds 是否一致
mismatch = 0
checked = 0
for it in items:
    t = (it.get("snapshot") or {}).get("team") or {}
    code = t.get("gameTeamCode") or ""
    names = [b.get("name", "").strip() for b in (it.get("snapshot") or {}).get("builds") or []]
    found = re.findall(r"^#\s*([^：:#]+)[：:]", code, flags=re.M)
    found = [x.strip() for x in found if x.strip() not in ("魔法",)]
    if found:
        checked += 1
        if found[:6] != names[:6]:
            mismatch += 1
lines.append(f"技能行 vs builds 名称：校验 {checked} 队，不一致 {mismatch} 队")

Path(_ROOT / "_meta_item_probe.txt").write_text("\n".join(lines), encoding="utf-8")
print("probe5 -> _meta_item_probe.txt")
