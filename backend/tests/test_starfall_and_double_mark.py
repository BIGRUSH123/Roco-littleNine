"""守望星（星陨半耗）与 二律背反（double what:"mark"）——两条小配套修复。

- 守望星：「触发星陨印记时仅消耗一半层数，仍造成满层伤害」是**防守方**（印记持有方）
  的特性，修复前 `trigger_starfall` / `consume_starfall_stacks` 读的是攻击方 →
  半耗从未生效。
- 二律背反：「应对防御：额外使敌方星陨印记层数翻倍」，`double what:"mark"` 分支此前缺失
  → 静默 no-op。
"""

from __future__ import annotations

from backend.engine.replayer import JournalReplayer
from backend.sim.action import Action
from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.sim.traits import dispatch_entry
from backend.vm.effect import MarkEffect, ModifierEffect

factory = SimFactory()


def _battle(trait_a: str = "", trait_b: str = "",
            skills_a=("猛烈撞击",), skills_b=("猛烈撞击",)):
    p1 = factory.build_player("A", [{"name": "草衣虫", "skills": list(skills_a)}])
    p2 = factory.build_player("B", [{"name": "花衣蝶", "skills": list(skills_b)}])
    if trait_a:
        p1.team[0].species.ability = trait_a
        p1.team[0].species.ability_id = 0
    if trait_b:
        p2.team[0].species.ability = trait_b
        p2.team[0].species.ability_id = 0
    battle = Battle(p1, p2, verbose=False)
    battle.species_db = factory.sprite_db
    battle.player_a.active_index = 0
    battle.player_b.active_index = 0
    dispatch_entry(p1.team[0], battle, "A")
    dispatch_entry(p2.team[0], battle, "B")
    return battle


def _add_starfall(battle, team: str, stacks: int) -> None:
    battle.globals.apply_mark(team, "星陨印记", "negative", stacks)


def _starfall_stacks(battle, team: str) -> int:
    return sum(m.stacks for m in battle.globals.mark_effects.get(team, [])
               if isinstance(m, MarkEffect) and m.name == "星陨印记")


def _give_half_ratio(sprite) -> None:
    """模拟「守望星」已生效：写入 starfall_consume_ratio=0.5。"""
    sprite.active_effects.append(ModifierEffect(
        name="starfall_consume_ratio", source="守望星",
        attr="starfall_consume_ratio", value=0.5, mode="set"))


# ═══════════════════════════════════════════════════════════════════
# 守望星 — 半耗（防守方）
# ═══════════════════════════════════════════════════════════════════

def test_starfall_halves_consumption_when_defender_has_ratio():
    battle = _battle()
    _add_starfall(battle, "B", 6)
    _give_half_ratio(battle.player_b.active)   # 守望星在防守方（印记持有方）

    battle.globals.trigger_starfall("B", battle.player_a.active,
                                    battle.player_b.active,
                                    battle.player_a.active.skills[0].base)

    assert _starfall_stacks(battle, "B") == 3


def test_starfall_full_consume_without_ratio():
    battle = _battle()
    _add_starfall(battle, "B", 6)

    battle.globals.trigger_starfall("B", battle.player_a.active,
                                    battle.player_b.active,
                                    battle.player_a.active.skills[0].base)

    assert _starfall_stacks(battle, "B") == 0


def test_starfall_ratio_on_attacker_has_no_effect():
    """修复前读攻击方 → 半耗实际从未生效；现在攻击方持有比例不改变消耗。"""
    battle = _battle()
    _add_starfall(battle, "B", 6)
    _give_half_ratio(battle.player_a.active)   # 比例写在攻击方（错误的一侧）

    battle.globals.trigger_starfall("B", battle.player_a.active,
                                    battle.player_b.active,
                                    battle.player_a.active.skills[0].base)

    assert _starfall_stacks(battle, "B") == 0


def test_starfall_damage_uses_full_stacks_even_when_halved():
    """「仍造成满层伤害」：半耗不改伤害（威力按触发前层数算）。"""
    dmg_half = _trigger_damage(half=True)
    dmg_full = _trigger_damage(half=False)
    assert dmg_half == dmg_full > 0


def _trigger_damage(half: bool) -> int:
    battle = _battle()
    _add_starfall(battle, "B", 6)
    if half:
        _give_half_ratio(battle.player_b.active)
    return battle.globals.trigger_starfall(
        "B", battle.player_a.active, battle.player_b.active,
        battle.player_a.active.skills[0].base)


def test_consume_starfall_stacks_reads_owner_ratio():
    battle = _battle()
    _add_starfall(battle, "A", 5)
    _give_half_ratio(battle.player_a.active)

    consumed = battle.globals.consume_starfall_stacks("A", 5, battle.player_a.active)

    assert consumed == 2
    assert _starfall_stacks(battle, "A") == 3


# ═══════════════════════════════════════════════════════════════════
# 二律背反 — double what:"mark"
# ═══════════════════════════════════════════════════════════════════

def test_erlvbeifan_doubles_opponent_marks_on_counter():
    battle = _battle(skills_a=("二律背反",))
    _add_starfall(battle, "B", 3)

    battle._execute_skill_vm("A", Action("skill", skill_index=0), is_first=True,
                             countered_skill=battle.player_b.active.skills[0])

    # 3（原有）+ 3（技能施加）→ ×2 = 12
    assert _starfall_stacks(battle, "B") == 12


def test_double_mark_targets_own_team():
    from backend.vm.journal import Double

    battle = _battle()
    _add_starfall(battle, "A", 4)
    r = JournalReplayer(battle.player_a.active, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)

    r.replay([Double(target="team_own", what="mark", name="星陨印记")])

    assert _starfall_stacks(battle, "A") == 8


def test_double_mark_without_name_doubles_all_marks():
    from backend.vm.journal import Double

    battle = _battle()
    battle.globals.apply_mark("B", "星陨印记", "negative", 2)
    battle.globals.apply_mark("B", "湿润印记", "positive", 3)
    r = JournalReplayer(battle.player_a.active, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)

    r.replay([Double(target="team_opp", what="mark")])

    stacks = {m.name: m.stacks for m in battle.globals.mark_effects["B"]}
    assert stacks["星陨印记"] == 4
    assert stacks["湿润印记"] == 6


def test_double_mark_is_noop_without_marks():
    from backend.vm.journal import Double

    battle = _battle()
    r = JournalReplayer(battle.player_a.active, battle.player_b.active, battle.globals,
                        battle._vm_engine.registry, team="A", battle=battle)

    assert r.replay([Double(target="team_opp", what="mark", name="星陨印记")]) == []
