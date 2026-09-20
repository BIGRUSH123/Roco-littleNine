# -*- coding: utf-8 -*-
"""native/tools/audit_ruleagent_decisions.py — 决策审计（"蒸馏"用：先找错，再改规则）。

把 RuleAgentV2 的每个决策点记下来：**决策时的可见状态 + 它选了什么 + 它放弃了什么
（候选打分）+ 这一手的事后结果（双方掉血/力倒/是否被应）**，然后用一组"可疑模式"
自动挑出值得人工复核的决策。

可疑模式（都用引擎语义事后判定，不猜）：
  counter_into_defense   我攻击撞上它的防御技（被应对 → 伤害被减半）
  countstatus_lost       我用状态技时它用攻击技反制（被应对 → 挨"倍率一击+反击一击"）
  defense_wasted         我用防御技，它却用状态技（我的减伤根本没落地）
  missed_kill            我这回合本来能一击杀它，却没出那一手
  doomed_stayed          我这回合被 tick 死或被打死，却没换人
  overkill               能用更便宜的招杀它，却用了高费的
  switch_wasted          我换人而对面没换（白丢一次出手）
  buff_under_threat      我在它能一击杀我时选了强化
  energy_burnout         用光能量打小伤害（下一回合够不到更强的招）

用法：
    env\\python.exe native/tools/audit_ruleagent_decisions.py --games 120
    env\\python.exe native/tools/audit_ruleagent_decisions.py --games 120 --examples 8
"""
from __future__ import annotations

import argparse
import collections
import random
import sys
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
    ap.add_argument("--games", type=int, default=120)
    ap.add_argument("--examples", type=int, default=6, help="每类可疑模式打印几条实例")
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--ev", action="store_true", help="审计期望值层（默认审旧启发式路径）")
    return ap.parse_args()


def _dmg(battle, a, d, skill, team, first=False) -> int:
    if skill is None or d is None or d.current_hp <= 0:
        return 0
    v, _ = battle._resolver.calc_damage(a, d, SkillUse(battle_skill=skill, is_first=first),
                                        battle.globals, attacker_team=team)
    return v


def _kind(skill) -> str:
    return "攻击" if skill.is_attack else ("防御" if skill.is_defense else "状态")


def _snapshot(battle, team: str, agent: RuleAgentV2, strategy: TeamStrategy) -> dict:
    """决策前的可见状态 + 候选打分（不改状态）。"""
    p = agent.player
    s, opp_player = p.active, battle.get_opponent(team)
    opp = opp_player.active
    opp_team = tactics.opponent_team(team)
    table = sorted(((i, _dmg(battle, s, opp, sk, team, True), sk.energy_cost)
                    for i, sk in enumerate(s.skills)
                    if sk.is_attack and sk.cooldown <= 0 and not sk.sealed
                    and sk.energy_cost <= s.energy), key=lambda x: -x[1])
    return {
        "turn": battle.turn,
        "me": s, "opp": opp, "opp_player": opp_player, "table": table,
        "opp_best": max((_dmg(battle, opp, s, sk, opp_team) for sk in opp.skills
                         if sk.is_attack and sk.cooldown <= 0 and not sk.sealed
                         and sk.energy_cost <= opp.energy), default=0),
        "skills": list(s.skills),
    }


def _describe(snapshot: dict, action) -> str:
    s, opp = snapshot["me"], snapshot["opp"]
    if action.kind == "skill" and action.skill_index is not None \
            and action.skill_index < len(snapshot["skills"]):
        sk = snapshot["skills"][action.skill_index]
        what = f"{_kind(sk)}·{sk.name}(费{sk.energy_cost})"
    elif action.kind == "switch":
        what = "换人"
    else:
        what = action.kind
    return (f"T{snapshot['turn']} 我{s.name} {s.current_hp}/{s.max_hp}E{s.energy} "
            f"vs {opp.name} {opp.current_hp}/{opp.max_hp}E{opp.energy} → 出【{what}】")


def _skill_by_name(sprite, name: str):
    for sk in getattr(sprite, "skills", ()) or ():
        if sk.name == name:
            return sk
    return None


def _suspicions(battle, snapshot: dict, my_action, their_record, team: str) -> list[str]:
    """事后判定这一手是否可疑（用双方真实动作 + 引擎规则，不做预测）。"""
    out: list[str] = []
    s, opp = snapshot["me"], snapshot["opp"]
    my_sk = (snapshot["skills"][my_action.skill_index]
             if my_action.kind == "skill" and my_action.skill_index is not None
             and my_action.skill_index < len(snapshot["skills"]) else None)
    their_sk = None
    their_kind = "?"
    if their_record is not None:
        their_kind = their_record.kind
        if their_record.kind == "skill":
            their_sk = _skill_by_name(opp, their_record.skill_name)
            their_kind = _kind(their_sk) if their_sk is not None else "技能"
        else:
            their_kind = {"switch": "换人", "gather": "聚能", "item": "道具"}.get(
                their_record.kind, their_record.kind)

    # ① 应对三角打反了（引擎规则，事后判定）
    if my_sk is not None and their_sk is not None:
        if SkillResolver.resolve_counter(my_sk, their_sk):
            out.append("counter_into_defense" if their_sk.is_defense else "countstatus_lost")
    if my_sk is not None and my_sk.is_defense and their_kind in ("状态", "聚能", "换人"):
        out.append("defense_wasted")          # 减伤没落地（wiki：防御被状态抓到=白放+进冷却）

    # ② 错失斩杀 / 高费过杀
    kills = [(i, d, c) for i, d, c in snapshot["table"] if d >= opp.current_hp > 0]
    if kills:
        kills.sort(key=lambda x: x[2])
        cheapest_kill = kills[0][0]
        if not (my_action.kind == "skill" and my_action.skill_index == cheapest_kill):
            if my_action.kind == "skill" and my_action.skill_index is not None:
                out.append("overkill")
            else:
                out.append("missed_kill")

    # ③ 会被打死/被 tick 死却没撤
    if my_action.kind != "switch" and s.current_hp <= 0.4 * s.max_hp \
            and snapshot["opp_best"] >= s.current_hp > 0:
        out.append("doomed_stayed")

    # ④ 白换人（对面没换 → 白丢一次出手）
    if my_action.kind == "switch" and their_kind != "换人":
        out.append("switch_wasted")

    # ⑤ 在会被一击打死时选强化
    if my_sk is not None and not my_sk.is_attack and not my_sk.is_defense \
            and snapshot["opp_best"] >= s.current_hp > 0:
        out.append("buff_under_threat")

    # ⑥ 能量烧光且这手伤害很低（下一回合连最便宜的招都放不出）
    if my_sk is not None and my_sk.is_attack and opp.current_hp > 1:
        left = s.energy - my_sk.energy_cost
        cheapest = min((sk.energy_cost for sk in s.skills if sk.is_attack), default=0)
        dmg_done = max((d for i, d, _c in snapshot["table"]
                        if i == my_action.skill_index), default=0)
        if left < cheapest and dmg_done < 0.12 * max(1, opp.max_hp):
            out.append("energy_burnout")
    del team
    return out


def main() -> None:
    ensure_hash_seed()
    args = parse_args()
    factory = SimFactory()
    meta = load_meta_teams()
    rng = random.Random(args.seed)
    random.seed(args.seed)
    counts: collections.Counter = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    decisions = 0

    for g in range(args.games):
        meta_i = rng.randrange(len(meta)) if (meta and rng.random() < args.meta_frac) else None
        if meta_i is not None:
            sa, _ = spec_from_team(meta[meta_i], rng)
            sb, _ = spec_from_team(meta[meta_i], rng)
            ia, ib = item_from_team(meta[meta_i], sa), item_from_team(meta[meta_i], sb)
            st_a = st_b = strategy_from_team(meta[meta_i], rng)
        else:
            sa, sb, ia, ib = T._random_teams(factory, dict(SPRITE_RANDOM_POOL),
                                             optimal_frac=0.95, meta_frac=0.0)
            st_a = st_b = TeamStrategy(default=SpriteStrategy())
        if not args.ev:
            st_a = TeamStrategy(name=st_a.name, sprites=dict(st_a.sprites),
                                default=SpriteStrategy(ev_decide=False))
            st_b = TeamStrategy(name=st_b.name, sprites=dict(st_b.sprites),
                                default=SpriteStrategy(ev_decide=False))
        p1 = factory.build_player("A", sa, item=ia)
        p2 = factory.build_player("B", sb, item=ib)
        battle = factory.build_battle(p1, p2)
        a1 = RuleAgentV2("A", p1, strategy=st_a)
        a2 = RuleAgentV2("B", p2, strategy=st_b)

        pending: dict[str, dict] = {}
        for team, agent in (("A", a1), ("B", a2)):
            real = agent.choose_action

            def spy(b, _team=team, _agent=agent, _real=real):
                snap = _snapshot(b, _team, _agent, _agent.strategy)
                action = _real(b)
                pending[_team] = {"snap": snap, "action": action,
                                  "hp": (snap["me"].current_hp, snap["opp"].current_hp),
                                  "desc": _describe(snap, action)}
                return action
            agent.choose_action = spy

        turns = 0
        while not battle.is_finished and turns < args.max_turns:
            pending.clear()
            rec = battle.execute_turn(a1, a2)
            turns += 1
            for team, action_rec in (("A", rec.action_a), ("B", rec.action_b)):
                info = pending.get(team)
                if not info:
                    continue
                their = rec.action_b if team == "A" else rec.action_a
                decisions += 1
                flags = _suspicions(battle, info["snap"], info["action"], their, team)
                if not flags:
                    continue
                for f in flags:
                    counts[f] += 1
                    if len(examples[f]) < args.examples:
                        mine, opp = info["snap"]["me"], info["snap"]["opp"]
                        hp0, ohp0 = info["hp"]
                        result = (f"结果 我-{hp0 - mine.current_hp} 敌-{ohp0 - opp.current_hp}"
                                  f"{'（我力倒）' if mine.is_fainted else ''}"
                                  f"{'（它力倒）' if opp.is_fainted else ''}")
                        examples[f].append(
                            f"{info['desc']} | 对手出 {their.kind if their else '?'}"
                            f"·{getattr(their, 'skill_name', '')} | {result}")

    print(f"=== {args.games} 局，{decisions} 个决策点（{'EV 层' if args.ev else '旧启发式'}）===")
    for name, n in counts.most_common():
        print(f"  {name:<22} {n:>6}  ({n / max(1, decisions):.1%})")
        for line in examples[name]:
            print(f"      · {line}")
    if not counts:
        print("  没有可疑决策。")


if __name__ == "__main__":
    main()
