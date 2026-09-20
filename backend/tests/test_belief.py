"""backend/tests/test_belief.py — 对手动作概率模型（`backend/sim/belief.py`）。

钉住的是**可解释的方向性**，不是具体数值（数值是手设先验、允许后续用数据校准）：
  - 概率必须归一、不可用分支必须为 0（没替补 → P(换)=0；防御技在冷却 → 防御列 = 0）；
  - 信号单调：我能斩杀它 / 它被 tick 死 / 它没能量 / 它残血 → P(换人) 上升；
    它刚换上过 → P(换人) 下降；
  - 我这回合的威胁越大 → 它越倾向防御、越不倾向原地强化；
  - 校准参数能存能读、缺字段回退默认。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim import belief as B
from backend.sim.factory import SimFactory

factory = SimFactory()


def _battle(a_specs, b_specs):
    p1 = factory.build_player("A", a_specs)
    p2 = factory.build_player("B", b_specs)
    return factory.build_battle(p1, p2)


def _with_bench():
    """对手（B）有替补的两只阵容；我方（A）单只，便于控制变量。"""
    return _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                   [{"name": "雪怪", "skills": ["猛烈撞击", "甩水"]},
                    {"name": "草衣虫", "skills": ["猛烈撞击"]}])


def test_belief_normalized_and_columns():
    b = _with_bench()
    dist = B.belief(b, "A", b.player_b, my_best_dmg=40)
    assert set(dist) == set(B.SCENARIOS)
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    assert all(0.0 <= v <= 1.0 for v in dist.values())
    assert dist["attack"] > 0            # 有攻击技、能量够 → 攻击列必须有概率


def test_no_bench_means_no_switch():
    """对手没有替补 → P(换人) 必须是 0（换不了）。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                [{"name": "雪怪", "skills": ["猛烈撞击"]}])
    assert B.switch_probability(b, "A", b.player_b, my_best_dmg=999) == 0.0
    assert B.belief(b, "A", b.player_b, 999)["switch"] == 0.0


def test_lethal_and_tick_doom_raise_switch_probability():
    b = _with_bench()
    base = B.switch_probability(b, "A", b.player_b, my_best_dmg=10)
    lethal = B.switch_probability(b, "A", b.player_b, my_best_dmg=999)
    assert lethal > base

    from backend.vm.effect import AbnormalEffect

    b.player_b.active.active_effects.append(
        AbnormalEffect(name="中毒", source="测试", stacks=40,
                       tick_damage_pct=0.05, tick_per_stack=True))
    doomed = B.switch_probability(b, "A", b.player_b, my_best_dmg=10)
    assert doomed > base


def test_low_hp_and_no_energy_raise_switch_probability():
    b = _with_bench()
    base = B.switch_probability(b, "A", b.player_b, my_best_dmg=10)
    low_hp = _with_bench()
    low_hp.player_b.active.current_hp = max(1, int(low_hp.player_b.active.max_hp * 0.2))
    assert B.switch_probability(low_hp, "A", low_hp.player_b, 10) > base

    drained = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                      [{"name": "雪怪", "skills": ["猛烈撞击"]},   # 唯一攻击技 1 费
                       {"name": "草衣虫", "skills": ["猛烈撞击"]}])
    drained.player_b.active.energy = 0        # 打不出任何攻击（wiki：无能量 → 聚能或换人）
    assert B.switch_probability(drained, "A", drained.player_b, 10) > base
    assert B.belief(drained, "A", drained.player_b, 10)["attack"] == 0.0   # 打不了 → 攻击列归零


def test_just_entered_lowers_switch_probability():
    b = _with_bench()
    b.turn = 5
    b.player_b.active.entry_turn = 5          # 刚换上过
    just = B.switch_probability(b, "A", b.player_b, my_best_dmg=10)
    b.player_b.active.entry_turn = 1          # 站场很久了
    settled = B.switch_probability(b, "A", b.player_b, my_best_dmg=10)
    assert just < settled


def test_defense_column_needs_usable_defense_skill():
    """防御列只在它真的有可用防御技时出现（wiki：防御用完进一回合冷却）。"""
    no_def = _with_bench()
    assert B.belief(no_def, "A", no_def.player_b, 40)["defense"] == 0.0

    with_def = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                       [{"name": "水灵", "skills": ["猛烈撞击", "火焰护盾"]},
                        {"name": "草衣虫", "skills": ["猛烈撞击"]}])
    dist = B.belief(with_def, "A", with_def.player_b, 999)   # 我威胁极大 → 它更想防御
    assert dist["defense"] > 0.0

    with_def.player_b.active.skills[1].cooldown = 2          # 进冷却 → 不能防御
    assert B.belief(with_def, "A", with_def.player_b, 999)["defense"] == 0.0


def test_threat_shifts_weight_from_status_to_defense():
    """我威胁大 → 它更倾向防御、更不倾向原地强化。"""
    b = _battle([{"name": "水灵", "skills": ["猛烈撞击"]}],
                [{"name": "水灵", "skills": ["猛烈撞击", "火焰护盾", "冥想"]},
                 {"name": "草衣虫", "skills": ["猛烈撞击"]}])
    weak = B.belief(b, "A", b.player_b, 1)
    strong = B.belief(b, "A", b.player_b, 9999)
    assert strong["defense"] >= weak["defense"]
    assert strong["status"] <= weak["status"]


def test_calibrated_params_roundtrip(tmp_path):
    """校准参数能存能读；坏文件/缺字段回退默认。"""
    path = tmp_path / "belief.json"
    tuned = B.BeliefParams(switch_base=0.5, w_lethal=3.0)
    B.save_params(tuned, path)
    loaded = B.load_calibrated(path)
    assert loaded.switch_base == pytest.approx(0.5)
    assert loaded.w_lethal == pytest.approx(3.0)
    assert loaded.w_hp_low == B.BeliefParams().w_hp_low      # 未写的字段保持默认

    path.write_text("{ 坏 json", encoding="utf-8")
    assert B.load_calibrated(path) == B.BeliefParams()
    assert B.load_calibrated(tmp_path / "不存在.json") == B.BeliefParams()


def test_features_exposed_for_calibration():
    b = _with_bench()
    f = B.features(b, "A", b.player_b, my_best_dmg=999)
    assert f["lethal"] == 1.0 and f["has_bench"] == 1.0
    assert 0.0 <= f["my_threat"] <= 1.0
