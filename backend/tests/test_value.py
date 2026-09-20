# -*- coding: utf-8 -*-
"""E1 叶子估值（`backend/sim/value.py`）的单调性与对称性测试。

v1 的权重是手设值，**只保证符号与单调性**：好坏方向不能反、同局面两侧必须互为相反数。
绝对量级靠 E0 的 regret 与同局配对 A/B 校准，不在这里断言。
"""
from __future__ import annotations

import pytest

from backend.sim.value import ValueParams, sprite_effect_value, state_value, team_value
from backend.vm.effect import AbnormalEffect, MarkEffect, StatBuffEffect

PARAMS = ValueParams()


@pytest.fixture()
def battle():
    from backend.sim.factory import SimFactory

    factory = SimFactory()
    from backend.engine.ai.data.meta_teams import load_meta_teams, spec_from_team
    import random

    rng = random.Random(2026)
    specs_a, _ = spec_from_team(load_meta_teams()[31], rng)
    specs_b, _ = spec_from_team(load_meta_teams()[0], rng)
    p1 = factory.build_player("A", specs_a)
    p2 = factory.build_player("B", specs_b)
    return factory.build_battle(p1, p2)


def _add_effect(sprite, effect):
    sprite.active_effects.append(effect)


def test_state_value_is_antisymmetric(battle):
    va = state_value(battle, "A", PARAMS)
    vb = state_value(battle, "B", PARAMS)
    assert abs(va + vb) < 1e-9, f"两侧必须互为相反数：{va} vs {vb}"


def test_team_value_matches_official_base_terms(battle):
    """基础项必须与官方局面分同量纲（效果层为 0 时两者只差在场血量权重）。"""
    from backend.engine.ai.core.outcome import team_battle_score

    official = team_battle_score(battle.player_a)
    mine = team_value(battle, battle.player_a, "A", ValueParams(active_hp=0.0))
    assert mine == pytest.approx(official, abs=1e-9)


def test_positive_stat_buff_raises_holder_value(battle):
    sprite = battle.player_a.active
    effect_before = sprite_effect_value(battle, sprite, PARAMS)
    state_before = state_value(battle, "A", PARAMS)
    _add_effect(sprite, StatBuffEffect(name="测试强化", source="test", stat_key="atk", steps=2))
    assert sprite_effect_value(battle, sprite, PARAMS) > effect_before
    assert state_value(battle, "A", PARAMS) > state_before


def test_debuff_on_opponent_raises_my_value(battle):
    """给对手挂减益 → 我的分差必须变大。"""
    before = state_value(battle, "A", PARAMS)
    _add_effect(battle.player_b.active,
                StatBuffEffect(name="测试削弱", source="test", stat_key="atk", steps=-3))
    after = state_value(battle, "A", PARAMS)
    assert after > before


def test_abnormal_stacks_on_me_lowers_my_value(battle):
    before = state_value(battle, "A", PARAMS)
    _add_effect(battle.player_a.active, AbnormalEffect(
        name="测试中毒", source="test", stacks=3, tick_damage_pct=0.05, tick_per_stack=True))
    after = state_value(battle, "A", PARAMS)
    assert after < before, "自侧异常层数必须降低自己的分数"


def test_negative_mark_on_my_side_lowers_value_and_on_theirs_raises(battle):
    before = state_value(battle, "A", PARAMS)
    mark_b = MarkEffect(name="测试印记", source="test", category="negative", stacks=2,
                        turn_end_damage_pct=0.05, switch_damage_pct=0.03)
    battle.globals.mark_effects.setdefault("B", []).append(mark_b)
    after = state_value(battle, "A", PARAMS)
    assert after > before, "对手侧的负面印记应当抬高我的分数"
    mark_a = MarkEffect(name="测试印记", source="test", category="negative", stacks=2,
                        turn_end_damage_pct=0.05, switch_damage_pct=0.03)
    battle.globals.mark_effects.setdefault("A", []).append(mark_a)
    worst = state_value(battle, "A", PARAMS)
    assert worst < after, "自侧印记（末伤+进场伤害）必须压低我的分数"


def test_fainted_sprite_lowers_value(battle):
    before = state_value(battle, "A", PARAMS)
    battle.player_a.active.current_hp = 0
    assert battle.player_a.active.is_fainted
    after = state_value(battle, "A", PARAMS)
    assert after < before - PARAMS.alive * 0.9, "力竭要按存活数权重扣分"


def test_bench_effects_count_less_than_active(battle):
    bench = [s for i, s in enumerate(battle.player_a.team) if i != battle.player_a.active_index][0]
    active_before = sprite_effect_value(battle, battle.player_a.active, PARAMS)
    bench_before = sprite_effect_value(battle, bench, PARAMS)
    eff = StatBuffEffect(name="测试强化", source="test", stat_key="def", steps=1)
    _add_effect(battle.player_a.active, eff)
    _add_effect(bench, eff)
    assert (sprite_effect_value(battle, battle.player_a.active, PARAMS) - active_before) == \
        pytest.approx(sprite_effect_value(battle, bench, PARAMS) - bench_before), \
        "同一效果在场上与板凳上的单只价值相同（差异由 team_value 的 weight 体现）"
