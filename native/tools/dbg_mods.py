"""dbg_mods — 打印 py 对局某回合后指定精灵的 sprite/skill 修饰符。

用法：env\\python.exe native/tools/dbg_mods.py <spec> <turn> <A|B> <idx>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
turn = int(sys.argv[2])
pi = 0 if sys.argv[3].upper() == "A" else 1
si = int(sys.argv[4])

random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)

sp = battle.players[pi] if hasattr(battle, "players") else None
player = battle.player_a if pi == 0 else battle.player_b
sprite = player.team[si]
print(f"{sprite.name} sprite._modifiers={dict(sprite._modifiers)}")
for i, sk in enumerate(sprite.skills):
    print(f"  [{i}] {sk.name} mods={dict(sk._modifiers)}")
