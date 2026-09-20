# -*- coding: utf-8 -*-
"""探针 4：popular/builds 载荷结构 + creatureId → 池内名 映射可行性。"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402

raw = json.loads((_ROOT / ".tmp_probe/resp_popular_builds.json").read_text(encoding="utf-8"))
items = raw.get("items", raw) if isinstance(raw, dict) else raw
lines = [f"载荷类型={type(raw).__name__} n={len(items)}"]
lines.append(f"item keys: {sorted(items[0].keys())}")

# snapshot 结构
snap = items[0].get("snapshot") if isinstance(items[0], dict) else None
lines.append(f"snapshot keys: {sorted(snap.keys()) if isinstance(snap, dict) else snap}")
lines.append("首条: " + json.dumps(items[0], ensure_ascii=False)[:800])

# creatureId → 名字（用名字命中池的 build 做映射样本）
cid_to_names: dict[str, Counter] = defaultdict(Counter)
n_cid = 0
for it in items:
    bs = (it.get("snapshot") or {}).get("builds") if isinstance(it.get("snapshot"), dict) else None
    if bs is None:
        b = (it.get("snapshot") or {}).get("build") if isinstance(it.get("snapshot"), dict) else None
        bs = [b] if b else []
    for b in bs:
        if not b:
            continue
        n_cid += 1
        cid = str(b.get("creatureId", ""))
        nm = (b.get("name") or "").strip()
        cid_to_names[cid][nm] += 1

lines.append("")
lines.append(f"build 数={n_cid}  不同 creatureId={len(cid_to_names)}")
# 冲突：同一 creatureId 对应多个名字，其中只有部分命中池
conflict = 0
usable = 0
for cid, c in cid_to_names.items():
    hits = [n for n in c if n in SPRITE_RANDOM_POOL]
    if len(c) > 1:
        conflict += 1
    if hits:
        usable += 1
lines.append(f"有池内名命中的 creatureId={usable}；名字不一致的 creatureId={conflict}")
sample = [(cid, dict(c)) for cid, c in list(cid_to_names.items())[:8]]
lines += [f"  {cid} → {c}" for cid, c in sample]
# 同一 cid 多名字的例子（能验证「改名也能靠 cid 还原」）
lines.append("")
lines.append("== 同 creatureId 多名字示例 ==")
shown = 0
for cid, c in cid_to_names.items():
    hits = [n for n in c if n in SPRITE_RANDOM_POOL]
    others = [n for n in c if n not in SPRITE_RANDOM_POOL]
    if hits and others and shown < 12:
        lines.append(f"  cid={cid}: 池内={hits} 其他={others}")
        shown += 1

Path(_ROOT / "_meta_builds_probe.txt").write_text("\n".join(lines), encoding="utf-8")
print("probe4 -> _meta_builds_probe.txt")
