"""scripts/sim/traits/_complex.py — Complex 级特性（跨精灵/多步/战场光环）

需要 battle 级状态追踪（pre-entry accumulator / pending effects / aura）。
"""

from . import register, TraitHandler
from scripts.sim.sprite import StatusEffect, Sprite
from scripts.sim.battle import Battle
from scripts.sim.battleskill import BattleSkill, SkillUse
from scripts.common.skill_trait_ids import (
    TRAIT_吟游之弦, TRAIT_多人宿舍, TRAIT_守望星, TRAIT_星地善良,
)


# ═══════════════════════════════════════════════════════════════
# on_leave → next entry buff（离场后，下个入场精灵获得增益）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Enemy-leave reaction（敌方离场时触发）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Pre-entry accumulators（入场前累积计数 → 入场时一次性消费）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Aura traits（在场时持续生效的战场光环）
# ═══════════════════════════════════════════════════════════════

@register("吟游之弦")
class BardicStrings(TraitHandler):
    """赋予的印记不替换其他印记，同时生效。"""
    # 实现在 GlobalEffects.apply_mark: 若 user 有此特性 → 同名叠加/异名共存


@register("多人宿舍")
class SharedDorm(TraitHandler):
    """能量可以超过能量上限（10→15）。"""
    # 实现在 Sprite.max_energy 属性中


@register("无忧无虑")
class Carefree(TraitHandler):
    """萌化层数不受限制。"""
    # 当前系统无萌化层数上限，默认已生效


# ═══════════════════════════════════════════════════════════════
# Conditional damage resistance（条件减伤）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# KO / Faint 特殊效果
# ═══════════════════════════════════════════════════════════════

@register("不朽")
class Immortal(TraitHandler):
    """力竭3回合后复活。"""

    def on_faint(self, sprite: Sprite, killer: Sprite | None,
                 battle: Battle, team: str) -> list[str]:
        sprite._faint_turn = battle.turn
        return [f'{sprite.name} 不朽: 第{battle.turn}回合力竭, 3回合后复活']


# ═══════════════════════════════════════════════════════════════
# 技能槽位限制
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# Other conditional/passive traits
# ═══════════════════════════════════════════════════════════════

@register("星地善良")
class StargroundKind(TraitHandler):
    """回合结束时若场上己方精灵能量=0，自己立即替换之。"""
    # 实现在 Battle._phase_turn_end 中的 bench 扫描逻辑
    pass


@register("对流")
class Convection(TraitHandler):
    """自己的能耗增加变为降低；降低变为增加。（由 engine 在能量计算和 energy_cost_increment 处统一反转）"""


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


# ═══════════════════════════════════════════════════════════════
# 裂口组 / 复杂状态转换
# ═══════════════════════════════════════════════════════════════

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
    from scripts.sim.battleskill import BattleSkill
    from scripts.common.models import SpeciesStats

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


@register("契约的形状")
class ContractShape(TraitHandler):
    """根据捕捉所用的咕噜球，入场时获得不同效果。"""
    pass


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


@register("守望星")
class Starguard(TraitHandler):
    """触发星陨时消耗一半层数，仍造成满层伤害。"""
    # 实现在 GlobalEffects.consume_starfall_stacks: 若 user 有此特性 → 消耗减半


@register("石头大餐")
class StoneFeast(TraitHandler):
    """能量不足时消耗5%生命代替1能量。"""

    def on_energy_short(self, sprite: Sprite, cost: int,
                         battle: Battle, team: str) -> int:
        return round(sprite.max_hp * 0.05 * cost)


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


@register("嫉妒")
class Jealousy(TraitHandler):
    """蓄力状态下可使用任一携带技能。"""
    # agent 已处理：蓄力中 + 嫉妒 trait → 跳过 charged_skill_index 强制选择


@register("机械变式")
class MechanicalVariation(TraitHandler):
    """技能每回合位置变化时，该技能能耗-1。"""
    # 实现在 Battle._phase_turn_start 传动后检查中


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


# ═══════════════════════════════════════════════════════════════
# Layer 3b: Hook 注册回调（替代硬编码 trait 名检查）
# ═══════════════════════════════════════════════════════════════

from scripts.sim.traits.trait_engine import register_hook


def _bard_before_apply_mark(team, name, category, stacks, user):
    """吟游之弦: 印记共存模式。"""
    if user is None:
        return None
    from scripts.sim.traits import get_trait
    h = get_trait(user)
    return 'coexist' if (h and h.trait_id == TRAIT_吟游之弦) else None

register_hook('before_apply_mark', _bard_before_apply_mark, '吟游之弦')


def _dorm_max_energy(sprite):
    """多人宿舍: 能量上限 10→15。"""
    from scripts.sim.traits import get_trait
    h = get_trait(sprite)
    return 15 if (h and h.trait_id == TRAIT_多人宿舍) else None

register_hook('max_energy_override', _dorm_max_energy, '多人宿舍')


def _starguard_consume_starfall(team, amount, sprite, starfall_mark):
    """守望星: 星陨消耗减半。"""
    if sprite is None:
        return None
    from scripts.sim.traits import get_trait
    h = get_trait(sprite)
    return max(1, amount // 2) if (h and h.trait_id == TRAIT_守望星) else None

register_hook('before_consume_starfall', _starguard_consume_starfall, '守望星')


def _starground_bench_check(battle, team, active, player):
    """星地善良: 己方能量=0时主动替换上场。"""
    if active.is_fainted or active.energy > 0:
        return None
    for i, bench in enumerate(player.team):
        if i == player.active_index or bench.is_fainted:
            continue
        from scripts.sim.traits import get_trait
        h = get_trait(bench)
        if h and h.trait_id == TRAIT_星地善良:
            return (i, '星地善良')
    return None

register_hook('turn_end_bench_check', _starground_bench_check, '星地善良')


def _mechvar_after_transmission(sprite, prev, battle, team):
    """机械变式: 传动后技能位置变化 → 能耗-1。"""
    events = []
    for i, bs in enumerate(sprite.skills):
        if i < len(prev) and bs.name != prev[i]:
            bs.base.energy_cost = max(0, bs.base.energy_cost - 1)
            events.append(f'{sprite.name} 机械变式: {bs.name} 能耗-1(传动位移)')
    return events if events else None

register_hook('after_transmission', _mechvar_after_transmission, '机械变式')

