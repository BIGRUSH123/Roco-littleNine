"""backend/sim/traits/_complex.py — Complex 级特性（需要 Python 逻辑的特性）

Phase C4: 9 个 pass-through 特性已移至 JSON + engine/hooks/。
保留 11 个需要复杂战斗逻辑的特性类。
"""

from backend.sim.battle import Battle
from backend.sim.battleskill import BattleSkill, SkillUse
from backend.sim.sprite import Sprite, StatusEffect

from . import TraitHandler, register


# ═══════════════════════════════════════════════════════════════════
# 技能槽位限制
# ═══════════════════════════════════════════════════════════════════

@register("宝剑王牌")
class SwordAce(TraitHandler):
    """仅可使用1号和3号位技能（封印2号和4号位）。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        for i, bs in enumerate(sprite.skills):
            if i not in (0, 2):  # slot 1 and 3 (0-indexed)
                bs.sealed = True
        return [f'{sprite.name} 宝剑王牌: 仅1号/3号位可用']


@register("正位宝剑")
class UprightSword(TraitHandler):
    """仅可使用1号位技能（封印2/3/4号位）。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        for i, bs in enumerate(sprite.skills):
            if i != 0:  # slot 1 only
                bs.sealed = True
        return [f'{sprite.name} 正位宝剑: 仅1号位可用']


# ═══════════════════════════════════════════════════════════════════
# Conditional / modifier traits
# ═══════════════════════════════════════════════════════════════════

@register("倾轧")
class Crush(TraitHandler):
    """技能受能耗变化效果影响翻倍。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        total = sum(e.steps for e in user.effects if e.category == 'stat'
                    and e.stat_key == 'energy_cost')
        if total == 0:
            return []
        if total > 0:
            user.lose_energy(total)
            return [f'{user.name} 倾轧: 能耗翻倍, 额外消耗{total}E']
        else:
            user.gain_energy(-total)
            return [f'{user.name} 倾轧: 能耗翻倍, 返还{-total}E']


@register("张弛有度")
class WorkLifeBalance(TraitHandler):
    """周末双攻+40%，其他时间双防+40%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        import datetime
        weekday = datetime.date.today().weekday()
        is_weekend = weekday >= 5  # 5=Sat, 6=Sun
        if is_weekend:
            for key in ('atk', 'sp_atk'):
                sprite.add_effect(StatusEffect(
                    name='周末双攻+', category='stat', stat_key=key,
                    steps=4, scope='battlefield', source='张弛有度'))
            return [f'{sprite.name} 张弛有度: 周末双攻+40%']
        else:
            for key in ('def', 'sp_def'):
                sprite.add_effect(StatusEffect(
                    name='双防+', category='stat', stat_key=key,
                    steps=4, scope='battlefield', source='张弛有度'))
            return [f'{sprite.name} 张弛有度: 平日双防+40%']


# ═══════════════════════════════════════════════════════════════════
# 裂口组 / 复杂状态转换
# ═══════════════════════════════════════════════════════════════════

@register("腾挪")
class EvasiveManeuver(TraitHandler):
    """攻击技能应对1次后，回满状态，变为棋绮后。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        if not countered_skill.base.is_attack:
            return []
        return _try_transform(user, battle, team, '腾挪')


@register("保卫")
class DefendTransform(TraitHandler):
    """防御技能应对2次后，回满状态，变为棋绮后。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        if not countered_skill.base.is_defense:
            return []
        count = user.inc_counter('defend_countered')
        if count >= 2:
            user.counters['defend_countered'] = 0
            return _try_transform(user, battle, team, '保卫')
        return [f'{user.name} 保卫: 防御应对 {count}/2']


@register("好象坏象")
class LikeBadElephant(TraitHandler):
    """状态技能应对1次后，回满状态，变为棋绮后。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        if not countered_skill.base.is_status:
            return []
        return _try_transform(user, battle, team, '好象坏象')


def _try_transform(user: Sprite, battle: Battle, team: str, trait_name: str) -> list[str]:
    """尝试将 user 变换为棋绮后。"""
    from backend.common.models import SpeciesStats

    new_species = battle.lookup_species('棋绮后')
    if new_species is None:
        # species_db 未注入，用占位 species（继承原 stats）
        s = user.species
        new_species = SpeciesStats(
            name='棋绮后', form='',
            hp=s.hp, atk=s.atk, sp_atk=s.sp_atk,
            def_=s.def_, sp_def=s.sp_def, speed=s.speed,
            attributes=s.attributes, ability=s.ability,
        )
    new_skills = battle.build_skills([])  # TODO: 加载棋绮后技能
    user.heal(user.max_hp)
    user.energy = 10
    user.clear_effects('battlefield')
    events = user.transform(new_species, new_skills)
    events.insert(0, f'{user.name} {trait_name}: 回满状态→棋绮后')
    return events


# ═══════════════════════════════════════════════════════════════════
# Bloodline / resource / damage traits
# ═══════════════════════════════════════════════════════════════════

@register("稀兽花宝")
class RareBeastFlower(TraitHandler):
    """根据自己的血脉，入场时获得不同效果。"""

    # 血脉 → 效果映射（待补全）
    _BLOODLINE_EFFECTS: dict[str, list] = {
        # '火': [StatusEffect(name='...', ...)],
    }

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        effects = self._BLOODLINE_EFFECTS.get(sprite.bloodline, [])
        if not effects:
            return []
        for e in effects:
            sprite.add_effect(e)
        return [f'{sprite.name} 稀兽花宝({sprite.bloodline}): 获得血脉效果']




@register("系统发育")
class Phylogeny(TraitHandler):
    """获得能量或生命时，等量随机分配给场下精灵。"""

    def on_energy_change(self, sprite: Sprite, delta: int, new_energy: int,
                         battle: Battle, team: str) -> list[str]:
        if delta <= 0:
            return []
        player = battle.get_player(team)
        bench = [s for i, s in enumerate(player.team)
                 if i != player.active_index and not s.is_fainted]
        if not bench:
            return []
        import random
        target = random.choice(bench)
        gained = target.gain_energy(delta)
        return [f'{sprite.name} 系统发育: {target.name} +{gained}E']


@register("刺肤")
class ThornSkin(TraitHandler):
    """每受1次攻击，对攻击者造成50威力物理伤害。"""

    def on_take_damage(self, target: Sprite, attacker: Sprite, damage: int,
                       battle: Battle, team: str) -> list[str]:
        if attacker is None or attacker.is_fainted or damage <= 0:
            return []
        raw = round(target.effective_stat('atk') * 50 / max(1, attacker.effective_stat('def')))
        dealt = attacker.take_damage(raw)
        return [f'{target.name} 刺肤: 反伤{attacker.name}-{dealt}HP']


# ═══════════════════════════════════════════════════════════════════
# on_take_damage — 受伤后触发（条件判断）
# ═══════════════════════════════════════════════════════════════════

@register("嫁祸")
class Scapegoat(TraitHandler):
    """每失去25%生命，连击数+2。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.counters['scapegoat_quarters'] = 0
        return []

    def on_take_damage(self, target: Sprite, attacker: Sprite, damage: int,
                       battle: Battle, team: str) -> list[str]:
        hp_pct = target.current_hp / target.max_hp
        lost_quarters = int((1.0 - hp_pct) / 0.25)
        prev = target.counters.get('scapegoat_quarters', 0)
        if lost_quarters > prev:
            gained = lost_quarters - prev
            target.counters['scapegoat_quarters'] = lost_quarters
            target.add_effect(StatusEffect(
                name='嫁祸连击', category='stat', stat_key='combo',
                steps=gained * 2, scope='battlefield', source='嫁祸'))
            return [f'{target.name} 嫁祸: 连击+{gained*2}']
        return []


# ═══════════════════════════════════════════════════════════════════
# on_fatal_damage — 致命伤害前触发
# ═══════════════════════════════════════════════════════════════════



