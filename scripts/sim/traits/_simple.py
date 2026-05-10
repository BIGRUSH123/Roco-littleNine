"""scripts/sim/traits/_simple.py — Simple 级特性（无条件 / 固定效果）

每个特性 3-8 行，纯数据驱动。
"""

from . import register, TraitHandler
from scripts.sim.sprite import StatusEffect, Sprite
from scripts.sim.battle import Battle
from scripts.sim.battleskill import BattleSkill, SkillUse
from scripts.sim.globals import GlobalEffects


# ═══════════════════════════════════════════════════════════════
# on_entry — 入场触发
# ═══════════════════════════════════════════════════════════════

@register("专注力")
class Focus(TraitHandler):
    """入场首回合，物攻+100%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.add_effect(StatusEffect(
            name="物攻+100%", category="stat", stat_key="atk",
            steps=10, scope="battlefield", source="专注力",
        ))
        return [f"{sprite.name} 专注力: 物攻+100%"]


@register("渴求")
class Thirst(TraitHandler):
    """入场时获得50%吸血。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.add_effect(StatusEffect(
            name="吸血50%", category="stat", stat_key="life_drain",
            steps=5, scope="battlefield", source="渴求",
        ))
        return [f"{sprite.name} 渴求: 吸血+50%"]


@register("小偷小摸")
class PettyTheft(TraitHandler):
    """入场时偷取所有敌方精灵2能量。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        events: list[str] = []
        opponent = battle.get_opponent(team)
        for s in opponent.team:
            if not s.is_fainted:
                lost = s.lose_energy(2)
                if lost:
                    events.append(f"{sprite.name} 小偷小摸: 偷取{s.name} {lost}E")
        return events


# ═══════════════════════════════════════════════════════════════
# on_leave — 离场触发
# ═══════════════════════════════════════════════════════════════

@register("快充")
class QuickCharge(TraitHandler):
    """离场时回复10能量。"""

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        if is_faint:
            return []
        gained = sprite.gain_energy(10)
        return [f"{sprite.name} 快充: +{gained}E"] if gained else []


# ═══════════════════════════════════════════════════════════════
# on_turn_end — 回合末触发
# ═══════════════════════════════════════════════════════════════

@register("养分内循环")
class NutrientCycle(TraitHandler):
    """回合结束时，回复6能量。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        gained = sprite.gain_energy(6)
        return [f"{sprite.name} 养分内循环: +{gained}E"] if gained else []


@register("养分重吸收")
class NutrientReabsorb(TraitHandler):
    """回合结束时，回复3能量。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        gained = sprite.gain_energy(3)
        return [f"{sprite.name} 养分重吸收: +{gained}E"] if gained else []


@register("生长")
class Growth(TraitHandler):
    """回合结束时，回复12%生命。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        amount = round(sprite.max_hp * 0.12)
        healed = sprite.heal(amount)
        return [f"{sprite.name} 生长: +{healed}HP"] if healed else []


@register("毒蘑菇")
class PoisonMushroom(TraitHandler):
    """回合结束时，偷取敌方场上精灵1能量。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp = battle.get_opponent(team).active
        if opp.is_fainted:
            return []
        lost = opp.lose_energy(1)
        return [f"{sprite.name} 毒蘑菇: 偷取{opp.name} {lost}E"] if lost else []


@register("大捞一笔")
class BigScore(TraitHandler):
    """回合结束时，偷取所有敌方精灵2能量。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        events: list[str] = []
        opponent = battle.get_opponent(team)
        for s in opponent.team:
            if not s.is_fainted:
                lost = s.lose_energy(2)
                if lost:
                    events.append(f"{sprite.name} 大捞一笔: 偷取{s.name} {lost}E")
        return events


@register("吸积盘")
class AccretionDisk(TraitHandler):
    """回合结束时，敌方获得2层星陨印记。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp_team = 'B' if team == 'A' else 'A'
        battle.globals.apply_mark(opp_team, '星陨印记', 'negative', 2)
        return [f"{sprite.name} 吸积盘: 敌方获得2层星陨印记"]


@register("花精灵")
class FlowerFairy(TraitHandler):
    """回合结束时，己方队伍获得1次随机奉献。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        player.lives += 1
        return [f"{sprite.name} 花精灵: 奉献+1"]


# ═══════════════════════════════════════════════════════════════
# on_modifier — 修改技能参数
# ═══════════════════════════════════════════════════════════════

@register("不移")
class Immovable(TraitHandler):
    """携带的无额外效果攻击技能威力+30%。"""

    _EXTRA_KINDS = {'stat', 'abnormal', 'mark', 'weather'}

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        has_extra = any(
            getattr(e, 'kind', '') in self._EXTRA_KINDS
            for e in use.battle_skill.effects
        )
        if not has_extra:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.3
        return []
