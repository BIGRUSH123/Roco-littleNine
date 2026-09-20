# -*- coding: utf-8 -*-
"""dbg_dmg_ratio.py — 实测随机 6v6 对局的单发伤害/HP 比值与理论击杀回合数。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

import random  # noqa: E402

from backend.engine.ai.train import _load_sprite_skills, _random_item, _random_teams  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.battleskill import SkillUse  # noqa: E402

factory = SimFactory()
sprite_skills = _load_sprite_skills()

ratios = []
for seed in range(6):
    random.seed(seed)
    ta, tb, ia, ib = _random_teams(factory, sprite_skills)
    p1 = factory.build_player("A", ta, item=ia)
    p2 = factory.build_player("B", tb, item=ib)
    battle = factory.build_battle(p1, p2)
    # 每只 A 精灵对 B 每只精灵的最优攻击伤害
    for s_a in p1.team:
        for s_b in p2.team:
            best = 0
            for skill in s_a.skills:
                if skill.skill_type in ("物攻", "魔攻"):
                    try:
                        dmg, _ = battle._resolver.calc_damage(
                            s_a, s_b, SkillUse(battle_skill=skill),
                            battle.globals, attacker_team="A")
                        best = max(best, dmg)
                    except Exception:
                        pass
            if best and s_b.max_hp > 0:
                ratios.append(best / s_b.max_hp)

ratios.sort()
import statistics  # noqa: E402
n = len(ratios)
print(f"配对数: {n}")
print(f"最优单发伤害/目标HP: mean={statistics.mean(ratios):.1%} "
      f"median={ratios[n//2]:.1%} p10={ratios[n//10]:.1%} p90={ratios[int(n*.9)]:.1%}")
hits = [1 / r for r in ratios if r > 0]
print(f"击杀所需命中数: mean={statistics.mean(hits):.1f} median={sorted(hits)[n//2]:.1f}")
