# -*- coding: utf-8 -*-
"""E2 规划层（`backend/sim/plan.py`）的不变量测试。

规划层在**真实对局对象**上做 rollout，靠 `save_mutable_state()/restore_mutable_state()`
（+ RNG 快照）回滚。这里锁两条最关键的不变量：**局面不许泄漏**、**随机流不许泄漏** ——
一旦破了，规划臂就会在"被仿真污染过的局面"上做真实决策，A/B 数字也就不可信。
"""
from __future__ import annotations

import random

import pytest

from backend.sim import plan


@pytest.fixture()
def battle():
    import random as _random

    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    rng = _random.Random(2026)
    specs_a, _ = spec_from_team(load_meta_teams()[31], rng)
    specs_b, _ = spec_from_team(load_meta_teams()[0], rng)
    return factory.build_battle(factory.build_player("A", specs_a),
                                factory.build_player("B", specs_b))


def fingerprint(battle) -> tuple:
    """局面指纹：六维相关字段 + 效果层 + 印记 + 心力 + 计数器/VM 历史。

    计数器与 VM 历史必须在内：它们同样会在仿真回合里被就地改，而「快照被写坏」
    正是靠它们暴露的（2026-09-21 的事故里，只比六维与印记的旧指纹是绿的）。
    """
    parts = [battle.turn, battle.player_a.lives, battle.player_b.lives]
    for player in (battle.player_a, battle.player_b):
        parts.append(player.active_index)
        for sprite in player.team:
            eff = tuple(sorted(
                (getattr(e, "name", ""), getattr(e, "stacks", 0), getattr(e, "steps", 0),
                 getattr(e, "ttl", 0))
                for e in getattr(sprite, "active_effects", []) or []))
            parts.append((sprite.name, sprite.current_hp, sprite.energy,
                          tuple(sorted((sprite._modifiers or {}).items())), eff))
    marks = tuple(sorted(
        (team, tuple(sorted((m.name, getattr(m, "stacks", 0)) for m in lst)))
        for team, lst in (battle.globals.mark_effects or {}).items()))
    vm = battle._vm_engine
    containers = (
        marks,
        battle.globals.weather, battle.globals.weather_turns,
        tuple(sorted((t, tuple(sorted(c.items())))
                     for t, c in (battle.team_counters or {}).items())),
        tuple(sorted(vm._counter_values.items())),
        tuple(sorted((k, len(v)) for k, v in vm._skill_history.items())),
        tuple(sorted((k, tuple(sorted(v.items()))) for k, v in vm._skill_tags.items())),
        tuple(sorted((t, len(v)) for t, v in vm._burst_effects.items())),
        tuple(sorted((t, len(v)) for t, v in vm._burst_names.items())),
    )
    return tuple(parts) + containers


def _candidates(battle, team: str, limit: int = 4) -> list:
    from backend.sim.action import Action
    from backend.sim.agent import _skill_action

    player = battle.player_a if team == "A" else battle.player_b
    out = [_skill_action(i) for i, sk in enumerate(player.active.skills)
           if not sk.sealed and sk.cooldown <= 0 and sk.energy_cost <= player.active.energy]
    out.append(Action(kind="switch", switch_index=(
        1 if player.active_index == 0 else 0)))
    del _skill_action
    return out[:limit]


def test_choose_does_not_leak_battle_state(battle):
    before = fingerprint(battle)
    picked, info = plan.choose(battle, "A", _candidates(battle, "A"), plies=1,
                               k_responses=3, rng=random)
    assert picked is not None and info
    assert fingerprint(battle) == before, "rollout 后必须逐字段回到原局面"


def test_repeated_restore_returns_to_same_state(battle):
    """同一个快照反复 restore 都要回到原局面 —— 快照不能被 rollout 写坏。

    与 `test_choose_does_not_leak_battle_state` 的区别：那条只做一次 save/restore
    循环，且开局没有印记 - 计数器也是空的，于是「快照自己被子回合写坏」这种泄漏
    它看不见（2026-09-21 实测 40/40 局都在漏，这条测试却是绿的）。
    """
    from backend.sim.agent_v2 import RuleAgentV2, SpriteStrategy, TeamStrategy

    battle.globals.apply_mark("B", "星陨印记", "negative", 3, coexist=True)
    strat = TeamStrategy(default=SpriteStrategy(plan_depth=0, ev_decide=False))
    ag_a = RuleAgentV2("A", battle.player_a, strategy=strat)
    ag_b = RuleAgentV2("B", battle.player_b, strategy=strat)

    saved = battle.save_mutable_state()
    before = fingerprint(battle)
    for cycle in range(3):
        battle.execute_turn_headless(
            agent_a=ag_a, agent_b=ag_b,
            fixed_action_a=ag_a.choose_action(battle),
            fixed_action_b=ag_b.choose_action(battle))
        battle.restore_mutable_state(saved)
        assert fingerprint(battle) == before, f"第 {cycle + 1} 次回滚没回到原局面"


def test_choose_does_not_consume_rng(battle):
    state = random.getstate()
    plan.choose(battle, "A", _candidates(battle, "A"), plies=1, k_responses=3, rng=random)
    assert random.getstate() == state, "rollout 用的随机流必须回滚"


def test_empty_candidates_returns_none(battle):
    picked, info = plan.choose(battle, "A", [], plies=1, k_responses=3, rng=random)
    assert picked is None and info == {}


def test_single_candidate_is_returned(battle):
    one = _candidates(battle, "A")[:1]
    picked, _info = plan.choose(battle, "A", one, plies=1, k_responses=3, rng=random)
    assert picked is one[0]


def test_planner_is_deterministic(battle):
    """同一局面连续两次决策必须给出同一动作（否则 BC 数据不可复现）。"""
    cands = _candidates(battle, "A")
    first, _ = plan.choose(battle, "A", cands, plies=1, k_responses=3, rng=random)
    second, _ = plan.choose(battle, "A", cands, plies=1, k_responses=3, rng=random)
    assert first is not None and str(first) == str(second)


def test_response_set_is_bounded_and_usable(battle):
    picks = plan.response_actions(battle, battle.player_b, 3)
    assert 1 <= len(picks) <= 3
    for label, action in picks:
        assert label and action is not None
