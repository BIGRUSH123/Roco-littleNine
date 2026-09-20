# -*- coding: utf-8 -*-
"""native/tools/audit_skill_choice_regret.py — E0：把「出招不会计划」量化成 regret。

**要回答的问题**：`RuleAgentV2` 的默认出招是"可负担攻击里取最高即时伤害"
（`backend/sim/agent_v2.py:439-462`），强化/状态技只在打不出招时兜底，技能效果只被**计数**
（`len(sk.effects)`、`count(kind=='stat')`）不被解读。于是它的技能选择接近"按伤害排序"，
不会为技能/特性做多回合计划。那么这到底亏多少？

**做法**：在每个决策点，从同一局面出发用引擎做一回合 rollout：
  我方候选（引擎的合法动作集）× 对手响应集（最高伤害攻击 / 最佳防御 / 最佳状态 / 换人 / 聚能）
叶子用引擎自己的局面分 `team_battle_score`（打满回合的官方裁决口径），取每个候选的平均价值，
再与专家实际的选择比 —— 差值就是 regret。

**指标**：
  · regret 的 mean / median / p90，以及 `regret > --epsilon` 的占比（默认 0.05 ≈ 1/5 点心力）
  · "一回合最优是非攻击动作、而专家出了攻击"的占比（这就是计划缺口）
  · 专家出招的动作类别分布（攻击/防御/状态/换人/聚能/道具）
  · 最差 N 个决策点的明细（回合、侧别、实际选择 vs 最优选择及其价值）

**不扰动对局**：rollout 用 `execute_turn_headless`（不写日志/记录）+ 每步
`battle.restore_mutable_state(saved)` 与 `random.setstate()` 回滚（与 MCTS 同一套机制），
真正的回合仍由专家正常走；审计结论与实录的选择不一致时会打警告。

用法：
    env\\python.exe native/tools/audit_skill_choice_regret.py --games 6 --seed 20260921
    env\\python.exe native/tools/audit_skill_choice_regret.py --games 6 --team-a meta:31 `
        --team-b file:native/tools/duel_teams/pro.json --k-responses 4 --verbose
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

import duel_harness as H  # noqa: E402  复用建局/队伍规格（同一口径）

from backend.common.constants import ITEM_VARIANT_ACTION_BASE  # noqa: E402
from backend.engine.ai.core.mcts import (  # noqa: E402
    get_valid_actions, action_index_to_action, ITEM_ACTION_IDX,
)
from backend.engine.ai.core.outcome import team_battle_score  # noqa: E402
from backend.sim.agent import _GATHER_ACTION  # noqa: E402

try:  # 与其它量测工具同口径的确定性
    from backend.engine.ai.determinism import ensure_hash_seed

    ensure_hash_seed()
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════
# 动作 ↔ 索引
# ══════════════════════════════════════════════════════════════════

def action_to_index(player, action) -> int | None:
    """Action → 动作空间索引（0-9 技能 / 10-14 换人槽 / 15 聚能 / 16 道具 / 17+ 形态）。"""
    if action is None:
        return None
    if action.kind == "skill":
        return int(action.skill_index)
    if action.kind == "switch":
        active = player.active_index
        bench_slot = action.switch_index if action.switch_index < active else action.switch_index - 1
        return 10 + bench_slot if 0 <= bench_slot < 5 else None
    if action.kind == "gather":
        return 15
    if action.kind == "item":
        if action.variant is None:
            return ITEM_ACTION_IDX
        return ITEM_VARIANT_ACTION_BASE + int(action.variant)
    return None


def _to_action(player, idx: int):
    if idx is None or idx < 0:
        return _GATHER_ACTION
    return action_index_to_action(player, idx) or _GATHER_ACTION


def describe(player, action) -> str:
    if action is None:
        return "None"
    if action.kind == "skill":
        skills = player.active.skills if player.active else []
        if 0 <= action.skill_index < len(skills):
            sk = skills[action.skill_index]
            kind = "攻击" if sk.is_attack else ("防御" if sk.is_defense else "状态")
            return f"技能 {sk.name}（{kind}）"
        return f"技能槽{action.skill_index}"
    if action.kind == "switch":
        if 0 <= action.switch_index < len(player.team):
            return f"换人 {player.team[action.switch_index].name}"
        return f"换人槽{action.switch_index}"
    return {"item": "道具", "gather": "聚能"}.get(action.kind, action.kind)


def kind_of(player, action) -> str:
    if action is None:
        return "none"
    if action.kind == "switch":
        return "switch"
    if action.kind == "gather":
        return "gather"
    if action.kind == "item":
        return "item"
    if action.kind == "skill":
        skills = player.active.skills if player.active else []
        if 0 <= action.skill_index < len(skills):
            sk = skills[action.skill_index]
            return "attack" if sk.is_attack else ("defense" if sk.is_defense else "status")
    return "other"


def _affordable(active, sk) -> bool:
    return not sk.sealed and sk.cooldown <= 0 and sk.energy_cost <= active.energy


# ══════════════════════════════════════════════════════════════════
# 对手响应集
# ══════════════════════════════════════════════════════════════════

def response_actions(battle, opp_player, k: int) -> list[tuple[str, object]]:
    """对手响应集：最高伤害攻击 / 最佳防御 / 最佳状态 / 换人 / 聚能（去重取前 k）。"""
    from backend.sim.item_policy import estimate_damage
    from backend.sim.ev import defense_reduction
    from backend.sim.action import Action

    other = battle.player_a if opp_player is battle.player_b else battle.player_b
    team = "A" if opp_player is battle.player_a else "B"
    active = opp_player.active
    if active is None:
        return [("聚能", _GATHER_ACTION)]
    picks: list[tuple[str, object]] = []
    best_atk, best_atk_dmg = None, -1
    best_def, best_def_val = None, -1.0
    best_st, best_st_n = None, -1
    for i, sk in enumerate(active.skills):
        if not _affordable(active, sk):
            continue
        if sk.is_attack:
            try:
                dmg = estimate_damage(battle, active, other.active, sk, team)
            except Exception:
                continue
            if dmg > best_atk_dmg:
                best_atk, best_atk_dmg = i, dmg
        elif sk.is_defense:
            val = defense_reduction(sk)
            if val > best_def_val:
                best_def, best_def_val = i, val
        elif len(sk.effects) > best_st_n:
            best_st, best_st_n = i, len(sk.effects)
    if best_atk is not None:
        picks.append(("攻击", Action(kind="skill", skill_index=best_atk)))
    if best_def is not None:
        picks.append(("防御", Action(kind="skill", skill_index=best_def)))
    if best_st is not None:
        picks.append(("状态", Action(kind="skill", skill_index=best_st)))
    bench = [i for i, s in enumerate(opp_player.team)
             if i != opp_player.active_index and not s.is_fainted]
    if bench:
        picks.append(("换人", Action(kind="switch", switch_index=bench[0])))
    picks.append(("聚能", _GATHER_ACTION))
    # 去重（按索引空间）并按 k 截断
    seen, out = set(), []
    for label, act in picks:
        idx = action_to_index(opp_player, act)
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        out.append((label, act))
        if len(out) >= max(1, k):
            break
    return out


# ══════════════════════════════════════════════════════════════════
# rollout
# ══════════════════════════════════════════════════════════════════

def leaf_value(battle, side: str) -> float:
    """叶子价值 = 官方局面分差（存活 1.0 + 血量比 0.5 + 心力 0.25 + 能量 0.05）。"""
    me = battle.player_a if side == "A" else battle.player_b
    opp = battle.player_b if side == "A" else battle.player_a
    return team_battle_score(me) - team_battle_score(opp)


class _FixedAgent:
    def __init__(self, team: str, player, action, replacement):
        self.team, self.player = team, player
        self.action, self._replacement = action, replacement

    def choose_action(self, battle):
        return self.action

    def choose_lead(self, battle) -> int:
        return self.player.active_index

    def choose_replacement(self, battle) -> int:
        return self._replacement(self.player)

    def on_game_end(self, winner):
        pass


def _first_alive_bench(player) -> int:
    for i, s in enumerate(player.team):
        if i != player.active_index and not s.is_fainted:
            return i
    return player.active_index


def rollout(battle, side: str, my_action, their_action, plies: int = 1,
            agents: dict | None = None) -> float:
    """从当前局面走 plies 个回合，返回我方视角的叶子价值。调用方负责回滚。

    第 1 回合按给定的一对动作走；后续回合用**专家的选择**续走（续走用的是一次性
    专家实例，不去动正在跑的那一局的真专家）。视野 ≥2 回合才看得见"铺垫"的价值。
    """
    me = battle.player_a if side == "A" else battle.player_b
    opp = battle.player_b if side == "A" else battle.player_a
    ag_me = _FixedAgent(side, me, my_action, _first_alive_bench)
    ag_op = _FixedAgent("B" if side == "A" else "A", opp, their_action, _first_alive_bench)
    a, b = (ag_me, ag_op) if side == "A" else (ag_op, ag_me)
    battle.execute_turn_headless(agent_a=a, agent_b=b,
                                 fixed_action_a=a.action, fixed_action_b=b.action)
    for _ in range(max(0, plies - 1)):
        if battle.is_finished or agents is None:
            break
        act_a = agents["A"].choose_action(battle)
        act_b = agents["B"].choose_action(battle)
        pa, pb = (act_a, act_b)
        battle.execute_turn_headless(
            agent_a=_FixedAgent("A", battle.player_a, pa, _first_alive_bench),
            agent_b=_FixedAgent("B", battle.player_b, pb, _first_alive_bench),
            fixed_action_a=pa, fixed_action_b=pb)
    return leaf_value(battle, side)


# ══════════════════════════════════════════════════════════════════
# 决策点审计
# ══════════════════════════════════════════════════════════════════

def audit_decision(battle, side: str, agent, k_responses: int, records: list[dict],
                   plies: int, agents: dict | None, chosen_action, chosen_player) -> None:
    """从**回合前快照**回滚做 rollout；专家只被调用一次（由调用方在真回合里调用）。

    `chosen_action/chosen_player` 是实录的专家选择 —— 先走真回合拿到它，再回滚局面做评估，
    这样既不会二次调用专家（它带内部状态，二次调用可能给出不同答案），
    也不会让 rollout 污染对局（结束时恢复到真回合之后的状态）。
    """
    player = battle.player_a if side == "A" else battle.player_b
    opp = battle.player_b if side == "A" else battle.player_a
    if player.active is None or player.active.is_fainted:
        return
    valid, _mask = get_valid_actions(player, battle)
    cands = [i for i in valid if _to_action(player, i) is not None]
    if len(cands) < 2:
        return
    chosen_idx = action_to_index(player, chosen_action)
    responses = response_actions(battle, opp, k_responses)
    saved = battle.save_mutable_state()       # 回合前
    pre_rng = random.getstate()
    values: dict[int, float] = {}
    try:
        for ci in cands:
            my_act = _to_action(player, ci)
            vals = []
            for _label, their_act in responses:
                battle.restore_mutable_state(saved)
                random.setstate(pre_rng)
                try:
                    vals.append(rollout(battle, side, my_act, their_act, plies, agents))
                except Exception:
                    pass
            if vals:
                values[ci] = sum(vals) / len(vals)
    finally:
        battle.restore_mutable_state(saved)
        random.setstate(pre_rng)

    if len(values) < 2:
        return
    best_idx = max(values, key=lambda i: values[i])
    records.append({"side": side, "turn": battle.turn,
                    "chosen": chosen_idx, "chosen_action": chosen_action,
                    "values": values, "best": best_idx, "best_value": values[best_idx],
                    "player": player, "opp": opp,
                    "my_name": player.active.name, "opp_name": opp.active.name,
                    "n_cand": len(cands), "n_resp": len(responses)})


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

class _Recorder:
    """包住专家：记录实际选择（含当时的场上精灵），不改变决策。"""

    def __init__(self, team: str, agent, log: list):
        self.team, self.agent, self.log = team, agent, log

    def choose_action(self, battle):
        act = self.agent.choose_action(battle)
        player = battle.player_a if self.team == "A" else battle.player_b
        self.log.append((self.team, act, player.active))
        return act

    def choose_lead(self, battle) -> int:
        return self.agent.choose_lead(battle)

    def choose_replacement(self, battle) -> int:
        return self.agent.choose_replacement(battle)

    def on_game_end(self, winner):
        return self.agent.on_game_end(winner)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260921)
    ap.add_argument("--team-a", default="meta:31")
    ap.add_argument("--team-b", default="file:native/tools/duel_teams/pro.json")
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--random-teams", action="store_true",
                    help="每局用随机阵容（默认用 --team-a/--team-b 的固定对局）")
    ap.add_argument("--k-responses", type=int, default=4)
    ap.add_argument("--plies", type=int, default=1,
                    help="rollout 视野回合数（1 = 只看本回合；2 = 看铺垫的回报）")
    ap.add_argument("--epsilon", type=float, default=0.05,
                    help="regret 判「踩空」的阈值（局面分单位，0.05 ≈ 1/5 点心力）")
    ap.add_argument("--worst", type=int, default=8)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    from backend.sim.agent_v2 import RuleAgentV2

    records: list[dict] = []
    kinds = Counter()
    unaudited = 0
    games_done = 0

    for g in range(args.games):
        cfg = {"team_a": args.team_a, "team_b": args.team_b, "seed": args.seed + g,
               "lead_a": 0, "lead_b": 0, "neutral": True}
        try:
            if args.random_teams:
                from backend.engine.ai.train import _random_teams
                from backend.engine.ai.data.sprite_random_pool import SPRITE_RANDOM_POOL
                from backend.sim.factory import SimFactory

                factory = SimFactory()
                sa, sb, item_a, item_b = _random_teams(
                    factory, dict(SPRITE_RANDOM_POOL), optimal_frac=0.95, meta_frac=0.0,
                    rng=random.Random(args.seed + g))
                battle = factory.build_battle(
                    factory.build_player("A", sa, item=item_a),
                    factory.build_player("B", sb, item=item_b))
            else:
                _factory, battle = H._build_battle(cfg)
        except Exception as exc:
            print(f"  建局失败（seed {cfg['seed']}）：{exc}", file=sys.stderr)
            continue
        ag_a, ag_b = RuleAgentV2("A", battle.player_a), RuleAgentV2("B", battle.player_b)
        if args.plies > 1:  # 续走用的独立实例，别动真专家
            sim_agents = {"A": RuleAgentV2("A", battle.player_a),
                          "B": RuleAgentV2("B", battle.player_b)}
        else:
            sim_agents = None
        while not battle.is_finished and battle.turn < args.max_turns:
            pre = battle.save_mutable_state()
            pre_rng = random.getstate()
            logged: list = []
            played = {side: _Recorder(side, agent, logged)
                      for side, agent in (("A", ag_a), ("B", ag_b))}
            try:
                battle.execute_turn(played["A"], played["B"])
            except Exception as exc:
                print(f"  [回合执行失败] T{battle.turn}：{exc}", file=sys.stderr)
                battle.restore_mutable_state(pre)
                random.setstate(pre_rng)
                break
            post = battle.save_mutable_state()
            post_rng = random.getstate()

            first: dict[str, tuple] = {}
            for team, act, sprite in logged:
                kinds[kind_of_sprite(sprite, act)] += 1
                first.setdefault(team, (act, sprite))
            try:
                for side, agent in (("A", ag_a), ("B", ag_b)):
                    if side not in first:
                        continue
                    battle.restore_mutable_state(pre)
                    random.setstate(pre_rng)
                    audit_decision(battle, side, agent, args.k_responses, records,
                                   args.plies, sim_agents, first[side][0], None)
                    if records:
                        records[-1].setdefault("game", g + 1)
                    if records and records[-1]["chosen"] not in records[-1]["values"]:
                        unaudited += 1
            finally:
                battle.restore_mutable_state(post)   # 回到真回合之后，继续下一回合
                random.setstate(post_rng)
        games_done += 1
        if args.verbose:
            print(f"  第 {g + 1} 局：{battle.turn} 回合 winner={battle.winner} "
                  f"心力 A{battle.player_a.lives}/B{battle.player_b.lives}")
    report(records, kinds, games_done, unaudited, args)


def kind_of_sprite(sprite, action) -> str:
    if action is None:
        return "none"
    if action.kind in ("switch", "gather", "item"):
        return action.kind
    if action.kind == "skill" and sprite is not None:
        skills = getattr(sprite, "skills", [])
        if 0 <= action.skill_index < len(skills):
            sk = skills[action.skill_index]
            return "attack" if sk.is_attack else ("defense" if sk.is_defense else "status")
    return "other"


def report(records, kinds, games_done, unaudited, args) -> None:
    if not records:
        print("没有任何决策点被审计（建局失败或对局太短）")
        return
    regrets, comparable = [], []
    nonattack_better = 0
    for rec in records:
        chosen = rec["chosen"]
        if chosen is None or chosen not in rec["values"]:
            continue
        reg = rec["best_value"] - rec["values"][chosen]
        regrets.append(reg)
        comparable.append(rec)
        c_act = rec["chosen_action"]
        b_act = _to_action(rec["player"], rec["best"])
        if c_act is not None and c_act.kind == "skill" and b_act.kind == "skill":
            pl = rec["player"]
            cs, bs = c_act.skill_index, b_act.skill_index
            if 0 <= cs < len(pl.active.skills) and 0 <= bs < len(pl.active.skills):
                if pl.active.skills[cs].is_attack and not pl.active.skills[bs].is_attack:
                    nonattack_better += 1

    n = len(regrets)
    if not n:
        print("没有可比较的决策点")
        return
    mean = statistics.fmean(regrets)
    med = statistics.median(regrets)
    p90 = sorted(regrets)[min(n - 1, int(0.9 * n))]
    bad = sum(1 for r in regrets if r > args.epsilon)
    print(f"=== E0 出招 regret 审计（{games_done} 局 / {n} 个决策点）===")
    print("  叶子口径：官方局面分差（存活×1.0 + 血量比×0.5 + 心力×0.25 + 在场能量×0.05）")
    print(f"  候选集：引擎合法动作；对手响应集 {args.k_responses} 个（攻击/防御/状态/换人/聚能）")
    print(f"  regret：mean {mean:.3f}  median {med:.3f}  p90 {p90:.3f}"
          f"   >{args.epsilon:g} 的占比 {bad / n:.1%}")
    print(f"  「一回合最优是非攻击、专家却出了攻击」：{nonattack_better} 次（{nonattack_better / n:.1%}）")
    total = sum(kinds.values()) or 1
    print("  专家出招类别分布：" + "  ".join(
        f"{k} {kinds[k]}({kinds[k] / total:.1%})"
        for k in ("attack", "defense", "status", "switch", "gather", "item") if kinds[k]))
    if unaudited:
        print(f"  专家选择不在一回合候选里的次数：{unaudited}（道具/形态类动作，配对已跳过）")
    print(f"  最差 {args.worst} 个决策点（G=局号）：")
    order = sorted(range(n), key=lambda i: -regrets[i])[: args.worst]
    for pos in order:
        rec = comparable[pos]
        pl = rec["player"]
        print(f"   · G{rec.get('game', '?')} T{rec['turn']:<2} [{rec['side']}] "
              f"{rec['my_name']} vs {rec['opp_name']}"
              f"  实际 {describe(pl, rec['chosen_action'])}"
              f"（{rec['values'][rec['chosen']]:+.3f}）"
              f"  →  最优 {describe(pl, _to_action(pl, rec['best']))}"
              f"（{rec['best_value']:+.3f}）  regret {regrets[pos]:.3f}")


if __name__ == "__main__":
    main()
