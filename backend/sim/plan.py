# -*- coding: utf-8 -*-
"""backend/sim/plan.py — E2：用「候选 × 对手响应」的一回合 rollout + E1 叶子做决策。

**与 `ev.py` 的分工**：`ev.py` 是解析式一回合期望值（信念 × 手写收益公式）；
本模块是**真模拟** —— 把候选动作与对手响应真的喂进引擎走一回合，叶子用
`value.state_value`。于是"铺垫一回合再收成""举盾吃下这一击""换人躲 tick 又躲进场印记"
这类依赖引擎细节的计划，不用为每种机制写一条规则就能被评估。

**成本**：候选 ≤8 × 对手响应 ≤3 × ply 1~2 = 每次决策 8~48 个 headless 回合；
回滚用 `battle.save_mutable_state()/restore_mutable_state()` + `random.getstate()/setstate()`
（与 MCTS 同一套），仿真期间 `execute_turn_headless` 不写日志/记录。
"""
from __future__ import annotations

import random

from backend.sim.value import DEFAULT_PARAMS


def response_actions(battle, opp_player, k: int = 3) -> list[tuple[str, object]]:
    """对手响应集（粗粒度、确定性）：最高伤攻击 / 最佳防御 / 最佳状态 / 换人 / 聚能。

    精确预测它出哪一招不可能，而决策真正关心的是"它这一回合大概在做什么事"。
    """
    from backend.sim.action import Action
    from backend.sim.agent import _GATHER_ACTION
    from backend.sim.ev import defense_reduction
    from backend.sim.item_policy import estimate_damage

    other = battle.player_a if opp_player is battle.player_b else battle.player_b
    team = "A" if opp_player is battle.player_a else "B"
    active = opp_player.active
    if active is None:
        return [("聚能", _GATHER_ACTION)]

    def usable(sk) -> bool:
        return not sk.sealed and sk.cooldown <= 0 and sk.energy_cost <= active.energy

    best_atk = best_atk_dmg = None
    best_def = best_def_val = None
    best_st = best_st_n = None
    best_atk_dmg = -1
    best_def_val = -1.0
    best_st_n = -1
    for i, sk in enumerate(active.skills):
        if not usable(sk):
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
        elif best_st_n < 0:
            # 状态技候选：取第一个可用者。旧判据 `len(sk.effects)`（旧 kind 层）
            # 随该层删除——IR 语料下它恒为 0，`0 > -1` 只对第一个成立，
            # 语义与今天的实际行为一致。
            best_st, best_st_n = i, 0

    picks: list[tuple[str, object]] = []
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
    return picks[: max(1, k)]


class FixedAgent:
    """把预定动作喂给引擎（仿真用）。"""

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


def first_alive_bench(player) -> int:
    for i, s in enumerate(player.team):
        if i != player.active_index and not s.is_fainted:
            return i
    return player.active_index


def choose(battle, team: str, candidates: list, *, plies: int = 1,
           k_responses: int = 3, params=None, rng=None,
           continuation=None) -> tuple[object | None, dict]:
    """候选动作里选 rollout 期望价值最高的那个。

    Args:
        candidates: 候选 `Action` 列表（调用方负责给"引擎允许"的动作）。
        plies: 视野回合数（1 = 只看本回合；2+ 需要 `continuation` 提供续走用 agent）。
        continuation: `{"A": agent, "B": agent}`，续走回合由它们决策（不能是规划型，
            否则递归爆炸）。
        rng: 用于回滚的随机源（默认全局 `random`，与引擎一致）。

    Returns:
        (选中的 Action 或 None, 诊断信息) —— None 表示"没有可评估的候选，调用方走旧路径"。
    """
    from backend.sim.value import state_value

    if not candidates:
        return None, {}
    params = params or DEFAULT_PARAMS
    rng = rng or random
    me = battle.player_a if team == "A" else battle.player_b
    opp = battle.player_b if team == "A" else battle.player_a
    other = "B" if team == "A" else "A"
    responses = response_actions(battle, opp, k_responses)

    saved = battle.save_mutable_state()
    rng_state = rng.getstate()
    values: dict[int, float] = {}
    try:
        for idx, act in enumerate(candidates):
            total = 0.0
            used = 0
            for _label, their_act in responses:
                battle.restore_mutable_state(saved)
                rng.setstate(rng_state)
                try:
                    _rollout(battle, team, me, opp, act, their_act, plies, continuation)
                except Exception:
                    continue
                total += state_value(battle, team, params)
                used += 1
            if used:
                values[idx] = total / used
    finally:
        battle.restore_mutable_state(saved)
        rng.setstate(rng_state)

    if not values:
        return None, {}
    best_idx = max(values, key=lambda i: values[i])
    info = {"values": {str(candidates[i]): v for i, v in values.items()},
            "best": str(candidates[best_idx]), "n_candidates": len(candidates),
            "n_responses": len(responses), "plies": plies}
    del other
    return candidates[best_idx], info


def _rollout(battle, team: str, me, opp, my_action, their_action,
             plies: int, continuation) -> None:
    """在 battle 上走 plies 个回合（第 1 回合按给定动作，后续用 continuation）。"""
    ag_me = FixedAgent(team, me, my_action, first_alive_bench)
    ag_op = FixedAgent("B" if team == "A" else "A", opp, their_action, first_alive_bench)
    a, b = (ag_me, ag_op) if team == "A" else (ag_op, ag_me)
    battle.execute_turn_headless(agent_a=a, agent_b=b,
                                 fixed_action_a=a.action, fixed_action_b=b.action)
    for _ in range(max(0, plies - 1)):
        if battle.is_finished or continuation is None:
            break
        act_a = continuation["A"].choose_action(battle)
        act_b = continuation["B"].choose_action(battle)
        battle.execute_turn_headless(
            agent_a=FixedAgent("A", battle.player_a, act_a, first_alive_bench),
            agent_b=FixedAgent("B", battle.player_b, act_b, first_alive_bench),
            fixed_action_a=act_a, fixed_action_b=act_b)
