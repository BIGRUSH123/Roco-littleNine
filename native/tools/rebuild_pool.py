# -*- coding: utf-8 -*-
"""native/tools/rebuild_pool.py — 重建精灵随机池缓存。

data/sprites + data/skills 更新后，sprite_random_pool.json（缓存）不会
自动重扫（存在即加载）。本脚本强制重建并输出差异与构建性校验：
  1. 与旧缓存对比：新增/移除/技能数变化；
  2. 每只精灵 StatsCalc 可计算（种族数据可解析）；
  3. 每个技能 JSON 可解析（捕获新增技能的数据错误）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data import sprite_random_pool as pool_mod
from backend.common.formulas import StatsCalc
from backend.sim.factory import SimFactory

POOL_FILE = pool_mod._POOL_FILE
old = json.loads(POOL_FILE.read_text(encoding="utf-8")) if POOL_FILE.exists() else {}
new = pool_mod._build_pool()

added = sorted(set(new) - set(old))
removed = sorted(set(old) - set(new))
skill_changed = sorted(
    n for n in set(new) & set(old)
    if sorted(new[n]) != sorted(old[n])
)

print(f"旧池 {len(old)} 只 → 新池 {len(new)} 只")
print(f"新增 {len(added)}: {added[:20]}{' ...' if len(added) > 20 else ''}")
print(f"移除 {len(removed)}: {removed[:20]}{' ...' if len(removed) > 20 else ''}")
print(f"技能列表变化 {len(skill_changed)}: {skill_changed[:10]}")

# ── 构建性校验 ──
factory = SimFactory()
sc = StatsCalc()
bad_stats, bad_skills, empty_sk = [], [], []
skills_dir = Path("data/skills")
for name, skills in new.items():
    species = factory.sprite_db.get(name)
    try:
        sc.compute(species)
    except Exception as exc:  # noqa: BLE001
        bad_stats.append((name, f"{type(exc).__name__}: {exc}"))
    if not skills:
        empty_sk.append(name)
    for sk in skills:
        p = skills_dir / f"{sk}.json"
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            bad_skills.append((name, sk, f"{type(exc).__name__}: {exc}"))

print(f"\n校验: 面板异常 {len(bad_stats)}  技能文件异常 {len(bad_skills)}  零技能 {len(empty_sk)}")
for row in bad_stats[:10]:
    print("  [面板]", row)
for row in bad_skills[:10]:
    print("  [技能]", row)
for n in empty_sk[:10]:
    print("  [零技能]", n)

if bad_stats or bad_skills:
    print("\n!! 存在异常，未写入缓存。修复数据后重跑。")
    raise SystemExit(1)

POOL_FILE.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n已写入 {POOL_FILE}")
