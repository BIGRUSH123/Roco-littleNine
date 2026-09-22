"""`burst_grant from:"triggered"`（踏雷）—— data/IR_GUIDE.md §3D burst_grant。

口径：
- 来源池 = `BattleVMEngine._burst_effects[team]`，每个「以迸发身份使用过的技能」记一条
  `(技能名, 该技能完整效果列表)`，按触发时间升序；
- `count: N` 取**最近 N 条**，`count: "all"`（或 ≤0）取全部；池空 → 空操作；
- 授予写在命中技能槽的 `_burst_effects` 上，等该技能下一次以迸发身份行动时执行。
"""

from __future__ import annotations

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.pipeline import TurnPipeline
from backend.sim.traits import dispatch_entry

factory = SimFactory()


def make_battle(skills_a):
    p1 = factory.build_player("A", [{"name": "神谕鲨", "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    for sprite in list(p1.team) + list(p2.team):
        sprite.energy = 10
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def use(battle, team, idx, branch=None, **kw):
    return battle._execute_skill_vm(
        team, Action("skill", skill_index=idx, branch=branch), **kw)


def pool(battle, team="A"):
    return [(name, len(effects)) for name, effects in battle._vm_engine._burst_effects[team]]


def granted(battle):
    return {bs.name: len(bs._burst_effects) for bs in battle.player_a.active.skills}


def seed_two_bursts(battle):
    """让池里有两条第（星火、奇点），都是真实「以迸发身份使用」登记的。"""
    use(battle, "A", 0, is_first=True)          # 星火
    battle.player_a.active.first_action = True   # 模拟返场/重新入场
    use(battle, "A", 1, is_first=True)          # 奇点
    battle.player_a.active.first_action = False


def test_triggered_pool_records_burst_skills():
    battle = make_battle(["星火", "奇点", "踏雷", "猛烈撞击"])
    assert pool(battle) == []
    use(battle, "A", 0, is_first=True)
    assert [n for n, _ in pool(battle)] == ["星火"]
    battle.player_a.active.first_action = True
    use(battle, "A", 1, is_first=True)
    assert [n for n, _ in pool(battle)] == ["星火", "奇点"]


def test_tailei_count_one_takes_most_recent_entry():
    battle = make_battle(["星火", "奇点", "踏雷", "猛烈撞击"])
    seed_two_bursts(battle)
    last_effects = len(battle._vm_engine._burst_effects["A"][-1][1])
    assert last_effects > 0

    use(battle, "A", 2, is_first=False)          # 踏雷：非应对 → count: 1

    got = granted(battle)
    assert got["奇点"] == last_effects            # 攻击技能命中
    assert got["猛烈撞击"] == last_effects
    assert got["星火"] == 0                       # 状态技能不命中（skill_filter:"attack"）


def test_tailei_counter_success_grants_all_triggered():
    battle = make_battle(["星火", "奇点", "踏雷", "猛烈撞击"])
    seed_two_bursts(battle)
    total = sum(n for _, n in pool(battle))

    use(battle, "A", 2, is_first=False,
        countered_skill=battle.player_b.active.skills[0])   # 应对成功 → count: "all"

    got = granted(battle)
    assert total > 0
    assert got["猛烈撞击"] == total


def test_tailei_empty_pool_is_noop():
    battle = make_battle(["踏雷", "猛烈撞击"])
    use(battle, "A", 0, is_first=False)
    assert granted(battle)["猛烈撞击"] == 0


def test_tailei_real_timeline_replays_triggered_burst_next_turn():
    """第1回合 星火迸发 → 第2回合 踏雷（返场）→ 第3回合 猛烈撞击以迸发身份重放星火效果。"""
    battle = make_battle(["星火", "踏雷", "猛烈撞击"])
    use(battle, "A", 0, is_first=True)                 # 第1回合：星火（敌方 +8 灼烧）
    battle._write_turn_match_counters()
    battle.player_a.active.first_action = False
    battle.turn += 1
    battle._ctx_team_cache.clear()
    TurnPipeline.execute_turn_start(battle)
    burn_before = battle.player_b.active._cached_abnormals.get("灼烧", 0)
    assert burn_before == 8

    use(battle, "A", 1, is_first=False)                # 第2回合：踏雷授予迸发
    atk = next(bs for bs in battle.player_a.active.skills if bs.name == "猛烈撞击")
    assert len(atk._burst_effects) > 0

    battle._write_turn_match_counters()
    battle.player_a.active.first_action = True          # 返场（turn_end）把 first_action 置真
    battle.turn += 1
    battle._ctx_team_cache.clear()
    TurnPipeline.execute_turn_start(battle)

    use(battle, "A", 2, is_first=True)                 # 第3回合：猛烈撞击（迸发）
    assert battle.player_b.active._cached_abnormals.get("灼烧", 0) == burn_before * 2
