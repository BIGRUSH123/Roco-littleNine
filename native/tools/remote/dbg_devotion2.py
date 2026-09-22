# -*- coding: utf-8 -*-
"""探"奉献"机制是否落地：花衣蝶（回合末 +1 随机奉献）／铠甲虫（受击 +1）／飞断（use_devotion）。"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path("/mnt/workspace/roco_remote")
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402

ensure_hash_seed()
factory = SimFactory()


def build(team_a, team_b):
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    b = factory.build_battle(p1, p2)
    a1 = RuleAgentV2("A", p1, strategy=TeamStrategy(default=SpriteStrategy()))
    a2 = RuleAgentV2("B", p2, strategy=TeamStrategy(default=SpriteStrategy()))
    return b, p1, p2, a1, a2


# ① 花衣蝶：回合结束给 1 次随机奉献
b, p1, p2, a1, a2 = build([{"name": "花衣蝶", "skills": ["飞断", "虫群", "晒太阳"]}],
                          [{"name": "草衣虫", "skills": ["猛烈撞击", "防御"]}])
random.seed(7)
for t in range(3):
    b.execute_turn(a1, a2)
    print(f"[花衣蝶] T{t+1} devotion = {dict(p1.devotion)}")

# ② 铠甲虫：每受到 1 次攻击伤害 +1
b, p1, p2, a1, a2 = build([{"name": "铠甲虫", "skills": ["虫结阵", "虫群过境", "倾泻"]}],
                          [{"name": "草衣虫", "skills": ["猛烈撞击"]}])
for t in range(3):
    b.execute_turn(a1, a2)
    print(f"[铠甲虫] T{t+1} devotion = {dict(p1.devotion)}  HP={p1.active.current_hp}")

# ③ 飞断：技能自带 use_devotion
b, p1, p2, a1, a2 = build([{"name": "陨星虫", "skills": ["飞断", "假寐", "虫群"]}],
                          [{"name": "草衣虫", "skills": ["猛烈撞击"]}])
for t in range(3):
    b.execute_turn(a1, a2)
    print(f"[陨星虫-飞断] T{t+1} devotion = {dict(p1.devotion)}")
