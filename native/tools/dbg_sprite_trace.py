"""dbg_sprite_trace — 逐回合追踪某精灵字段（py vs rust）。

用法：env\\python.exe native/tools/dbg_sprite_trace.py <spec> <player:A|B> <idx> <from> <to>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import battle_from_spec, run_python  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
si = int(sys.argv[3])
lo = int(sys.argv[4]) if len(sys.argv) > 4 else 0
hi = int(sys.argv[5]) if len(sys.argv) > 5 else 10

py_digests, _ = run_python(spec)
result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rust_digests = result["turns"]

for i, (pd, rd) in enumerate(zip(py_digests, rust_digests)):
    if not (lo <= i <= hi):
        continue
    ps = pd["players"][pi]["sprites"][si]
    rs = rd["players"][pi]["sprites"][si]
    mark = "" if ps == rs else "   <<< DIFF"
    print(f"t{i}: py  hp={ps['hp']} e={ps['energy']} chg={ps['charging']} fa={ps['first_action']} "
          f"eff={[e[0] for e in ps['effects']]}{mark}")
    if mark:
        print(f"     rust hp={rs['hp']} e={rs['energy']} chg={rs['charging']} fa={rs['first_action']} "
              f"eff={[e[0] for e in rs['effects']]}")
        # 技能级差异
        for a, b in zip(ps["skills"], rs["skills"]):
            if a != b:
                print(f"     skill {a['name']}: py={a} rust={b}")
        if ps.get("modifiers") != rs.get("modifiers"):
            print(f"     mods py={ps['modifiers']}")
            print(f"     mods ru={rs['modifiers']}")

# ── py 侧逐回合动作与蓄力目标 ──
print("\n=== py 动作/蓄力目标轨迹 ===")
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
for t in range(lo, hi + 1):
    if battle.is_finished or battle.turn >= 150:
        break
    rec = battle.execute_turn(a, b)
    sp = battle.get_player("B").team[si]
    ar = rec.action_b
    kind = getattr(ar, "kind", "") if ar else ""
    sk_nm = getattr(ar, "skill_name", None) if ar else None
    evs = getattr(ar, "events", None) if ar else None
    print(f"t{battle.turn}: B action kind={kind} skill={sk_nm} | "
          f"{sp.name} _charging={getattr(sp, '_charging', False)} "
          f"charged_ref={getattr(getattr(sp, '_charged_skill_ref', None), 'name', None)} "
          f"charged_idx={getattr(sp, '_charged_skill_index', None)}")
    if evs:
        for e in evs:
            print("      ev:", e)
