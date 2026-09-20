# -*- coding: utf-8 -*-
"""首领形态外观链路验证：外观独立条目 + 首领化保留外观。

对应数据模型（form=阶段 / appearance=外观）：
  1. 外观条目可作为独立精灵构建（build_player 支持 "名字（外观）"）。
  2. 使用「进化之力」首领化时保留当前外观
     （名字（外观）→ 首领名（同外观）；无同外观时回退默认外观）。
"""
from __future__ import annotations

import pytest

from backend.sim.factory import SimFactory
from backend.sim.player import Item


@pytest.fixture(scope="module")
def factory():
    return SimFactory()


def _read_all(factory, name: str):
    db = factory.sprite_db
    return [s for s in (db._read_one(p) for p in db._by_name.get(name, [])) if s]


def _pick_family(factory):
    """找一个『基础阶段 + 首领阶段（同编号）都有同一外观』的精灵。

    首领形态与基础形态共用 number 但名字不同（如 鸭吉吉 / 鸭吉吉国王），
    因此按编号分组查找。
    """
    db = factory.sprite_db
    for number in db._by_number:
        entries = [s for s in (db._read_one(p) for p in db._by_number[number]) if s]
        base = [s for s in entries if not s.is_leader_stage()]
        boss = [s for s in entries if s.is_leader_stage()]
        shared = ({s.appearance for s in base if s.appearance}
                  & {s.appearance for s in boss if s.appearance})
        if base and boss and shared:
            return base[0].name, sorted(shared)[0], boss[0].name
    return None


def test_appearance_entry_builds(factory):
    """池键（名字（外观））可直接构建精灵，外观挂在 species 上。"""
    db = factory.sprite_db
    candidate = None
    for name in db._by_name:
        appearances = [a for a in db.list_appearances(name) if a]
        if appearances:
            candidate = (name, appearances[0])
            break
    if candidate is None:
        pytest.skip("数据中没有带外观的精灵")
    name, appearance = candidate
    sprite = factory.build_sprite(name, skills=[], appearance=appearance)
    assert sprite.species.name == name
    assert sprite.species.appearance == appearance


def test_leader_item_keeps_appearance(factory):
    """进化之力首领化：保留当前外观（不是跳到默认外观）。"""
    picked = _pick_family(factory)
    if picked is None:
        pytest.skip("数据中没有『基础+首领同外观』的精灵")
    name, appearance, boss_name = picked

    spec = [{"name": name, "skills": [], "appearance": appearance}]
    p1 = factory.build_player("A", spec, item=Item.leader())
    p2 = factory.build_player("B", spec, item=Item.leader())
    sprite = p1.team[0]
    sprite.bloodline = "首领"
    battle = factory.build_battle(p1, p2)
    battle.turn = 1

    battle._resolve_item("A")
    assert sprite.species.name == boss_name
    assert "首领" in sprite.species.form
    assert sprite.species.appearance == appearance


def test_leader_form_cannot_evolve_again(factory):
    """首领形态不能再进化（即便带首领血脉）；动作掩码也不该给道具。"""
    picked = _pick_family(factory)
    if picked is None:
        pytest.skip("数据中没有『基础+首领同外观』的精灵")
    _base_name, appearance, boss_name = picked

    spec = [{"name": boss_name, "skills": [], "appearance": appearance}]
    p1 = factory.build_player("A", spec, item=Item.leader())
    p2 = factory.build_player("B", spec, item=Item.leader())
    sprite = p1.team[0]
    sprite.bloodline = "首领"
    battle = factory.build_battle(p1, p2)
    battle.turn = 1

    assert battle.item_usable("A") is False
    before = sprite.species.name
    battle._resolve_item("A")
    assert sprite.species.name == before

    from backend.engine.ai.core.mcts import ITEM_ACTION_IDX, ITEM_VARIANT_ACTION_BASE, get_valid_actions
    _, mask = get_valid_actions(p1, battle)
    assert mask[ITEM_ACTION_IDX] == 0
    assert mask[ITEM_VARIANT_ACTION_BASE:].sum() == 0


def _pick_multi_boss_family(factory):
    """找一个候选首领形态 ≥2 的基础精灵（如 迪莫 → 圣光/圣水/圣火/圣草迪莫）。

    以「基础形态实际会拿到的候选列表」为准（同外观 + 默认外观），
    而不是同编号首领形态总数——后者只对默认外观的基础形态成立。
    """
    db = factory.sprite_db
    for number in db._by_number:
        entries = [s for s in (db._read_one(p) for p in db._by_number[number]) if s]
        for base in (s for s in entries if not s.is_leader_stage()):
            cands = db.leader_form_candidates(number, base.appearance)
            if len(cands) >= 2:
                return base, cands
    return None


def test_leader_form_variants_are_player_choice(factory):
    """多首领形态家族：候选列表 + 动作掩码 17-21 + 指定槽位首领化到该形态。"""
    from backend.common.constants import ITEM_VARIANT_ACTION_BASE, ITEM_VARIANT_SLOTS
    from backend.engine.ai.core.mcts import get_valid_actions

    picked = _pick_multi_boss_family(factory)
    if picked is None:
        pytest.skip("数据中没有多首领形态家族")
    base, bosses = picked
    assert len(bosses) <= ITEM_VARIANT_SLOTS, "候选数超过动作槽位，需扩槽"

    spec = [{"name": base.display_name(), "skills": []}]
    p1 = factory.build_player("A", spec, item=Item.leader())
    p2 = factory.build_player("B", spec, item=Item.leader())
    sprite = p1.team[0]
    sprite.bloodline = "首领"
    battle = factory.build_battle(p1, p2)
    battle.turn = 1

    variants = battle.item_variants("A")
    assert [v.name for v in variants] == [b.name for b in bosses]
    _, mask = get_valid_actions(p1, battle)
    for k in range(len(variants)):
        assert mask[ITEM_VARIANT_ACTION_BASE + k] == 1.0
    # 多候选家族不再使用「愿力槽」16
    assert mask[16] == 0.0

    # 选第 2 个候选 → 应该变到那个形态（而不是候选首位）
    target_idx = 1
    battle._resolve_item("A", target_idx)
    assert sprite.species.name == bosses[target_idx].name
    assert "首领" in sprite.species.form


def test_single_boss_family_uses_slot_zero(factory):
    """单候选家族：缺省 variant 与显式 0 都落到同一形态（旧行为兼容）。"""
    picked = _pick_family(factory)
    if picked is None:
        pytest.skip("数据中没有『基础+首领同外观』的精灵")
    name, appearance, boss_name = picked

    spec = [{"name": name, "skills": [], "appearance": appearance}]
    p1 = factory.build_player("A", spec, item=Item.leader())
    p2 = factory.build_player("B", spec, item=Item.leader())
    sprite = p1.team[0]
    sprite.bloodline = "首领"
    battle = factory.build_battle(p1, p2)
    battle.turn = 1

    assert battle._resolve_item("A") == "进化之力"
    assert sprite.species.name == boss_name
    assert battle._resolve_item("A", 0) == ""  # 道具已用尽
