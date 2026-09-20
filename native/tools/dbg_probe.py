"""dbg_probe — 跑到指定回合后打印双方 active 的攻防/阶数/修饰符（py 侧）。

用法：env\\python.exe native/tools/dbg_probe.py <spec> <turn>
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
target = int(sys.argv[2])
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
battle.player_a.active_index = a.choose_lead(battle)
battle.player_b.active_index = b.choose_lead(battle)
battle._invalidate_ctx_team_cache()
while not battle.is_finished and battle.turn < target:
    battle.execute_turn(a, b)

for label, player in (("A", battle.player_a), ("B", battle.player_b)):
    for i, s in enumerate(player.team):
        if i != player.active_index:
            continue
        print(f"{label}[{i}] {s.name} hp={s.current_hp}/{s.max_hp} "
              f"atk={s.initial_stats.get('atk')} sp_atk={s.initial_stats.get('sp_atk')} "
              f"def={s.initial_stats.get('def')} sp_def={s.initial_stats.get('sp_def')}")
        for e in s.active_effects:
            print(f"     eff {e.name!r} scope={e.scope} steps={getattr(e, 'steps', None)} "
                  f"ttl={getattr(e, 'ttl', None)} src={getattr(e, 'source', None)}")
        print(f"     stages={dict(s._cached_stages) if not s._effects_dirty else s.get_effects_snapshot()['stages']} "
              f"atk_with_mods={s.atk_with_modifiers} def_with_mods={s.def_with_modifiers} "
              f"moe={getattr(s, '_moe_position', 0)} mods={dict(s._modifiers)}")

# 复算上一回合 A 对 B 的攻击（若存在 水刃 技能）
from backend.sim.battleskill import SkillUse  # noqa: E402

a_act = battle.get_player("A").active
b_act = battle.get_player("B").active
for sk in a_act.skills:
    if sk.name == "水刃":
        dmg, _ = battle._resolver.calc_damage(
            a_act, b_act, SkillUse(battle_skill=sk), battle.globals, attacker_team="A")
        from backend.sim.resolver import SkillResolver
        print("recompute 水刃:", dmg,
              "type_mult:", SkillResolver._get_type_mult(sk, a_act, b_act),
              "stab:", SkillResolver._get_stab(sk, a_act),
              "skill_power:", sk.power,
              "def_base:", b_act.initial_stats.get("def"),
              "atk_base:", a_act.initial_stats.get("atk"))
