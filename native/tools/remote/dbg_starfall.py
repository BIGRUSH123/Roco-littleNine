# -*- coding: utf-8 -*-
"""星陨印记使用情况诊断：AI 到底有没有在"叠印记"。

逐回合打印：双方场上精灵 / 动作 / 双方星陨印记层数；结束给出统计：
  - 印记技**可选**的回合数 vs **实际使用**的回合数
  - 双方层数轨迹、峰值
  - --dump-turn N：在那一步打印规划层各候选打分（带技能名），看它怎么给"叠层"估值

用法: python dbg_starfall.py --team 星陨队 --turns 40 [--dump-turn 12] [--seed 2026]
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path("/mnt/workspace/roco_remote")
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team, load_meta_teams, spec_from_team,
)
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402

ensure_hash_seed()

from backend.engine import morph  # noqa: E402
from backend.sim import plan as plan_mod  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.skill_ir import skill_profile  # noqa: E402


def marks(battle, team: str) -> int:
    try:
        for m in battle.globals.mark_effects.get(team, []) or []:
            if getattr(m, "name", "") == "星陨印记":
                return int(getattr(m, "stacks", 0) or 0)
    except AttributeError:
        pass
    return 0


def mark_skills(battle, sprite) -> list[str]:
    """该精灵手上能叠星陨印记的技能名。"""
    out = []
    for bs in getattr(sprite, "skills", None) or ():
        prof = skill_profile(battle, bs)
        desc = getattr(bs.base, "description", "") or ""
        if "星陨印记" in desc or "星陨" in str(getattr(bs.base, "morph", "") or ""):
            out.append(bs.name)
    return out


def label_action(act, player) -> str:
    """Action（决策对象）→ 可读文本。"""
    kind = getattr(act, "kind", "")
    if kind == "skill":
        s = player.active
        i = getattr(act, "skill_index", -1)
        return s.skills[i].name if 0 <= i < len(s.skills) else f"skill[{i}]"
    if kind == "switch":
        i = getattr(act, "switch_index", -1)
        return f"→{player.team[i].name}" if 0 <= i < len(player.team) else "→?"
    return kind


def label(act, player) -> str:
    """ActionRecord → 可读文本（kind + 技能名/换上谁/聚能/道具）。"""
    name = getattr(act, "skill_name", "") or ""
    if act.kind == "skill":
        return name or "?"
    if act.kind == "switch":
        return f"→{name}"
    return act.kind


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="星陨队")
    ap.add_argument("--turns", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--dump-turn", type=int, default=0)
    a = ap.parse_args()

    factory = SimFactory()
    team = [t for t in load_meta_teams() if t.get("name") == a.team][0]
    rng = random.Random(a.seed)
    random.seed(a.seed)
    sa, _ = spec_from_team(team, rng)
    sb, _ = spec_from_team(team, rng)
    ia, ib = item_from_team(team, sa), item_from_team(team, sb)
    p1 = factory.build_player("A", sa, item=ia)
    p2 = factory.build_player("B", sb, item=ib)
    battle = factory.build_battle(p1, p2)
    st = TeamStrategy(default=SpriteStrategy())
    a1 = RuleAgentV2("A", p1, strategy=st)
    a2 = RuleAgentV2("B", p2, strategy=st)

    avail = {"A": 0, "B": 0}
    used = {"A": 0, "B": 0}
    peak = {"A": 0, "B": 0}
    t = 0
    while not battle.is_finished and t < a.turns:
        # 决策前记录：哪些印记技可用
        pre = {}
        for tag, player, agent in (("A", p1, a1), ("B", p2, a2)):
            s = player.active
            names = mark_skills(battle, s)
            usable = [n for n in names
                      if any(bs.name == n and bs.cooldown <= 0 and not bs.sealed
                             and bs.energy_cost <= s.energy for bs in s.skills)]
            pre[tag] = usable
            if usable:
                avail[tag] += 1
        rec = battle.execute_turn(a1, a2)
        t += 1
        row = []
        for tag, player in (("A", p1), ("B", p2)):
            ar = rec.action_a if tag == "A" else rec.action_b
            act_txt = f"{label(ar, player) if ar else '-'}"
            if pre[tag]:
                hit = act_txt in pre[tag]
                used[tag] += 1 if hit else 0
                act_txt += ("  [叠印记]" if hit else f"  (可选:{'/'.join(pre[tag])})")
            row.append(f"{tag} {player.active.name[:8]:9s} {act_txt}")
        st_a, st_b = marks(battle, "B"), marks(battle, "A")   # 施加在对手身上的层数
        peak["A"] = max(peak["A"], st_a)
        peak["B"] = max(peak["B"], st_b)
        print(f"T{t:02d}  {row[0]:52s} | {row[1]:52s} | 印记(A→B)={st_a:3d} (B→A)={st_b:3d}")
        if a.dump_turn and t == a.dump_turn:
            for tag, player, agent in (("A", p1, a1), ("B", p2, a2)):
                s = player.active
                table = agent._attack_table(battle, s, battle.get_opponent(tag).active)
                cands = agent._plan_candidates(battle, s, battle.get_opponent(tag).active,
                                               table, agent._st(s))
                _picked, info = plan_mod.choose(battle, tag, cands, plies=1,
                                                k_responses=3, rng=random.Random(1))
                vals = info.get("values") or {}
                rows = sorted(((vals.get(str(c), float("nan")), label_action(c, player))
                               for c in cands),
                              reverse=True)
                print(f"     [{tag}] {s.name} HP {s.current_hp}/{s.max_hp} E {s.energy} "
                      f"| 对手印记 {marks(battle, 'B' if tag == 'A' else 'A')} 层")
                for v, name in rows[:6]:
                    print(f"        {v:+.4f}  {name}")
    print(f"\n汇总: 可用印记技的回合 A={avail['A']} B={avail['B']} | "
          f"实际叠了 A={used['A']} B={used['B']} | 层数峰值(A→B {peak['A']}, B→A {peak['B']})")
    print(f"结束: finished={battle.is_finished} turns={t} "
          f"剩A={sum(1 for s_ in p1.team if s_.current_hp > 0)} "
          f"剩B={sum(1 for s_ in p2.team if s_.current_hp > 0)}")


if __name__ == "__main__":
    main()
