"""叶子"临场血量加权"（时钟项）测试。

背景：打满回合上限时按血量差判胜（`core/outcome.py` 的 draw_margin 口径；30K 数据集里打满的
4749 局有 62% 是这样判出来的），而叶子对血量的权重与回合数无关。
`ValueParams.clock_hp_weight` = 越接近上限，越把"全队血量比差"当钱：
    v += clock_hp_weight × (turn / turn_cap) × (我血比 − 它血比)
默认 0.0 = 完全等于出厂行为。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.battle import Battle  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.traits import dispatch_entry  # noqa: E402
from backend.sim.value import DEFAULT_PARAMS, state_value  # noqa: E402

factory = SimFactory()


def _battle() -> Battle:
    p1 = factory.build_player("A", [{"name": "草衣虫", "skills": ["猛烈撞击"]}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def test_clock_term_is_off_by_default():
    battle = _battle()
    battle.player_b.team[0].current_hp = int(battle.player_b.team[0].max_hp * 0.5)
    battle.turn = 59
    assert state_value(battle, "A", DEFAULT_PARAMS) == state_value(battle, "A", DEFAULT_PARAMS)
    assert DEFAULT_PARAMS.clock_hp_weight == 0.0


def test_clock_term_amplifies_hp_lead_near_cap():
    battle = _battle()
    battle.player_b.team[0].current_hp = int(battle.player_b.team[0].max_hp * 0.5)
    on = dataclasses.replace(DEFAULT_PARAMS, clock_hp_weight=1.0)

    battle.turn = 1
    early = state_value(battle, "A", on) - state_value(battle, "A", DEFAULT_PARAMS)
    battle.turn = 59
    late = state_value(battle, "A", on) - state_value(battle, "A", DEFAULT_PARAMS)

    assert early > 0 and late > early * 20, (early, late)     # 越接近上限越当钱
    assert late < 1.0, late                                   # 但仍与基础项同量纲


def test_clock_term_is_zero_when_hp_equal():
    battle = _battle()
    on = dataclasses.replace(DEFAULT_PARAMS, clock_hp_weight=1.0)
    battle.turn = 59
    assert abs(state_value(battle, "A", on) - state_value(battle, "A", DEFAULT_PARAMS)) < 1e-9


def test_clock_term_penalises_hp_deficit():
    """血量落后时该项为负：临上限不该"躺平"，落后方该更积极。"""
    battle = _battle()
    battle.player_a.team[0].current_hp = int(battle.player_a.team[0].max_hp * 0.5)
    on = dataclasses.replace(DEFAULT_PARAMS, clock_hp_weight=1.0)
    battle.turn = 59
    assert state_value(battle, "A", on) < state_value(battle, "A", DEFAULT_PARAMS)
