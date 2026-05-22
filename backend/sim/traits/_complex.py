"""backend/sim/traits/_complex.py — Complex 级特性（需要 Python 逻辑的特性）

Phase C4: 16 个 pass-through 特性已移至 JSON + engine/hooks/。
保留 6 个需要复杂战斗逻辑的特性类。
"""

from backend.sim.battle import Battle
from backend.sim.battleskill import BattleSkill
from backend.sim.sprite import Sprite, StatusEffect

from . import TraitHandler, register


# ═══════════════════════════════════════════════════════════════════
# Conditional / modifier traits
# ═══════════════════════════════════════════════════════════════════

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



