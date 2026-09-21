"""情报遮蔽状态（木桶 3024 / 月陨星 3025）——获得与解除时机。

游戏内文本：「该状态下会隐藏精灵的信息，自己行动或被敌方攻击时解除。」
本引擎是完全信息对局，隐藏信息无战斗后果，因此只落地**获得/解除**两个时点：
  - 木桶戏法（特性）：离场后换入者以木桶状态登场（`inherit via_pending`）
  - 观测者效应（技能）：自己脱离 + 换入者以月陨星状态登场
  - 解除：自己行动（`battle._execute_skill_vm` 执行尾）/ 被敌方攻击（`replayer._apply_damage`）
"""

from __future__ import annotations

from backend.engine.replayer import JournalReplayer
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry
from backend.vm.effect import AbnormalEffect
from backend.vm.journal import Damage

factory = SimFactory()


def _battle(trait_a: str = "", skills_a=("猛烈撞击", "甩水"), name_a: str = "草衣虫",
            skills_b=("猛烈撞击", "甩水"), name_b: str = "花衣蝶", team_a: int = 2):
    p1 = factory.build_player("A", [
        {"name": name_a, "skills": list(skills_a)}
        for _ in range(team_a)
    ])
    p2 = factory.build_player("B", [{"name": name_b, "skills": list(skills_b)}])
    if trait_a:
        p1.team[0].species.ability = trait_a
        p1.team[0].species.ability_id = 0
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _stacks(sprite, name: str) -> int:
    return sum(e.stacks for e in sprite.active_effects
               if isinstance(e, AbnormalEffect) and e.name == name)


# ═══════════════════════════════════════════════════════════════════
# 木桶戏法 — 离场后换入者获得木桶状态
# ═══════════════════════════════════════════════════════════════════

def test_barrel_trick_grants_state_on_next_entrant():
    """修复前：数据是 triggers[].pending_effects（loader 只读 effects）→ 整条失效。"""
    battle = _battle("木桶戏法")
    battle._handle_escape("A", battle.player_a.active, [], urgent=True)
    incoming = battle.player_a.active
    assert _stacks(incoming, "木桶状态") == 1


def test_barrel_trick_uses_engine_pending_queue():
    battle = _battle("木桶戏法")
    battle._handle_escape("A", battle.player_a.active, [], urgent=True)
    # 队列被入场流程消费清空，效果已经落到新上场精灵身上
    assert battle.pending_effects["A"] == []
    assert any(isinstance(e, AbnormalEffect) and e.name == "木桶状态"
               for e in battle.player_a.active.active_effects)


def test_barrel_trick_also_applies_on_normal_switch():
    """「离场后」含主动换人（_resolve_switch 路径），不只是脱离。"""
    battle = _battle("木桶戏法")
    old = battle.player_a.active

    battle._resolve_switch("A", Action("switch", switch_index=1))

    assert battle.player_a.active is not old
    assert _stacks(battle.player_a.active, "木桶状态") == 1


def test_barrel_state_released_when_holder_acts():
    battle = _battle("木桶戏法")
    battle._handle_escape("A", battle.player_a.active, [], urgent=True)
    holder = battle.player_a.active
    assert _stacks(holder, "木桶状态") == 1

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert _stacks(holder, "木桶状态") == 0


def test_barrel_state_released_when_attacked():
    battle = _battle("木桶戏法")
    battle._handle_escape("A", battle.player_a.active, [], urgent=True)
    holder = battle.player_a.active

    replayer = JournalReplayer(
        battle.player_b.active, holder, battle.globals,
        battle._vm_engine.registry, team="B", battle=battle,
    )
    events = replayer.replay([Damage(target="sprite_opp", amount=1,
                                     element="普通", type="物攻")])

    assert _stacks(holder, "木桶状态") == 0
    assert any("木桶状态解除" in e for e in events)


def test_barrel_state_not_released_by_self_damage():
    """「被敌方攻击」才解除：自我伤害（m.target == sprite_self）不解除。"""
    battle = _battle("木桶戏法")
    battle._handle_escape("A", battle.player_a.active, [], urgent=True)
    holder = battle.player_a.active

    replayer = JournalReplayer(
        holder, battle.player_b.active, battle.globals,
        battle._vm_engine.registry, team="A", battle=battle,
    )
    replayer.replay([Damage(target="sprite_self", amount=1,
                            element="普通", type="物攻")])

    assert _stacks(holder, "木桶状态") == 1


# ═══════════════════════════════════════════════════════════════════
# 观测者效应 — 自己脱离 + 换入者以月陨星状态登场
# ═══════════════════════════════════════════════════════════════════

def test_observer_effect_grants_moon_state_to_replacement():
    battle = _battle(skills_a=("观测者效应", "甩水"))
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert battle.pending_escape is not None
    assert [getattr(e, "name", "") for e in battle.pending_effects["A"]] == ["月陨星状态"]

    battle.resolve_escape("A", 1)
    incoming = battle.player_a.active
    assert _stacks(incoming, "月陨星状态") == 1


def test_observer_effect_moon_state_released_on_action():
    battle = _battle(skills_a=("观测者效应", "甩水"))
    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)
    battle.resolve_escape("A", 1)
    incoming = battle.player_a.active
    assert _stacks(incoming, "月陨星状态") == 1

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True)

    assert _stacks(incoming, "月陨星状态") == 0


def test_observer_effect_json_is_engine_readable():
    """数据契约：观测者效应落地为 escape + inherit(effects)，不再是空 effects。"""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent.parent / "data" / "skills" / "观测者效应.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    ops = [e.get("op") for e in data["effects"]]
    assert ops == ["escape", "inherit"]

    trait_path = (Path(__file__).resolve().parent.parent.parent
                  / "data" / "traits" / "木桶戏法.json")
    trait = json.loads(trait_path.read_text(encoding="utf-8"))
    assert "effects" in trait and "triggers" not in trait
