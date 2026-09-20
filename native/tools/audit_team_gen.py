# -*- coding: utf-8 -*-
"""audit_team_gen.py — 统计随机阵容生成器的分布质量。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

import random  # noqa: E402

from backend.engine.ai.train import _load_sprite_skills, _random_item, _random_teams  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

factory = SimFactory()
sprite_skills = _load_sprite_skills()

# 技能类型缓存
skill_type = {}


def st(name: str) -> str:
    if name not in skill_type:
        p = Path("data/skills") / f"{name}.json"
        skill_type[name] = p.stem and __import__("json").loads(
            p.read_text(encoding="utf-8")).get("skill_type", "") if p.exists() else "?"
    return skill_type[name]


N = 20000
sizes = {1: 0, 2: 0, 3: 0}
no_attack_team = 0        # 全队无任何攻击技能（物攻/魔攻）
no_attack_sprite = 0      # 单只精灵无攻击技能
total_sprites = 0
atk_counts = []
energy_short = 0          # 全队没有能耗≤2 的技能（开局没牌打）

for _ in range(N):
    ta, tb, _ia, _ib = _random_teams(factory, sprite_skills)
    sizes[len(ta)] += 1
    for team in (ta, tb):
        types = []
        for spec in team:
            total_sprites += 1
            tset = [st(s) for s in spec["skills"]]
            n_atk = sum(1 for t in tset if t in ("物攻", "魔攻"))
            atk_counts.append(n_atk)
            if n_atk == 0:
                no_attack_sprite += 1
            types += tset
        if not any(t in ("物攻", "魔攻") for t in types):
            no_attack_team += 1
        if all(True for _ in ()):# 占位
            pass
    # 能耗统计：全队最低能耗
    for team in (ta, tb):
        costs = []
        for spec in team:
            for s in spec["skills"]:
                p = Path("data/skills") / f"{s}.json"
                if p.exists():
                    costs.append(__import__("json").loads(
                        p.read_text(encoding="utf-8")).get("energy_cost", 9))
        if costs and min(costs) > 3:
            energy_short += 1

print(f"对局 {N} 场（两队共 {2*N} 队）")
print(f"队伍规模分布: 1v1={sizes[1]/N:.1%}  2v2={sizes[2]/N:.1%}  3v3={sizes[3]/N:.1%}")
print(f"整队无攻击技能: {no_attack_team}/{2*N} = {no_attack_team/(2*N):.2%}")
print(f"单精灵无攻击技能: {no_attack_sprite}/{total_sprites} = {no_attack_sprite/total_sprites:.2%}")
import statistics
print(f"每精灵攻击技能数: mean={statistics.mean(atk_counts):.2f} "
      f"0个占比={atk_counts.count(0)/len(atk_counts):.2%}")
