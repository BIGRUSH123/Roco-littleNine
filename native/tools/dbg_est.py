"""dbg_est — 用 Python 复算 rust estimate_damage 的输入，与 py calc_damage 对比。

用法：env\\python.exe native/tools/dbg_est.py <spec_path> <team>
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
from backend.sim.battleskill import SkillUse  # noqa: E402
from backend.vm.damage import calc_damage as vm_damage  # noqa: E402
from backend.sim.resolver import _TYPE_CHART as TYPE_CHART  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
team = sys.argv[2].upper()
random.seed(spec["seed"] + 1)
battle = battle_from_spec(spec)
a = RuleAgent("A", battle.player_a)
b = RuleAgent("B", battle.player_b)
la = a.choose_lead(battle)
battle.player_a.active_index = la
lb = b.choose_lead(battle)
battle.player_b.active_index = lb

player = battle.get_player(team)
opp_pi = "B" if team == "A" else "A"
opp = battle.get_opponent(team).active


def rust_estimate(sp, sk, defender):
    """逐条复刻 native/roco-core rule_agent::estimate_damage。"""
    if not sk.is_attack:
        return 0
    st = sk.skill_type
    if st == "物攻":
        ak, dk = "atk", "def"
    elif st == "魔攻":
        ak, dk = "sp_atk", "sp_def"
    elif st == "动态攻击":
        ak = "atk" if sp.atk >= sp.sp_atk else "sp_atk"
        dk = "def" if ak == "atk" else "sp_def"
    else:
        return 0
    atk_base = sp.initial_stats.get(ak, 0)
    def_base = defender.initial_stats.get(dk, 0)
    if atk_base <= 0 or def_base <= 0:
        return 0
    atk_stage = sp._sum_steps(ak) / 10.0
    def_stage = defender._sum_steps(dk) / 10.0
    additive_power = sp._sum_steps("power") * 10  # 无印记
    elem = sk.element or ""
    def_elems = defender.species.elements or tuple(
        e.strip() for e in (defender.species.attributes or "").split(",") if e.strip())
    type_mult = 1.0
    if elem and def_elems:
        for de in def_elems:
            type_mult *= TYPE_CHART.get(elem, {}).get(de, 1.0)
    attrs = sp.species.elements or tuple(
        e.strip() for e in (sp.species.attributes or "").split(",") if e.strip())
    stab = 1.25 if elem and elem in attrs else 1.0
    weather_mult = 1.5 if battle.globals.weather == "rain" and "水" in elem else 1.0
    return vm_damage(
        power=sk.power, atk_base=atk_base, def_base=def_base,
        atk_stage=atk_stage, def_stage=def_stage,
        stab_mult=stab, type_mult=type_mult, weather_mult=weather_mult,
        damage_reduction=0.0, power_mult=1.0, counter_power_mult=1.0,
        additive_power=additive_power, damage_mult=1.0, combo_count=1,
        mark_bonus=0.0,
    )


resolver = battle._resolver
print(f"A lead={la} B lead={lb}; opponent={opp.name}")
for i, sp in enumerate(player.team):
    if sp.is_fainted:
        continue
    py_max = 0
    rs_max = 0
    detail = []
    for sk in sp.skills:
        if not sk.is_attack:
            continue
        py_dmg, _ = resolver.calc_damage(sp, opp, SkillUse(battle_skill=sk),
                                         battle.globals, attacker_team=team)
        rs_dmg = rust_estimate(sp, sk, opp)
        py_max = max(py_max, py_dmg)
        rs_max = max(rs_max, rs_dmg)
        if py_dmg != rs_dmg:
            detail.append(f"{sk.name}({sk.skill_type},p={sk.power},el={sk.element})"
                          f" py={py_dmg} rust={rs_dmg}")
    print(f"  [{i}] {sp.name} py_score={py_max + sp.current_hp * 0.05:.1f} "
          f"rust_score={rs_max + sp.current_hp * 0.05:.1f} "
          f"py_max={py_max} rust_max={rs_max}")
    for sk in sp.skills:
        py_dmg, _ = resolver.calc_damage(sp, opp, SkillUse(battle_skill=sk),
                                         battle.globals, attacker_team=team)
        rs = rust_estimate(sp, sk, opp)
        print(f"      {sk.name}: type={sk.skill_type} power={sk.power} el={sk.element} "
              f"is_attack={sk.is_attack} py={py_dmg} rust_est={rs}")
    for d in detail:
        print("      DIFF", d)
