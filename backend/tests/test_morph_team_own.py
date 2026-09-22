"""变身（技能顶层 `morph` 字段）测试 — 借用 / 复写。

契约见 `data/IR_GUIDE.md`「变身」小节：
- 每回合开始时，**场上**精灵的变身槽变为池中随机技能（池 = 己方队伍**其他精灵**的技能）；
- `exclude_owned: true` 再剔除施法者已携带的技能（复写「自己未携带的技能」）；
- `energy_delta` 给产物加减能耗（复写 -2）；
- 不写 `_morph_temp`（不吃巧变的能耗-1，也不会"用掉后还原"）；
- 池为空 → 不替换，槽位保持原技能。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.engine import morph  # noqa: E402
from backend.sim.action import Action  # noqa: E402
from backend.sim.battle import Battle  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.sim.traits import dispatch_entry  # noqa: E402

factory = SimFactory()


def _battle(team_a: list[dict], team_b: list[dict] | None = None) -> Battle:
    team_b = team_b or [{"name": "草衣虫", "skills": ["猛烈撞击", "防御"]}]
    p1 = factory.build_player("A", team_a)
    p2 = factory.build_player("B", team_b)
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _slot_of(sprite, name: str):
    for i, bs in enumerate(sprite.skills):
        if bs.base.name == name:
            return i, bs
    raise AssertionError(f"{sprite.name} 没有技能 {name}")


# ── 池 ────────────────────────────────────────────────────────────

def test_borrow_pool_is_teammates_skills():
    """借用：池 = 己方其他精灵的技能（不含施法者自己的技能）。"""
    battle = _battle([
        {"name": "花衣蝶", "skills": ["借用", "猛烈撞击", "防御", "甩水"]},
        {"name": "草衣虫", "skills": ["落雷", "电弧"]},
    ])
    a = battle.player_a.active
    _i, bs = _slot_of(a, "借用")
    pool = morph._build_team_own(battle, a, bs, dict(bs.base.morph))
    assert set(pool) == {"落雷", "电弧"}, pool


def test_copy_pool_excludes_own_kit():
    """复写：`exclude_owned` 剔除自己已携带的技能（wiki「自己未携带的技能」）。"""
    battle = _battle([
        {"name": "花衣蝶", "skills": ["复写", "落雷", "防御", "甩水"]},
        {"name": "草衣虫", "skills": ["落雷", "电弧"]},
    ])
    a = battle.player_a.active
    _i, bs = _slot_of(a, "复写")
    assert bs.base.morph.get("exclude_owned") is True
    pool = morph._build_team_own(battle, a, bs, dict(bs.base.morph))
    assert set(pool) == {"电弧"}, pool          # 落雷在自己身上 → 不进池


def test_pool_empty_without_teammates():
    battle = _battle([{"name": "花衣蝶", "skills": ["借用", "猛烈撞击"]}])
    a = battle.player_a.active
    _i, bs = _slot_of(a, "借用")
    assert morph._build_team_own(battle, a, bs, dict(bs.base.morph)) == ()
    assert morph.apply_henshin(battle, "A", a) == ""
    assert bs.replaced_by is None


# ── 重掷 ──────────────────────────────────────────────────────────

def test_apply_henshin_replaces_slot_and_is_deterministic():
    team = [
        {"name": "花衣蝶", "skills": ["借用", "猛烈撞击", "防御", "甩水"]},
        {"name": "草衣虫", "skills": ["落雷", "电弧"]},
    ]
    pool = {"落雷", "电弧"}

    def run_once(seed: int):
        battle = _battle(team)
        a = battle.player_a.active
        _i, bs = _slot_of(a, "借用")
        random.seed(seed)
        text = morph.apply_henshin(battle, "A", a)
        return bs, text

    bs1, t1 = run_once(2026)
    bs2, _t2 = run_once(2026)
    assert bs1.replaced_by is not None and bs1.replaced_by.name in pool
    assert bs2.replaced_by is not None and bs2.replaced_by.name == bs1.replaced_by.name
    assert "变身 →" in t1
    # 不是巧变：不标记 `_morph_temp`
    assert getattr(bs1, "_morph_temp", False) is False
    # 产物按 变身 语义可见（槽位有效技能 = replaced_by）
    assert bs1.name == bs1.replaced_by.name


def test_henshin_rerolled_every_turn_for_active_only():
    """回合开始重掷：只对场上精灵生效；场下同技能不动。"""
    battle = _battle([
        {"name": "花衣蝶", "skills": ["借用", "猛烈撞击", "防御", "甩水"]},
        {"name": "草衣虫", "skills": ["借用", "落雷"]},
    ])
    active = battle.player_a.active
    bench = battle.player_a.team[1]
    _i, bs_active = _slot_of(active, "借用")
    _j, bs_bench = _slot_of(bench, "借用")

    random.seed(7)
    battle._phase_turn_start()
    assert bs_active.replaced_by is not None
    assert bs_bench.replaced_by is None, "场下精灵不该在回合开始被重掷"


def test_rollback_restores_henshin_slot():
    """回滚安全：变身产物随 `save_mutable_state`/`restore_mutable_state` 一起还原。"""
    battle = _battle([
        {"name": "花衣蝶", "skills": ["借用", "猛烈撞击", "防御", "甩水"]},
        {"name": "草衣虫", "skills": ["落雷", "电弧"]},
    ])
    a = battle.player_a.active
    _i, bs = _slot_of(a, "借用")
    saved = battle.save_mutable_state()
    random.seed(11)
    morph.apply_henshin(battle, "A", a)
    assert bs.replaced_by is not None
    battle.restore_mutable_state(saved)
    assert bs.replaced_by is None, "回滚后变身槽位必须回到原技能"


# ── 能耗修正 ──────────────────────────────────────────────────────

def test_copy_energy_delta_applies_to_product():
    """复写：产物的能耗 -2（wiki「且该技能能耗-2」）。"""
    battle = _battle([
        {"name": "花衣蝶", "skills": ["复写", "猛烈撞击", "防御", "甩水"]},
        {"name": "草衣虫", "skills": ["落雷", "电弧"]},
    ])
    a = battle.player_a.active
    idx, bs = _slot_of(a, "复写")
    a.energy = 10
    random.seed(3)
    morph.apply_henshin(battle, "A", a)
    product = bs.replaced_by
    assert product is not None
    assert battle.skill_energy_cost("A", a, bs, idx) == max(0, product.energy_cost - 2)

    # 巧变（_morph_temp）走的是另一条修正，不叠在变身产物上
    bs.replaced_by = None
    assert battle.skill_energy_cost("A", a, bs, idx) == bs.base.energy_cost
    assert morph.henshin_cost_delta(bs) == 0


def test_borrow_has_no_energy_delta():
    battle = _battle([
        {"name": "花衣蝶", "skills": ["借用", "猛烈撞击", "防御", "甩水"]},
        {"name": "草衣虫", "skills": ["落雷", "电弧"]},
    ])
    a = battle.player_a.active
    idx, bs = _slot_of(a, "借用")
    a.energy = 10
    random.seed(5)
    morph.apply_henshin(battle, "A", a)
    product = bs.replaced_by
    assert battle.skill_energy_cost("A", a, bs, idx) == product.energy_cost
