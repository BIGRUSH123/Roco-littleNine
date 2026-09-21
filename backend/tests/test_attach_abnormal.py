"""附加中毒（3013）——重金属粉尘「携带的攻击技能获得附加中毒3，应对防御：改为6」。

机制：`power_mod attr:"attach_abnormal"` 把「命中后追加 N 层中毒」挂到技能上
（技能级 `_modifiers`，与 life_drain 同口径：精灵级与技能级取 max）；
消费点在 `replayer._apply_damage()`，**每次行动只追加一次**。
"""

from __future__ import annotations

from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry
from backend.vm.effect import AbnormalEffect

factory = SimFactory()


def _battle(skills_a=("重金属粉尘", "猛烈撞击"), skills_b=("猛烈撞击",),
            name_a="草衣虫", name_b="花衣蝶"):
    p1 = factory.build_player("A", [{"name": name_a, "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": name_b, "skills": list(skills_b)}])
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _poison(sprite) -> int:
    return sum(e.stacks for e in sprite.active_effects
               if isinstance(e, AbnormalEffect) and e.name == "中毒")


def test_heavy_metal_dust_marks_attack_skills_only():
    battle = _battle(skills_a=("重金属粉尘", "猛烈撞击", "防御"))
    user = battle.player_a.active

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    mods = {bs.name: bs._modifiers.get("attach_abnormal") for bs in user.skills}
    assert mods["猛烈撞击"] == 3
    assert mods["重金属粉尘"] is None      # 自身是状态技能，不挂
    assert mods["防御"] is None           # 防御技能不挂


def test_attack_applies_poison_stacks_on_hit():
    battle = _battle()
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert _poison(battle.player_b.active) == 0

    battle._execute_skill_vm("A", Action("skill", skill_index=1), is_first=True)

    assert _poison(battle.player_b.active) == 3


def test_counter_branch_raises_marker_to_6():
    battle = _battle(skills_b=("防御",))
    user = battle.player_a.active

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True,
                             countered_skill=battle.player_b.active.skills[0])

    assert user.skills[1]._modifiers.get("attach_abnormal") == 6

    battle._execute_skill_vm("A", Action("skill", skill_index=1), is_first=True)
    assert _poison(battle.player_b.active) == 6


def test_attach_abnormal_applied_once_per_action():
    """连击技能：命中多段也只追加一次（「使用附加中毒X的技能会使敌方获得X层中毒」）。"""
    battle = _battle(skills_a=("重金属粉尘",), skills_b=("甩水",))
    user = battle.player_a.active
    user.skills[0]._modifiers["attach_abnormal"] = 2.0
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import Damage

    r = JournalReplayer(user, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A",
                        self_skill=user.skills[0], battle=battle)
    r.replay([
        Damage(target="sprite_opp", amount=1, element="毒", type="物攻"),
        Damage(target="sprite_opp", amount=1, element="毒", type="物攻"),
    ])

    assert _poison(battle.player_b.active) == 2


def test_attach_abnormal_sprite_level_modifier_also_reads():
    """精灵级 _modifiers（不带 skill_filter 的写法）同样命中。"""
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import Damage

    battle = _battle()
    user = battle.player_a.active
    user._modifiers["attach_abnormal"] = 4.0

    r = JournalReplayer(user, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([Damage(target="sprite_opp", amount=1, element="毒", type="物攻")])

    assert _poison(battle.player_b.active) == 4


def test_self_damage_does_not_apply_poison():
    from backend.engine.replayer import JournalReplayer
    from backend.vm.journal import Damage

    battle = _battle()
    user = battle.player_a.active
    user._modifiers["attach_abnormal"] = 3.0

    r = JournalReplayer(user, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)
    r.replay([Damage(target="sprite_self", amount=1, element="毒", type="物攻")])

    assert _poison(user) == 0


def test_attach_abnormal_marker_survives_turn_start_cleanup():
    """携带型属性不在 _PER_TURN_KEYS 里：跨回合保留且不叠加。"""
    from backend.sim.battle import _PER_TURN_KEYS

    assert "attach_abnormal" not in _PER_TURN_KEYS

    battle = _battle(skills_a=("重金属粉尘", "猛烈撞击"))
    user = battle.player_a.active
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    assert user.skills[1]._modifiers.get("attach_abnormal") == 3

    # 回合切换的清理 + 特性直连重放都不会把它翻倍
    for key in _PER_TURN_KEYS:
        user.skills[1]._modifiers.pop(key, None)
    battle._vm_engine.trait_loader.reapply_all_direct_mods(
        [battle.player_a.active, battle.player_b.active])

    assert user.skills[1]._modifiers.get("attach_abnormal") == 3
