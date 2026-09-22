"""`exchange` 的显式对家选择（瞳中倒影「其他精灵离场时交换血量百分比」）。

口径（data/IR_GUIDE.md §3C exchange）：交换双方是 `replayer.self`（语境主体）与
`target` 指向的对家 —— `"sprite_opp"`（默认）/ `"leaving"`（刚离场者）/
`"entering"`（本次换入者，仅 post_enemy_leave 提供）。

`post_enemy_leave` 语境下 `self` = 己方场上精灵、`opp` = 敌方换入者，
所以「自己与更换入场的精灵交换」= 默认那一对；`"entering"` 是同义的显式写法。
"""

from __future__ import annotations

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry

factory = SimFactory()


def make_battle(trait_a=None, ability_id=0, team_b_species=("花衣蝶", "绿翼鸟")):
    p1 = factory.build_player("A", [{"name": "神谕鲨", "skills": ["猛烈撞击"]}])
    p2 = factory.build_player("B", [{"name": n, "skills": ["猛烈撞击"]} for n in team_b_species])
    if trait_a:
        p1.team[0].species.ability = trait_a
        p1.team[0].species.ability_id = ability_id
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def ratio(sprite):
    return sprite.current_hp / sprite.max_hp


def set_ratio(sprite, r):
    sprite.current_hp = max(1, round(sprite.max_hp * r))


def close(a, b):
    return abs(a - b) < 0.01


def test_tongzhongdaoying_swaps_hp_when_other_sprite_leaves():
    battle = make_battle(trait_a="瞳中倒影", ability_id=20198)
    me = battle.player_a.active
    entering = battle.player_b.team[1]
    set_ratio(me, 0.5)
    set_ratio(battle.player_b.active, 1.0)
    set_ratio(entering, 0.3)

    battle._resolve_switch("B", Action("switch", switch_index=1))

    assert close(ratio(me), 0.3)
    assert close(ratio(entering), 0.5)


def test_without_trait_no_swap():
    battle = make_battle()
    me = battle.player_a.active
    entering = battle.player_b.team[1]
    set_ratio(me, 0.5)
    set_ratio(battle.player_b.active, 1.0)
    set_ratio(entering, 0.3)
    me_hp, entering_hp = me.current_hp, entering.current_hp

    battle._resolve_switch("B", Action("switch", switch_index=1))

    assert me.current_hp == me_hp
    assert entering.current_hp == entering_hp


def test_exchange_default_target_is_opp():
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import Exchange

    battle = make_battle()
    me, opp = battle.player_a.active, battle.player_b.active
    set_ratio(me, 0.25)
    set_ratio(opp, 1.0)

    replayer = JournalReplayer(me, opp, battle.globals, battle._vm_engine.registry,
                               team="A", battle=battle)
    replayer.replay([Exchange(target="sprite_opp", what="hp_ratio")])

    assert close(ratio(me), 1.0)
    assert close(ratio(opp), 0.25)


def test_exchange_leaving_and_entering_resolve_in_post_enemy_leave():
    """post_enemy_leave 语境：self=己方场上、opp=敌方换入者、_leaving=敌方离场者。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import Exchange

    for target in ("entering", "leaving"):
        battle = make_battle()
        me = battle.player_a.active
        leaving = battle.player_b.active
        entering = battle.player_b.team[1]
        set_ratio(me, 0.4)
        set_ratio(entering, 0.2)
        set_ratio(leaving, 0.8)

        replayer = JournalReplayer(me, entering, battle.globals, battle._vm_engine.registry,
                                   team="A", battle=battle, leaving_sprite=leaving)
        replayer.replay([Exchange(target=target, what="hp_ratio")])

        partner = entering if target == "entering" else leaving
        assert close(ratio(me), 0.2 if target == "entering" else 0.8)
        assert close(ratio(partner), 0.4)


def test_exchange_leaving_is_noop_outside_post_enemy_leave():
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import Exchange

    battle = make_battle()
    me, opp = battle.player_a.active, battle.player_b.active
    set_ratio(me, 0.4)
    set_ratio(opp, 1.0)
    me_hp, opp_hp = me.current_hp, opp.current_hp

    replayer = JournalReplayer(me, opp, battle.globals, battle._vm_engine.registry,
                               team="A", battle=battle)      # 无 leaving_sprite
    for target in ("leaving", "entering"):
        replayer.replay([Exchange(target=target, what="hp_ratio")])
        assert me.current_hp == me_hp
        assert opp.current_hp == opp_hp
