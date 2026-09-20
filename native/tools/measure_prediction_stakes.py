# -*- coding: utf-8 -*-
"""native/tools/measure_prediction_stakes.py — E0 度量：预测换人的"赌注"有多大。

问题（用户提出）：场上我方克制对面时，对面**可能换、也可能不换**；我可以预判换人
出克制技能、预判不换打伤害、或者干脆白嫖强化。这就是一手"赌博"。

本工具不改任何决策，只在**真实对局**里逐决策点记录三件事（全部用引擎自己的伤害
与应对判定算，不另立公式）：

1. **对手实际换人率**（基准线）：这些局面里对面到底换没换；
2. **预测分歧率**：把"假设它不换"与"假设它换成最优替补"两种世界分别求最优动作，
   两者是否不同（"换了该怎么打"和"不换该怎么打"是不是两套）；
3. **猜错代价**：押错方向时的直接损失——按 % 最大生命计（① 用错目标的技能伤害差；
   ② 若选了白嫖强化而对面没换，损失 = 我这回合没打出的伤害）。

**口径与已知低估**：只算本回合的即时价值，不含换人/强化的未来收益（防守换位、
强化后的下一回合爆发），所以"猜错代价"是**下界**，换人/强化两行的价值被系统性低估。
这是有意的：E0 只回答"这两种世界是否要求不同打法、代价多大"，不试图给出最终评价函数
（那是 `backend/sim/ev.py` 的事）。

用法（项目根、项目解释器）：
    env\\python.exe native/tools/measure_prediction_stakes.py --games 200
"""
from __future__ import annotations

import argparse
import collections
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.engine.ai.bc_record import run_recorded_battle  # noqa: E402
from backend.engine.ai.data.meta_teams import (  # noqa: E402
    item_from_team,
    load_meta_teams,
    spec_from_team,
    strategy_from_team,
)
from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL  # noqa: E402
from backend.engine.ai import train as T  # noqa: E402
from backend.sim import tactics  # noqa: E402
from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy  # noqa: E402
from backend.sim.battleskill import SkillUse  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402

BUFF_CREDIT = 0.15   # 一次强化的未来收益折算（占对方最大生命比例）——可扫参
GATHER_CREDIT = 0.05  # 聚能的能量收益折算


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--meta-frac", type=float, default=0.6)
    ap.add_argument("--optimal-frac", type=float, default=0.95)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--buff-credit", type=float, default=BUFF_CREDIT)
    return ap.parse_args()


def _damage(battle, attacker, defender, skill, team) -> int:
    if skill is None:
        return 0
    dmg, _ = battle._resolver.calc_damage(
        attacker, defender, SkillUse(battle_skill=skill), battle.globals, attacker_team=team)
    return dmg


def _my_attack_options(battle, me, target, team):
    """(skill_index, dmg) —— 本回合可负担、未冷却的攻击技对 target 的伤害。"""
    out = []
    for i, skill in enumerate(me.skills):
        if not skill.is_attack or skill.cooldown > 0 or skill.sealed:
            continue
        if skill.energy_cost > me.energy:
            continue
        out.append((i, _damage(battle, me, target, skill, team)))
    return out


def _has_buff(me) -> bool:
    return any(
        skill.cooldown <= 0 and not skill.sealed and not skill.is_attack
        and not skill.is_defense and skill.energy_cost <= me.energy
        and any(getattr(e, 'kind', '') == 'stat' for e in skill.effects)
        for skill in me.skills
    )


def _opp_best_incoming(battle, opp, me, team) -> int:
    """对手能对我打出的最大伤害（换人预测外的风险项）。"""
    best = 0
    for skill in opp.skills:
        if not skill.is_attack or skill.cooldown > 0 or skill.sealed:
            continue
        if skill.energy_cost > opp.energy:
            continue
        best = max(best, _damage(battle, opp, me, skill, tactics.opponent_team(team)))
    return best


def _measure_decision(battle, me, opp_player, team, args, stats: collections.Counter,
                      costs: list[float]) -> None:
    """一个决策点的赌注度量（不改状态）。"""
    opp = opp_player.active
    bench = [opp_player.team[i] for i in opp_player.alive_sprites
             if i != opp_player.active_index]
    if not bench or opp.current_hp <= 0 or me.current_hp <= 0:
        stats["跳过:无替补"] += 1
        return

    attacks_now = _my_attack_options(battle, me, opp, team)
    if not attacks_now:
        stats["跳过:无可用攻击"] += 1
        return

    # 对面会换成谁：它换成"对我最能扛/最能打"的那只才有意义，这里取
    # "我对它伤害最低"（对面最可能选它来吃我这招）与"它打我最高"两个视角取最坏
    switch_target = min(bench, key=lambda s: (_damage(battle, me, s, me.skills[attacks_now[0][0]], team),
                                              -tactics.switch_in_damage(battle, tactics.opponent_team(team), s)))
    attacks_after = _my_attack_options(battle, me, switch_target, team)
    if not attacks_after:
        stats["跳过:换人后无攻击"] += 1
        return

    stats["决策点"] += 1
    best_now_i, best_now_dmg = max(attacks_now, key=lambda x: x[1])
    best_sw_i, best_sw_dmg = max(attacks_after, key=lambda x: x[1])

    # ① 预测分歧：两种世界的最优技能不是同一个（且差值有意义，避免并列噪声）
    if best_now_i != best_sw_i and abs(best_now_dmg - best_sw_dmg) >= 1:
        stats["分歧:技能不同"] += 1

    # ② 猜错代价（占"目标"最大生命，上限 100% = 一条命）：押"它不换"却换了 / 押"它换"却没换
    opp_max = max(1, opp.max_hp)
    sw_max = max(1, switch_target.max_hp)
    cost_wrong_switch = min(1.0, max(
        0, (best_sw_dmg - _damage(battle, me, switch_target, me.skills[best_now_i], team))) / sw_max)
    cost_wrong_stay = min(1.0, max(
        0, (best_now_dmg - _damage(battle, me, opp, me.skills[best_sw_i], team))) / opp_max)
    costs.append(max(cost_wrong_switch, cost_wrong_stay) * 100)
    if cost_wrong_switch > 0.02:
        stats["代价>2%:押不换但换了"] += 1
    if cost_wrong_stay > 0.02:
        stats["代价>2%:押换但没换"] += 1

    # ③ 白嫖强化：我预判换人于是强化 → 对面没换时我损失这回合的伤害
    if _has_buff(me):
        stats["可强化"] += 1
        if best_now_dmg / opp_max > args.buff_credit:
            stats["白嫖强化:不如打（伤害更高）"] += 1

    # ④ 对面真的换了吗（用下一回合的状态反推：这里只统计"我方威胁是否有斩杀"）
    if best_now_dmg + tactics.starfall_bonus(
            battle, me, opp, me.skills[best_now_i], tactics.opponent_team(team)) >= opp.current_hp:
        stats["我一击可斩杀它"] += 1
        stats["我一击可斩杀它:且有替补(可逃)"] += 1


def _run_one_card(plan, args, stats, costs):
    """打一局，逐决策测赌注；返回 (turns, 对手换人次数, 对手动作决策数)。"""
    factory = SimFactory()
    team_a, team_b = plan["team_a"], plan["team_b"]
    item_a, item_b = plan["item_a"], plan["item_b"]
    strat_a, strat_b = plan["strat_a"], plan["strat_b"]
    random.seed(plan["rng_seed"])
    p1 = factory.build_player("A", team_a, item=item_a)
    p2 = factory.build_player("B", team_b, item=item_b)
    battle = factory.build_battle(p1, p2)
    a1 = RuleAgentV2("A", p1, strategy=strat_a)
    a2 = RuleAgentV2("B", p2, strategy=strat_b)

    switches = collections.Counter()
    real_a1, real_a2 = a1.choose_action, a2.choose_action

    def spy(inner, me_player, opp_player, team):
        def wrapped(battle_):
            # 先用"当前真实局面"量赌注，再让 agent 真出招
            if me_player.active is not None and opp_player.active is not None:
                _measure_decision(battle_, me_player.active, opp_player, team, args, stats, costs)
            action = inner(battle_)
            if action.kind == "switch":
                switches[team] += 1
            switches[team + ":决策"] += 1
            return action
        return wrapped

    a1.choose_action = spy(real_a1, p1, p2, "A")
    a2.choose_action = spy(real_a2, p2, p1, "B")
    turns = 0
    while not battle.is_finished and turns < args.max_turns:
        battle.execute_turn(a1, a2)
        turns += 1
    return turns, switches


def main() -> None:
    ensure_hash_seed()
    args = parse_args()
    factory = SimFactory()
    sprite_skills = dict(SPRITE_RANDOM_POOL)
    meta = load_meta_teams()
    rng = random.Random(args.seed)
    random.seed(args.seed)

    stats: collections.Counter = collections.Counter()
    costs: list[float] = []
    switch_counts: collections.Counter = collections.Counter()
    t0 = time.time()
    turns_sum = 0
    for g in range(args.games):
        if meta and rng.random() < args.meta_frac:
            i_a, i_b = rng.randrange(len(meta)), rng.randrange(len(meta))
            ta, _ = spec_from_team(meta[i_a], rng)
            tb, _ = spec_from_team(meta[i_b], rng)
            sa, sb = strategy_from_team(meta[i_a], rng), strategy_from_team(meta[i_b], rng)
            ia, ib = item_from_team(meta[i_a], ta), item_from_team(meta[i_b], tb)
        else:
            ta, tb, ia, ib = T._random_teams(factory, sprite_skills,
                                             optimal_frac=args.optimal_frac, meta_frac=0.0)
            sa = sb = TeamStrategy(default=SpriteStrategy())
        plan = {
            "team_a": ta, "team_b": tb, "item_a": ia, "item_b": ib,
            "strat_a": sa, "strat_b": sb,
            "rng_seed": (args.seed * 1000003 + g * 7919) & 0x7FFFFFFF,
        }
        turns, switches = _run_one_card(plan, args, stats, costs)
        turns_sum += turns
        switch_counts.update(switches)

    n = max(1, stats["决策点"])
    print(f"=== {args.games} 局（meta {args.meta_frac:.0%}），平均 {turns_sum / args.games:.1f} 回合，"
          f"{time.time() - t0:.0f}s ===")
    print(f"决策点 {stats['决策点']}（跳过：无替补 {stats['跳过:无替补']}、"
          f"无可用攻击 {stats['跳过:无可用攻击']}、换人后无攻击 {stats['跳过:换人后无攻击']}）")
    print(f"对手实际换人率: {switch_counts['A'] + switch_counts['B']}/{switch_counts['A:决策'] + switch_counts['B:决策']}"
          f" = {(switch_counts['A'] + switch_counts['B']) / max(1, switch_counts['A:决策'] + switch_counts['B:决策']):.1%}")
    print(f"预测分歧（换了/不换 该用不同技能）: {stats['分歧:技能不同']}/{n} = {stats['分歧:技能不同'] / n:.1%}")
    print(f"我一击可斩杀它（可逃局面）: {stats['我一击可斩杀它']}/{n} = {stats['我一击可斩杀它'] / n:.1%}")
    print(f"押错方向代价 >2% 最大生命: 押不换但换了 {stats['代价>2%:押不换但换了'] / n:.1%}、"
          f"押换但没换 {stats['代价>2%:押换但没换'] / n:.1%}")
    if costs:
        print(f"猜错代价（占目标最大生命）: 均值 {statistics.mean(costs):.2f}% "
              f"中位 {statistics.median(costs):.2f}% "
              f"p90 {sorted(costs)[int(len(costs) * 0.9)]:.2f}% 最大 {max(costs):.2f}%")
    if stats["可强化"]:
        print(f"有强化技可用 {stats['可强化']}/{n}；其中'白嫖强化不如直接打'（伤害>{args.buff_credit:.0%}）: "
              f"{stats['白嫖强化:不如打（伤害更高）']}/{stats['可强化']}")
    print("注：只算本回合即时价值，换人/强化的未来收益未计入 → 代价是下界。")


if __name__ == "__main__":
    main()
