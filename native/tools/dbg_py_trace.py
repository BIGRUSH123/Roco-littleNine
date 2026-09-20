"""dbg_py_trace — 打印 py 侧指定回合指定队伍技能执行的 journal 与关键 ctx。

用法：env\\python.exe native/tools/dbg_py_trace.py <spec_id> <turn> <A|B>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

sid, turn_s, team_s = sys.argv[1], sys.argv[2], sys.argv[3]
turn = int(turn_s)

spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()

# 挂钩 execute_skill 打印 journal
from backend.engine.battle import BattleVMEngine  # noqa: E402

orig = BattleVMEngine.execute_skill


def patched(self, self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs):
    res = orig(self, self_sprite, opp_sprite, self_skill, opp_skill, globals_, **kwargs)
    t = kwargs.get("team", "?")
    tn = kwargs.get("turn", -1)
    if str(tn) == turn_s and str(t) == team_s:
        ctx = res.ctx
        print(f"=== team={t} turn={tn} skill={getattr(self_skill, 'name', '?')}")
        print(f"    ctx.combo_self={ctx.combo_self} combo_mult={ctx.combo_mult_self} power={ctx.power_self}")
        try:
            print(f"    sprite._modifiers={{k: v for k, v in {self_sprite._modifiers!r}}}")
        except Exception:
            pass
        try:
            print(f"    skill.base.combo={self_skill.base.combo} skill._modifiers={self_skill._modifiers!r}")
        except Exception:
            pass
        for m in res.journal:
            print(f"    {type(m).__name__}: {m}")
    return res


BattleVMEngine.execute_skill = patched

while not battle.is_finished and battle.turn < turn:
    battle.execute_turn(a, b)
battle.execute_turn(a, b)
