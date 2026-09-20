"""dbg_ec — 逐回合打印指定精灵的技能 energy_cost 修饰符（py）。

用法：env\\python.exe native/tools/dbg_ec.py <spec> <player:A|B> <idx> <to_turn>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
si = int(sys.argv[3])
to = int(sys.argv[4])
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()


def dump(tag):
    sp = battle.player_a.team[si] if pi == 0 else battle.player_b.team[si]
    mods = [(i, s.name, dict(s._modifiers), getattr(s, "_transmission", None),
             getattr(s, "replaced_by", None) and s.replaced_by.name) for i, s in enumerate(sp.skills)]
    print(f"[{tag}] {sp.name} sprite_ec={sp._modifiers.get('energy_cost')!r} "
          f"direct_ec={sp._modifiers.get('energy_cost_delta_mult')!r}")
    for i, name, m, tr, rb in mods:
        if m or tr or rb:
            print(f"    [{i}] {name} mods={m} trans={tr} replaced_by={rb}")


dump("t0")
for t in range(to):
    if battle.is_finished:
        break
    battle.execute_turn(a, b)
    print(f"--- after turn {battle.turn} ---")
    dump(f"t{battle.turn}")
