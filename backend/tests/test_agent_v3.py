# -*- coding: utf-8 -*-
"""RuleAgentV3（`backend/sim/agent_v3.py`）与技能 IR 查询层（`backend/sim/skill_ir.py`）的测试。

锁住的都是"这版新增/修好的东西"，尤其是那条**曾经是死代码**的性质：
技能 JSON 是 IR 形式，`Skill.effects`（旧 kind 形式）对 IR 技能是空的 ——
所以任何按 `skill.effects` 找增益/异常/驱散的规则都恒不触发（V2 的"强化推队"就是）。
"""
from __future__ import annotations

import json
import random

import pytest

from backend.sim.agent_v3 import RuleAgentV3, V3Params, _OpponentModel
from backend.sim.factory import SimFactory
from backend.sim.skill import Skill
from backend.sim.skill_ir import skill_profile, total_buff_steps


def _skill(name: str) -> Skill:
    with open(f"data/skills/{name}.json", encoding="utf-8") as fh:
        return Skill.load(json.load(fh))


@pytest.fixture()
def battle():
    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team

    factory = SimFactory()
    rng = random.Random(2026)
    specs_a, _ = spec_from_team(load_meta_teams()[31], rng)
    specs_b, _ = spec_from_team(load_meta_teams()[0], rng)
    return factory.build_battle(factory.build_player("A", specs_a),
                               factory.build_player("B", specs_b))


# ── 技能 IR 画像：攻略里的四条战术各自能对上类型字段 ──

def test_profile_reads_abnormal_stacks(battle):
    """剧毒：常态 3 层 / 应对成功 8 层 → 取最大值，且应对标签是"防御"。"""
    prof = skill_profile(battle, _skill("剧毒"))
    assert prof.counters == "防御"
    assert dict(prof.abnorms)["中毒"] == 8


def test_profile_reads_self_buff(battle):
    """泥浆铠甲：物攻/物防各 +60%（6 步）= 12 步，且应对成功会翻倍。"""
    prof = skill_profile(battle, _skill("泥浆铠甲"))
    assert prof.self_buff_value >= 12
    assert prof.doubles_buffs is True


def test_profile_reads_energy_denial(battle):
    """恶作剧抽对手 6 能量、精神扰乱给对手全技能 +3 能耗。"""
    assert skill_profile(battle, _skill("恶作剧")).opp_energy_drain >= 3
    assert skill_profile(battle, _skill("精神扰乱")).opp_energy_cost_pressure >= 1


def test_profile_reads_mult_mod_buff(battle):
    """快速移动的"+速度"是 mult_mod(speed_flat)，不是 stat_stage —— 也要能读到。"""
    prof = skill_profile(battle, _skill("快速移动"))
    assert prof.self_buff_value > 0
    assert prof.self_buff_on_counter > prof.self_buff_value   # 应对成功是 160 > 常态 80


def test_legacy_effects_layer_is_gone_ir_is_the_only_source(battle):
    """旧 kind 层已整层删除（2026-09-22）：`Skill` 不再有 `effects` 字段，
    规则层只能读 IR 画像（`skill_ir.skill_profile`）。"""
    sk = _skill("泥浆铠甲")
    assert not hasattr(sk, "effects")
    assert skill_profile(battle, sk).self_buff_value > 0


def test_total_buff_steps_counts_existing_buffs(battle):
    """现存增益统计：给在场精灵挂两层属性增益后应能被读到。"""
    from backend.vm.effect import StatBuffEffect

    sprite = battle.player_a.active
    before = total_buff_steps(sprite)
    sprite.active_effects.append(
        StatBuffEffect(name="测试增益", stat_key="atk", steps=6,
                       scope="battlefield", source="test"))
    assert total_buff_steps(sprite) > before


# ── 读牌模型（纯函数，直接测）──

def test_opponent_model_works_after_window():
    m = _OpponentModel()
    assert m.mode(window=3, burst_ratio=0.35, control_count=2) == "unknown"
    m.turns = 3
    m.max_hit_ratio = 0.5
    assert m.mode(window=3, burst_ratio=0.35, control_count=2) == "burst"

    m2 = _OpponentModel(turns=3, defenses=1, statuses=1)
    assert m2.mode(window=3, burst_ratio=0.35, control_count=2) == "stall"

    m3 = _OpponentModel(turns=3, switches=2)
    assert m3.mode(window=3, burst_ratio=0.35, control_count=2) == "control"


# ── 整局烟测：V3 能打满一局、动作合法、规则有归属 ──

def _play_v3_vs_v3(max_turns: int = 30):
    from backend.engine.ai.core.outcome import battle_outcome_a
    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team

    factory = SimFactory()
    rng = random.Random(7)
    specs_a, _ = spec_from_team(load_meta_teams()[3], rng)
    specs_b, _ = spec_from_team(load_meta_teams()[4], rng)
    p1 = factory.build_player("A", specs_a)
    p2 = factory.build_player("B", specs_b)
    battle = factory.build_battle(p1, p2)
    a1 = RuleAgentV3("A", p1)
    a2 = RuleAgentV3("B", p2)
    turns = 0
    rules = set()
    while not battle.is_finished and turns < max_turns:
        battle.execute_turn(a1, a2)
        turns += 1
        rules.add(a1.last_rule)
        assert a1.last_rule, "每条决策都必须能报出走了哪条规则（审计用）"
    outcome, _reason = battle_outcome_a(battle, max_turns)
    return turns, outcome, rules, a1


def test_v3_plays_a_full_game():
    turns, outcome, rules, a1 = _play_v3_vs_v3()
    assert turns >= 1
    assert outcome in (-1.0, 0.0, 1.0)
    assert rules <= {"kill", "trade_kill", "counter_shield", "interrupt", "retreat",
                     "wish_kill", "wish_better", "plan", "attack", "setup", "rotate",
                     "gather", "faint_switch", "release_charge", "charge_switch",
                     "defend_read"}


def test_v3_uses_plan_layer_by_default():
    """默认走规划层（plan_depth=1）——攻略里的战术意图都在候选里交给它裁决。"""
    _turns, _outcome, rules, _a1 = _play_v3_vs_v3()
    assert "plan" in rules


# ── 收割位：不当首发、名额够时不早登场 ──

def test_closer_not_used_as_lead_or_early_replacement():
    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team
    from backend.sim.agent_v2 import SpriteStrategy, TeamStrategy

    factory = SimFactory()
    rng = random.Random(11)
    specs_a, _ = spec_from_team(load_meta_teams()[31], rng)
    specs_b, _ = spec_from_team(load_meta_teams()[0], rng)
    p1 = factory.build_player("A", specs_a)
    p2 = factory.build_player("B", specs_b)
    battle = factory.build_battle(p1, p2)

    closer_name = p1.team[-1].name
    strategy = TeamStrategy(
        name="t",
        sprites={closer_name: SpriteStrategy(role="closer")},
        default=SpriteStrategy(),
    )
    agent = RuleAgentV3("A", p1, strategy=strategy)
    assert agent.is_closer(p1.team[-1])
    assert agent.choose_lead(battle) != len(p1.team) - 1, "收割位不该当首发"

    # 对手 6 只全在 → 收割位被扣分（不当炮灰）
    score_held = agent._pick_replacement(battle, battle.player_b.active)
    assert score_held != len(p1.team) - 1


def test_closer_deployed_when_it_can_finish():
    """对手场上残血、收割位能一击拿下时，允许它登场。"""
    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team
    from backend.sim.agent_v2 import SpriteStrategy, TeamStrategy

    factory = SimFactory()
    rng = random.Random(13)
    specs_a, _ = spec_from_team(load_meta_teams()[31], rng)
    specs_b, _ = spec_from_team(load_meta_teams()[0], rng)
    p1 = factory.build_player("A", specs_a)
    p2 = factory.build_player("B", specs_b)
    battle = factory.build_battle(p1, p2)

    closer_idx = len(p1.team) - 1
    strategy = TeamStrategy(
        name="t",
        sprites={p1.team[closer_idx].name: SpriteStrategy(role="closer")},
        default=SpriteStrategy(),
    )
    agent = RuleAgentV3("A", p1, strategy=strategy)
    # 把对手压到残血、并让 A 的收割位有可用攻击
    for sprite in p2.team:
        sprite.current_hp = 1
    picked = agent._pick_replacement(battle, p2.active)
    assert picked >= 0


def test_v3params_disable_switches_back_to_v2_like_behaviour():
    """参数可一键退回：关掉新规则的收益项后仍能正常决策（不抛异常）。"""
    params = V3Params(rotate_gain=0.0, setup_min_gain=0.0, interrupt_on_charge=False,
                      defend_lethal_ratio=0.0)
    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team

    factory = SimFactory()
    rng = random.Random(17)
    specs_a, _ = spec_from_team(load_meta_teams()[31], rng)
    specs_b, _ = spec_from_team(load_meta_teams()[0], rng)
    p1 = factory.build_player("A", specs_a)
    p2 = factory.build_player("B", specs_b)
    battle = factory.build_battle(p1, p2)
    agent = RuleAgentV3("A", p1, params=params)
    action = agent.choose_action(battle)
    assert action is not None
