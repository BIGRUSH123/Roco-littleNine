"""dbg_calc — 打印 py 对局中 calc_damage 实际入参（定位伤害分歧）。

用法：env\\python.exe native/tools/dbg_calc.py <spec> <turn> [skill_name]
"""

from __future__ import annotations

import json
import traceback
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.resolver import SkillResolver  # noqa: E402
from backend.vm import damage as vm_damage  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = int(sys.argv[2])
skill_filter = sys.argv[3] if len(sys.argv) > 3 else ""

orig = SkillResolver.calc_damage


def patched(attacker, defender, use, globals_, attacker_team="A"):
    dmg, evs = orig(attacker, defender, use, globals_, attacker_team)
    sk = use.battle_skill
    name = getattr(sk, "name", "")
    if not skill_filter or name == skill_filter:
        print(f"[calc] {attacker.name}(atk={attacker.initial_stats.get('atk')}"
              f",sp_atk={attacker.initial_stats.get('sp_atk')}) → {defender.name}"
              f"(def={defender.initial_stats.get('def')},sp_def={defender.initial_stats.get('sp_def')})"
              f" skill={name} p={getattr(sk, 'power', 0)}"
              f" type_mult={SkillResolver._get_type_mult(sk, attacker, defender)}"
              f" stab={SkillResolver._get_stab(sk, attacker)}"
              f" def_elems={defender.species.elements}"
              f" atk_elems={attacker.species.elements} → {dmg}",
              flush=True)
    return dmg, evs


SkillResolver.calc_damage = staticmethod(patched)

# ── VM 伤害核心（实际执行路径）──
orig_vm = vm_damage.calc_damage


def patched_vm(*args, **kwargs):
    out = orig_vm(*args, **kwargs)
    stack = [f"{f.name}:{f.lineno}" for f in traceback.extract_stack()[-6:-1]]
    print(f"[vm] args={args} kw={kwargs} -> {out} via={stack}", flush=True)
    return out


vm_damage.calc_damage = patched_vm
# hit op 直接绑定了模块级名字 → 同时替换其引用
from backend.vm.ops import hit as vm_hit  # noqa: E402
vm_hit.calc_damage = patched_vm

random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while not battle.is_finished and battle.turn < target:
    battle.execute_turn(a, b)
print("done turn", battle.turn)
