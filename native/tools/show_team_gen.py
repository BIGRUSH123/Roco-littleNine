# -*- coding: utf-8 -*-
"""show_team_gen.py — 演示随机阵容/技能/道具的生成结果。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

import random  # noqa: E402

random.seed(7)
from backend.engine.ai.train import _load_sprite_skills, _random_item, _random_teams  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

sprite_skills = _load_sprite_skills()
factory = SimFactory()

team_a, team_b, item_a, item_b = _random_teams(factory, sprite_skills)

print(f"池子: {len(sprite_skills)} 只精灵, 技能目录 data/skills/*.json")
print(f"道具: A={item_a.name}(uses={item_a.max_uses},cd={item_a.cooldown_turns}) "
      f"B={item_b.name}")
for side, team in (("A", team_a), ("B", team_b)):
    print(f"── {side} 队（{len(team)} 只）")
    for spec in team:
        sprite = factory.build_sprite(spec["name"], spec["skills"],
                                      nature=spec["nature"], iv=spec["iv"])
        print(f"  {sprite.name:<6} 属性={sprite.species.elements} 血脉={sprite.bloodline} "
              f"特性={sprite.species.ability}")
        print(f"    性格={spec['nature']}  IV三项=10: "
              f"{[k for k, v in spec['iv'].items() if v > 0]}")
        print(f"    六维={sprite.initial_stats}")
        print(f"    技能({len(sprite.skills)}): {[s.name for s in sprite.skills]}")
        bl = sprite.bloodline_skills.get(sprite.bloodline)
        print(f"    血脉技能池[{sprite.bloodline}] = id {bl}")

p1 = factory.build_player("A", team_a, item=item_a)
p2 = factory.build_player("B", team_b, item=item_b)
battle = factory.build_battle(p1, p2)
print()
print(f"battle 构建成功: traits 已注册 = {len(battle._vm_engine.registry.observers)} 个观察者")
