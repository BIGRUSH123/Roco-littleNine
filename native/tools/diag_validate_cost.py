# -*- coding: utf-8 -*-
"""探针：validate_meta_teams 耗时（怀疑每只精灵新建 SimFactory）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data.meta_teams import load_meta_teams, validate_meta_teams  # noqa: E402
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

t0 = time.perf_counter()
SimFactory()
t1 = time.perf_counter()
print(f"SimFactory() 单次构造: {t1 - t0:.3f}s")

teams = load_meta_teams()
t2 = time.perf_counter()
problems = validate_meta_teams(teams, SPRITE_RANDOM_POOL)
t3 = time.perf_counter()
print(f"validate_meta_teams({len(teams)} 队): {t3 - t2:.2f}s；问题 {len(problems)} 条")

n_variants = sum(len([e] + list(e.get("alts") or [])) for t in teams for e in t["sprites"])
print(f"变体数（= SimFactory 构造次数下限）: {n_variants}")
