# -*- coding: utf-8 -*-
"""native/tools/diag_meta_rejects.py — 诊断阵容校验失败的根因（技能/精灵解析）。

输出 UTF-8 报告：对每个 (精灵, 技能) 失败项，标注技能是否在 data/skills 落盘、
是否属于该精灵的基础/技能石/血脉技能；对无法解析的精灵名，列出 db 中的近似候选。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.common.skill_trait_ids import SKILL_ID_TO_NAME  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

skill_dir = _PROJECT_ROOT / "data" / "skills"
on_disk = {p.stem for p in skill_dir.glob("*.json") if not p.stem.startswith("_")}
factory = SimFactory()
db = factory.sprite_db

items = json.loads((_PROJECT_ROOT / "backend/engine/ai/data/scraped_teams.json")
                   .read_text(encoding="utf-8"))["raw"]["items"]

skill_fail: list[tuple[str, str, str, str]] = []   # (精灵, 技能, 定位, 血脉)
name_fail: list[str] = []
low_skill: list[tuple[str, str, int]] = []
marks_fail: list[tuple[str, str, str]] = []

for it in items:
    snap = it.get("snapshot") or {}
    for b in snap.get("builds") or []:
        raw = (b.get("name") or "").strip()
        skills = [s for s in (b.get("selectedSkillNames") or []) if s]
        bl = ((b.get("bloodline") or {}).get("name") or "").replace("血脉", "")
        # 精灵解析（只按名字/db，命中不了记为解析失败）
        disp = None
        if raw in SPRITE_RANDOM_POOL:
            disp = raw
        else:
            sp = db.get(raw)
            if sp is not None and sp.display_name() in SPRITE_RANDOM_POOL:
                disp = sp.display_name()
        if disp is None:
            name_fail.append(raw)
            continue
        species = db.get(disp)
        pool_set = set(SPRITE_RANDOM_POOL[disp])
        bl_skill_id = (species.bloodline_skills or {}).get(bl) if species else None
        bl_skill = SKILL_ID_TO_NAME.get(int(bl_skill_id), "") if bl_skill_id else ""
        for sk in skills:
            if sk in pool_set:
                continue
            where = []
            if sk in on_disk:
                where.append("落盘")
            if sk == bl_skill:
                where.append(f"血脉技({bl})")
            if bl_skill and sk != bl_skill:
                where.append(f"该血脉技={bl_skill}")
            skill_fail.append((disp, sk, "/".join(where) or "无", bl))
        if len(skills) < 3:
            low_skill.append((snap.get("team", {}).get("name", "?"), disp, len(skills)))
        m = b.get("statMarks") or {}
        if len(m.get("plusStats") or []) != 3 or not m.get("minusStat"):
            marks_fail.append((disp, str(m.get("plusStats")), str(m.get("minusStat"))))

lines = ["== 技能不在池内（按技能聚合）=="]
agg: dict[str, Counter] = defaultdict(Counter)
for name, sk, where, bl in skill_fail:
    agg[sk][where] += 1
for sk, c in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
    lines.append(f"  {sk:<10} ×{sum(c.values()):<3} {dict(c)}  在 data/skills={sk in on_disk}")

lines += ["", "== 技能失败明细（前 40）=="]
for name, sk, where, bl in skill_fail[:40]:
    lines.append(f"  {name:<18} {sk:<10} {where} 血脉={bl}")

lines += ["", "== 精灵名无法解析 =="]
for nm in sorted(set(name_fail)):
    lines.append(f"  {nm}")
    # 近似候选：名字前缀匹配 db 显示名
    stem = nm.split("（")[0].split(" ")[0][:3]
    cands = [d for d in db._by_display if stem and (stem in d or d.startswith(nm[:2]))][:6]
    if cands:
        lines.append(f"      db 近似: {cands}")

lines += ["", "== 技能数 < 3 =="]
for t, n, c in low_skill:
    lines.append(f"  [{t}] {n} {c} 个")

lines += ["", "== statMarks 异常 =="]
for n, plus, minus in sorted(set(marks_fail)):
    lines.append(f"  {n:<18} plus={plus} minus={minus}")

Path(_PROJECT_ROOT / "_meta_rejects.txt").write_text("\n".join(lines), encoding="utf-8")
print(f"技能失败 {len(skill_fail)} 项 / {len(agg)} 种；名称失败 {len(set(name_fail))} 种；"
      f"技能数不足 {len(low_skill)}；标记异常 {len(set(marks_fail))}")
print("报告 → _meta_rejects.txt")
