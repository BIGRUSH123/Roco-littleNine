"""`flag_set flag:"drive" target:"skill_off_0"` = 本回合额外传动（轮班暗分支）。

口径（data/IR_GUIDE.md §3A flag_set drive）：把**当前使用技能**的传动等级临时提到
`base.transmission + value`，立即跑一次传动 pass，随后还原等级（只影响本回合这一次移动）。
"""

from __future__ import annotations

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry

factory = SimFactory()


def make_battle(skills_a):
    p1 = factory.build_player("A", [{"name": "神谕鲨", "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": ["猛烈撞击"]}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    p1.team[0].energy = 10
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def order(battle):
    return [bs.name for bs in battle.player_a.active.skills]


def use_lunban(battle, branch):
    idx = next(i for i, bs in enumerate(battle.player_a.active.skills) if bs.name == "轮班")
    return battle._execute_skill_vm(
        "A", Action("skill", skill_index=idx, branch=branch), is_first=True)


def test_lunban_ming_branch_does_not_extra_transmit():
    battle = make_battle(["水弹", "火苗", "轮班", "冰锥"])
    battle._apply_transmission(battle.player_a.active, team="A")   # 回合开始的传动 pass
    after_turn_start = order(battle)

    use_lunban(battle, 0)                                          # 明：1 号位威力+65

    assert order(battle) == after_turn_start


def test_lunban_an_branch_transmits_immediately():
    battle = make_battle(["水弹", "火苗", "轮班", "冰锥"])
    battle._apply_transmission(battle.player_a.active, team="A")
    after_turn_start = order(battle)          # ['水弹','火苗','冰锥','轮班']

    events = use_lunban(battle, 1)            # 暗：本回合额外传动1

    assert order(battle) != after_turn_start
    assert any("传动+1" in e for e in events)
    # 轮班 只在这两个位置之间来回：额外 pass 把它又挪了一格
    assert order(battle).index("轮班") != after_turn_start.index("轮班")


def test_extra_drive_level_is_restored_after_use():
    battle = make_battle(["水弹", "火苗", "轮班", "冰锥"])
    battle._apply_transmission(battle.player_a.active, team="A")
    lunban = next(bs for bs in battle.player_a.active.skills if bs.name == "轮班")
    assert lunban._transmission == lunban.base.transmission == 1

    n = len(battle.player_a.active.skills)
    before_idx = order(battle).index("轮班")
    use_lunban(battle, 1)                     # 额外 pass（1 格）+ 还原等级
    after_idx = order(battle).index("轮班")
    assert (after_idx - before_idx) % n == 1  # 只多移动 1 个槽位（不是 2）

    assert lunban._transmission == 1          # 等级已还原
    # 下一回合的传动仍按自带等级走：一次 pass 只移动 1 格
    prev_idx = order(battle).index("轮班")
    battle._apply_transmission(battle.player_a.active, team="A")
    now_idx = order(battle).index("轮班")
    assert (now_idx - prev_idx) % n == 1


def test_drive_on_skill_at_still_accumulates_slots():
    """`target:"skill_at_N"`（翼轴那类）保持旧语义：在自带传动上累加。"""
    battle = make_battle(["水弹", "轮班", "冰锥"])
    battle.trait_loader = None  # 占位：本例只用 op 层契约
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import ModifierInjection

    sprite = battle.player_a.active
    replayer = JournalReplayer(sprite, battle.player_b.active, battle.globals,
                               battle._vm_engine.registry, team="A", battle=battle)
    replayer.replay([ModifierInjection(target="skill_at_2", stat="drive", value=1,
                                       mode="set", scope="battlefield")])
    assert sprite.skills[1]._transmission == (sprite.skills[1].base.transmission or 0) + 1
