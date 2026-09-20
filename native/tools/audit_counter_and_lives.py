# -*- coding: utf-8 -*-
"""native/tools/audit_counter_and_lives.py — 对着"职业复盘的胜负因素"体检我们的专家。

三份洛神杯复盘的共性（`wiki/对局记录/单局记录/*.md`）：
  ① **应对先读**（读中对手技能类别，胜率基础；有场次是 2:2 vs 0:3）
  ② **心力管理**（"叠 5 层中毒值不了 1 心"、"特性双刃剑 3心→2心→0心终结比赛"）
  ③ **出手权**（速度线 + 先手技；22 回合里出手顺序被改了 9 次）
  ④ **补刀纪律**（"打至 5% 却聚能"→ 被反杀）
  ⑤ **印记运营**（叠层→引爆；星陨换人不消失）

本工具量化 ①②③：逐决策记「我方技能有没有应对关系 / 对手实际类别 / 双方心力 / 是否在
1 心时做了必死交换」等，并按 应对关系、心力、印记层数 分桶统计。

用法：
    env\\python.exe native/tools/audit_counter_and_lives.py --games 150
    env\\python.exe native/tools/audit_counter_and_lives.py --games 150 --baseline   # 会话前版本
"""
from __future__ import annotations

import argparse
import collections
import random
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai import train as T  # noqa: E402
from backend.engine.ai.determinism import ensure_hash_seed  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.sim import tactics  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.battleskill import SkillUse  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.resolver import SkillResolver  # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=150)
    ap.add_argument("--baseline", action="store_true", help="用会话前的 agent 版本")
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--seed", type=int, default=2026)
    return ap.parse_args()


def _load_baseline() -> type:
    out = subprocess.run(["git", "show", "HEAD:backend/sim/agent_v2.py"],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    src = out.stdout.replace("from .agent import ELEMENTAL_BLOODLINES",
                             "from backend.common.constants import ELEMENTAL_BLOODLINES")
    name = "backend.sim._baseline_counter_audit"
    mod = types.ModuleType(name)
    mod.__package__ = "backend.sim"
    mod.__file__ = "<git HEAD:agent_v2.py>"
    sys.modules[name] = mod
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod.RuleAgentV2


def _dmg(battle, a, d, sk, team) -> int:
    if sk is None or d is None or d.current_hp <= 0:
        return 0
    v, _ = battle._resolver.calc_damage(a, d, SkillUse(battle_skill=sk), battle.globals,
                                        attacker_team=team)
    return v


def _kind(sk) -> str:
    return "攻击" if sk.is_attack else ("防御" if sk.is_defense else "状态")


def _stacks(battle, team: str) -> int:
    """该侧星陨印记层数（0 = 没有）。"""
    try:
        for mark in battle.globals.mark_effects.get(team, []) or []:
            if getattr(mark, "name", "") == "星陨印记":
                return int(getattr(mark, "stacks", 0) or 0)
    except AttributeError:
        pass
    return 0


def main() -> None:
    ensure_hash_seed()
    args = parse_args()
    AgentCls = _load_baseline() if args.baseline else RuleAgentV2
    factory = SimFactory()
    meta = load_meta_teams()
    rng = random.Random(args.seed)
    random.seed(args.seed)
    st = collections.Counter()
    mark_layers: list[int] = []

    for g in range(args.games):
        meta_i = rng.randrange(len(meta)) if (meta and rng.random() < args.meta_frac) else None
        if meta_i is not None:
            sa, _ = spec_from_team(meta[meta_i], rng)
            sb, _ = spec_from_team(meta[meta_i], rng)
            ia, ib = item_from_team(meta[meta_i], sa), item_from_team(meta[meta_i], sb)
            stra = strategy_from_team(meta[meta_i], rng)
        else:
            sa, sb, ia, ib = T._random_teams(factory, dict(SPRITE_RANDOM_POOL),
                                             optimal_frac=0.95, meta_frac=0.0)
            stra = TeamStrategy(default=SpriteStrategy())
        p1 = factory.build_player("A", sa, item=ia)
        p2 = factory.build_player("B", sb, item=ib)
        battle = factory.build_battle(p1, p2)
        a1 = AgentCls("A", p1, strategy=stra)
        a2 = AgentCls("B", p2, strategy=stra)

        pending: dict[str, dict] = {}
        for team, agent in (("A", a1), ("B", a2)):
            real = agent.choose_action

            def spy(b, _t=team, _a=agent, _real=real):
                p = _a.player
                s, opp_player = p.active, b.get_opponent(_t)
                opp = opp_player.active
                opp_team = tactics.opponent_team(_t)
                table = [(i, _dmg(b, s, opp, sk, _t), sk.energy_cost)
                         for i, sk in enumerate(s.skills)
                         if sk.is_attack and sk.cooldown <= 0 and not sk.sealed
                         and sk.energy_cost <= s.energy]
                act = _real(b)
                pending[_t] = {
                    "s": s, "opp": opp, "opp_team": opp_team, "act": act,
                    "skills": list(s.skills), "lives": p.lives,
                    "opp_lives": opp_player.lives,
                    "opp_best": max((_dmg(b, opp, s, sk, opp_team) for sk in opp.skills
                                     if sk.is_attack and sk.cooldown <= 0 and not sk.sealed
                                     and sk.energy_cost <= opp.energy), default=0),
                    "hp0": s.current_hp, "ohp0": opp.current_hp,
                    "starfall": _stacks(b, opp_team),
                }
                return act
            agent.choose_action = spy

        turns = 0
        while not battle.is_finished and turns < args.max_turns:
            pending.clear()
            rec = battle.execute_turn(a1, a2)
            turns += 1
            for team, other in (("A", rec.action_b), ("B", rec.action_a)):
                info = pending.get(team)
                if not info:
                    continue
                s = info["s"]
                opp = info["opp"]
                my_sk = None
                if info["act"].kind == "skill" and info["act"].skill_index is not None \
                        and info["act"].skill_index < len(info["skills"]):
                    my_sk = info["skills"][info["act"].skill_index]
                their_sk = None
                if other is not None and other.kind == "skill":
                    their_sk = next((sk for sk in opp.skills if sk.name == other.skill_name), None)
                their_cat = _kind(their_sk) if their_sk is not None else (
                    {"switch": "换人", "gather": "聚能", "item": "道具"}.get(
                        other.kind if other else "", "?"))

                st["决策点"] += 1
                st[f"我方类别:{_kind(my_sk) if my_sk else info['act'].kind}"] += 1
                # ① 应对关系（引擎纯函数，双向）
                if my_sk is not None and their_sk is not None:
                    if SkillResolver.resolve_counter(their_sk, my_sk):
                        st["应对关系:我克制它"] += 1
                    if SkillResolver.resolve_counter(my_sk, their_sk):
                        st["应对关系:它克制我"] += 1
                    if not (SkillResolver.resolve_counter(my_sk, their_sk)
                            or SkillResolver.resolve_counter(their_sk, my_sk)):
                        st["应对关系:无"] += 1
                # 我方可带但没选的应对机会：某个可用技能能反制它实际用的类别
                if their_sk is not None:
                    had_chance = any(
                        sk.cooldown <= 0 and not sk.sealed and sk.energy_cost <= s.energy
                        and SkillResolver.resolve_counter(their_sk, sk)
                        for sk in info["skills"])
                    if had_chance:
                        st["有应对机会"] += 1
                        if my_sk is not None and SkillResolver.resolve_counter(their_sk, my_sk):
                            st["有应对机会:抓住了"] += 1
                            # 抓住了的是哪条腿（防御克攻击 / 攻击克状态 / 状态克防御）
                            st[f"抓住组合:{_kind(my_sk)}克{_kind(their_sk)}"] += 1
                    # 状态腿专属：它举盾（防御）时，我该用**状态技**反制
                    if their_sk.is_defense:
                        st["它用防御技"] += 1
                        status_ok = [sk for sk in info["skills"]
                                     if sk.cooldown <= 0 and not sk.sealed
                                     and sk.energy_cost <= s.energy and sk.is_status]
                        if my_sk is not None and SkillResolver.resolve_counter(their_sk, my_sk):
                            st["它用防御技:我用状态反制"] += 1
                        elif status_ok:
                            st["它用防御技:有状态可用但没用"] += 1
                        else:
                            st["它用防御技:没有可用状态技"] += 1
                # ② 心力：1 心时的必死交换 / 残局运营
                died = s.is_fainted or (info["opp_best"] >= info["hp0"] > 0
                                        and info["act"].kind == "skill")
                if info["lives"] == 1:
                    st["1 心决策点"] += 1
                    if died and info["act"].kind == "skill":
                        st["1 心:做了必死交换"] += 1
                    if info["act"].kind == "switch":
                        st["1 心:撤人保命"] += 1
                # ③ 印记层数
                mark_layers.append(info["starfall"])
                if info["starfall"] >= 3:
                    st["我侧星陨≥3层"] += 1
                    if my_sk is not None and my_sk.is_attack and my_sk.element != "幻":
                        st["星陨≥3层:用非幻攻击引爆"] += 1

    total = max(1, st["决策点"])
    print(f"=== {args.games} 局 / {st['决策点']} 决策点（{'会话前版本' if args.baseline else '当前版本'}）===")
    for k, v in st.most_common():
        if k == "决策点":
            continue
        print(f"  {k:<22} {v:>7}  ({v / total:.1%})")
    if mark_layers:
        avg = sum(mark_layers) / len(mark_layers)
        print(f"  我侧星陨印记平均层数: {avg:.2f}（样本 {len(mark_layers)}）")


if __name__ == "__main__":
    main()

