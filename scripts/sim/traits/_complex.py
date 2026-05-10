"""scripts/sim/traits/_complex.py — Complex 级特性（跨精灵/多步/战场光环）

需要 battle 级状态追踪（pre-entry accumulator / pending effects / aura）。
"""

from . import register, TraitHandler
from scripts.sim.sprite import StatusEffect, Sprite
from scripts.sim.battle import Battle
from scripts.sim.battleskill import BattleSkill, SkillUse


# ═══════════════════════════════════════════════════════════════
# on_leave → next entry buff（离场后，下个入场精灵获得增益）
# ═══════════════════════════════════════════════════════════════

@register("美拉德反应")
class MaillardReaction(TraitHandler):
    """离场后，更换入场的精灵获得双攻+20%且免疫灼烧。"""

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        battle.pending_effects.setdefault(team, [])
        battle.pending_effects[team].append(StatusEffect(
            name='双攻+20%', category='stat', stat_key='atk',
            steps=2, scope='battlefield', source='美拉德反应'))
        battle.pending_effects[team].append(StatusEffect(
            name='双攻+20%', category='stat', stat_key='sp_atk',
            steps=2, scope='battlefield', source='美拉德反应'))
        battle.pending_effects[team].append(StatusEffect(
            name='免疫灼烧', category='state', source='美拉德反应'))
        return [f'{sprite.name} 美拉德反应: 下次入场继承双攻+20%免疫灼烧']


@register("吉利丁片")
class GelatinSheet(TraitHandler):
    """离场后，更换入场的精灵获得双防+20%且免疫冻结。"""

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        battle.pending_effects.setdefault(team, [])
        battle.pending_effects[team].append(StatusEffect(
            name='双防+20%', category='stat', stat_key='def',
            steps=2, scope='battlefield', source='吉利丁片'))
        battle.pending_effects[team].append(StatusEffect(
            name='双防+20%', category='stat', stat_key='sp_def',
            steps=2, scope='battlefield', source='吉利丁片'))
        battle.pending_effects[team].append(StatusEffect(
            name='免疫冻结', category='state', source='吉利丁片'))
        return [f'{sprite.name} 吉利丁片: 下次入场继承双防+20%免疫冻结']


@register("茶多酚")
class TeaPolyphenol(TraitHandler):
    """离场后，更换入场的精灵回复20%生命且免疫寄生。"""

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        battle.pending_effects.setdefault(team, [])
        battle.pending_effects[team].append(StatusEffect(
            name='回复20%HP', category='state', source='茶多酚'))
        battle.pending_effects[team].append(StatusEffect(
            name='免疫寄生', category='state', source='茶多酚'))
        return [f'{sprite.name} 茶多酚: 下次入场回复20%HP免疫寄生']


@register("洁癖")
class NeatFreak(TraitHandler):
    """离场后，自己的增益/减益被换上来的精灵继承。"""

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        inherited = [e for e in sprite.effects if e.scope == 'battlefield']
        if inherited:
            battle.pending_effects.setdefault(team, [])
            battle.pending_effects[team].extend(inherited)
            return [f'{sprite.name} 洁癖: 继承{len(inherited)}个效果']
        return []


@register("木桶戏法")
class BarrelTrick(TraitHandler):
    """离场后，换上来的精灵以木桶状态登场。"""

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        battle.pending_effects.setdefault(team, [])
        battle.pending_effects[team].append(StatusEffect(
            name='木桶状态', category='state', scope='battlefield', source='木桶戏法'))
        return [f'{sprite.name} 木桶戏法: 下次入场木桶状态']


# ═══════════════════════════════════════════════════════════════
# Enemy-leave reaction（敌方离场时触发）
# ═══════════════════════════════════════════════════════════════

@register("做噩梦")
class Nightmare(TraitHandler):
    """敌方精灵离场后，更换入场的精灵失去3能量。"""

    def on_enemy_leave(self, sprite: Sprite, enemy_old: Sprite,
                        enemy_new: Sprite, battle: Battle, team: str) -> list[str]:
        lost = enemy_new.lose_energy(3)
        return [f'{sprite.name} 做噩梦: {enemy_new.name} 失去{lost}能量']


@register("下黑手")
class CheapShot(TraitHandler):
    """敌方精灵离场后，更换入场的精灵获得5层中毒。"""

    def on_enemy_leave(self, sprite: Sprite, enemy_old: Sprite,
                        enemy_new: Sprite, battle: Battle, team: str) -> list[str]:
        enemy_new.add_effect(StatusEffect(
            name='中毒', category='abnormal', stacks=5, source='下黑手'))
        return [f'{sprite.name} 下黑手: {enemy_new.name} 中毒×5']


@register("珊瑚骨")
class CoralBone(TraitHandler):
    """敌方精灵离场时，自己获得全技能能耗-3。"""

    def on_enemy_leave(self, sprite: Sprite, enemy_old: Sprite,
                        enemy_new: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.add_effect(StatusEffect(
            name='能耗-3', category='stat', stat_key='energy_cost',
            steps=-3, scope='permanent', source='珊瑚骨'))
        return [f'{sprite.name} 珊瑚骨: 全技能能耗-3']


# ═══════════════════════════════════════════════════════════════
# Pre-entry accumulators（入场前累积计数 → 入场时一次性消费）
# ═══════════════════════════════════════════════════════════════

@register("水翼推进")
class HydroWingPush(TraitHandler):
    """己方精灵每使用1次水系技能，自己入场时全技能能耗-1。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        count = battle.get_team_counter(team, 'element:水')
        if count <= 0:
            return []
        sprite.add_effect(StatusEffect(
            name='能耗-', category='stat', stat_key='energy_cost',
            steps=-count, scope='permanent', source='水翼推进'))
        return [f'{sprite.name} 水翼推进: 全技能能耗-{count}']


@register("水翼飞升")
class HydroWingAscend(TraitHandler):
    """己方精灵每使用1次水系技能，入场时全技能能耗-1；能耗为0的技能威力+30%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        events: list[str] = []
        count = battle.get_team_counter(team, 'element:水')
        if count > 0:
            sprite.add_effect(StatusEffect(
                name='能耗-', category='stat', stat_key='energy_cost',
                steps=-count, scope='permanent', source='水翼飞升'))
            events.append(f'{sprite.name} 水翼飞升: 全技能能耗-{count}')
        # 能耗为0的技能威力+30% — applied in _apply_zero_cost_bonus
        return events


@register("地脉")
class Leyline(TraitHandler):
    """初始能量为0，入场前己方每放1次地系技能，回复3能量。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.energy = 0
        count = battle.get_team_counter(team, 'element:地')
        gained = sprite.gain_energy(count * 3)
        return [f'{sprite.name} 地脉: 初始0E, +{gained}E({count}次地系)']


@register("地脉馈赠")
class LeylineGift(TraitHandler):
    """突破能量上限，初始0E，己方每放1次地系技能回复3E，入场立即回复10E。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.energy = 0
        count = battle.get_team_counter(team, 'element:地')
        base = count * 3
        gained = sprite.gain_energy(base + 10)
        return [f'{sprite.name} 地脉馈赠: +{gained}E({count}次地系+10)']


@register("散热")
class HeatDissipation(TraitHandler):
    """初始能量为0，入场前己方每放1次火系技能，回复3能量。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.energy = 0
        count = battle.get_team_counter(team, 'element:火')
        gained = sprite.gain_energy(count * 3)
        return [f'{sprite.name} 散热: 初始0E, +{gained}E({count}次火系)']


@register("打雪仗")
class SnowballFight(TraitHandler):
    """初始能量为0，入场前己方每放1次冰系技能，回复3能量。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.energy = 0
        count = battle.get_team_counter(team, 'element:冰')
        gained = sprite.gain_energy(count * 3)
        return [f'{sprite.name} 打雪仗: 初始0E, +{gained}E({count}次冰系)']


@register("慢热型")
class SlowStarter(TraitHandler):
    """初始能量为0，入场前己方精灵每成功应对1次，回复5能量。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.energy = 0
        count = battle.get_team_counter(team, 'counter_success')
        gained = sprite.gain_energy(count * 5)
        return [f'{sprite.name} 慢热型: 初始0E, +{gained}E({count}次应对)']


@register("拨浪鼓")
class RattleDrum(TraitHandler):
    """己方精灵每使用1次状态技能，自己入场时毒系和萌系技能威力+10。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        count = battle.get_team_counter(team, 'status_skill')
        if count <= 0:
            return []
        for bs in sprite.skills:
            if bs.element in ('毒', '萌'):
                bs.power_mod += count * 10
        return [f'{sprite.name} 拨浪鼓: 毒/萌技能威力+{count*10}']


@register("身经百练")
class Seasoned(TraitHandler):
    """己方精灵每应对1次，自己入场时水系和武系技能威力+20%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        count = battle.get_team_counter(team, 'counter_success')
        if count <= 0:
            return []
        for bs in sprite.skills:
            if bs.element in ('水', '武'):
                bs.power_mod += int(bs.base.power * count * 0.2)
        return [f'{sprite.name} 身经百练: 水/武技能威力+{count*20}%']


@register("蒸汽膨胀")
class SteamExpansion(TraitHandler):
    """己方精灵每使用1次火系技能，入场时全技能威力+10。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        count = battle.get_team_counter(team, 'element:火')
        if count <= 0:
            return []
        for bs in sprite.skills:
            bs.power_mod += count * 10
        return [f'{sprite.name} 蒸汽膨胀: 全技能威力+{count*10}']


@register("定向精炼")
class DirectedRefining(TraitHandler):
    """己方精灵每使用1次防御技能，入场时机械/地系技能威力+10%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        count = battle.get_team_counter(team, 'defense_skill')
        if count <= 0:
            return []
        for bs in sprite.skills:
            if bs.element in ('机械', '地'):
                bs.power_mod += int(bs.base.power * count * 0.1)
        return [f'{sprite.name} 定向精炼: 机械/地技能威力+{count*10}%']


@register("渗透")
class InfiltrationAcc(TraitHandler):
    """己方精灵每使用1次武/地系技能，入场时攻防+5%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        wu = battle.get_team_counter(team, 'element:武')
        di = battle.get_team_counter(team, 'element:地')
        total = wu + di
        if total <= 0:
            return []
        sprite.add_effect(StatusEffect(
            name='攻防+', category='stat', stat_key='atk',
            steps=total, scope='permanent', source='渗透'))
        sprite.add_effect(StatusEffect(
            name='攻防+', category='stat', stat_key='def',
            steps=total, scope='permanent', source='渗透'))
        return [f'{sprite.name} 渗透: 攻防+{total*5}%({total}次)']


@register("搜刮")
class Loot(TraitHandler):
    """敌方每使用1次【聚能】或更换精灵，自己入场时魔攻+20%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp_team = 'B' if team == 'A' else 'A'
        gather = battle.get_team_counter(opp_team, 'enemy_gather')
        switch = battle.get_team_counter(opp_team, 'enemy_switch')
        count = gather + switch
        if count <= 0:
            return []
        sprite.add_effect(StatusEffect(
            name='魔攻+', category='stat', stat_key='sp_atk',
            steps=count * 2, scope='permanent', source='搜刮'))
        return [f'{sprite.name} 搜刮: 魔攻+{count*20}%({count}次)']


# ═══════════════════════════════════════════════════════════════
# Aura traits（在场时持续生效的战场光环）
# ═══════════════════════════════════════════════════════════════

@register("冰封")
class FrozenAura(TraitHandler):
    """在场时，敌方全技能能耗+1。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp = battle.get_opponent(team).active
        if not opp.is_fainted:
            opp.add_effect(StatusEffect(
                name='能耗+1', category='stat', stat_key='energy_cost',
                steps=1, scope='battlefield', source='冰封'))
        return [f'{sprite.name} 冰封: 敌方能耗+1']

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        opp = battle.get_opponent(team).active
        if not opp.is_fainted:
            opp.remove_effect('能耗+1')
        return []


@register("陨落")
class Downfall(TraitHandler):
    """在场时，双方回合结束时效果触发次数-1。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        battle._downfall_active = True
        return [f'{sprite.name} 陨落: 双方效果触发次数-1']

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        battle._downfall_active = False
        return []


@register("双向光速")
class BidirectionalLight(TraitHandler):
    """在场时，所有回合结束时触发次数+1。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        battle._light_speed_active = True
        return [f'{sprite.name} 双向光速: 效果触发次数+1']

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        battle._light_speed_active = False
        return []


@register("煤渣草")
class CinderGrass(TraitHandler):
    """在场时，所有灼烧的衰减变为增长。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        battle._cinder_grass_active = True
        return [f'{sprite.name} 煤渣草: 灼烧衰减→增长']

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        battle._cinder_grass_active = False
        return []


@register("无差别过滤")
class IndiscriminateFilter(TraitHandler):
    """在场时，所有精灵连击数固定为2。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        use.modifiers['multi_hit'] = 2
        return []


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


@register("防过载保护")
class OverloadProtection(TraitHandler):
    """每次行动后脱离。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        # 标记需要在回合结束时触发脱离
        user._escape_pending = True
        return []


# ═══════════════════════════════════════════════════════════════
# Conditional damage resistance（条件减伤）
# ═══════════════════════════════════════════════════════════════

@register("偏振")
class Polarization(TraitHandler):
    """受到自己携带技能系别的攻击伤害-40%。"""

    def on_defend(self, target: Sprite, attacker: Sprite, use: SkillUse,
                  battle: Battle, team: str) -> list[str]:
        my_elements = {bs.element for bs in target.skills if bs.element}
        if use.battle_skill.element in my_elements:
            use.modifiers['damage_mult'] = use.modifiers.get('damage_mult', 1.0) * 0.6
        return []


@register("完全偏振")
class FullPolarization(TraitHandler):
    """抵抗自己携带技能系别的攻击伤害。"""

    def on_defend(self, target: Sprite, attacker: Sprite, use: SkillUse,
                  battle: Battle, team: str) -> list[str]:
        my_elements = {bs.element for bs in target.skills if bs.element}
        if use.battle_skill.element in my_elements:
            use.modifiers['damage_mult'] = 0.0
        return []


@register("绝对秩序")
class AbsoluteOrder(TraitHandler):
    """受到非敌方系别的技能攻击时伤害-50%。"""

    def on_defend(self, target: Sprite, attacker: Sprite, use: SkillUse,
                  battle: Battle, team: str) -> list[str]:
        attacker_elements = set(attacker.species.elements)
        if use.battle_skill.element not in attacker_elements:
            use.modifiers['damage_mult'] = use.modifiers.get('damage_mult', 1.0) * 0.5
        return []


@register("毒腺")
class PoisonGland(TraitHandler):
    """使用能耗<=1的技能时，敌方获得4层中毒。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.energy_cost <= 1:
            target = battle.get_opponent(team).active
            if not target.is_fainted:
                target.add_effect(StatusEffect(
                    name='中毒', category='abnormal', stacks=4, source='毒腺'))
                return [f'{user.name} 毒腺: {target.name} 中毒×4']
        return []


@register("生物碱")
class Alkaloid(TraitHandler):
    """使用草系技能时，敌方获得2层中毒。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '草':
            target = battle.get_opponent(team).active
            if not target.is_fainted:
                target.add_effect(StatusEffect(
                    name='中毒', category='abnormal', stacks=2, source='生物碱'))
                return [f'{user.name} 生物碱: {target.name} 中毒×2']
        return []


@register("高浓生物碱")
class HighAlkaloid(TraitHandler):
    """使用技能时，敌方获得2层中毒。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        target = battle.get_opponent(team).active
        if not target.is_fainted:
            target.add_effect(StatusEffect(
                name='中毒', category='abnormal', stacks=2, source='高浓生物碱'))
            return [f'{user.name} 高浓生物碱: {target.name} 中毒×2']
        return []


@register("扩散侵蚀")
class SpreadingErosion(TraitHandler):
    """使用水系技能后，敌方获得中毒(层数=中毒印记层数×2)。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element != '水':
            return []
        opp_team = 'B' if team == 'A' else 'A'
        pos, neg = battle.globals.get_marks(opp_team)
        poison_mark = None
        for m in pos + neg:
            if m.name == '中毒印记':
                poison_mark = m
                break
        if not poison_mark:
            return []
        stacks = poison_mark.stacks * 2
        target = battle.get_opponent(team).active
        if not target.is_fainted:
            target.add_effect(StatusEffect(
                name='中毒', category='abnormal', stacks=stacks, source='扩散侵蚀'))
            return [f'{user.name} 扩散侵蚀: {target.name} 中毒×{stacks}']
        return []


# ═══════════════════════════════════════════════════════════════
# KO / Faint 特殊效果
# ═══════════════════════════════════════════════════════════════

@register("诈死")
class FakeDeath(TraitHandler):
    """自己力竭时，少损失1点魔力。"""

    def on_faint(self, sprite: Sprite, killer: Sprite | None,
                 battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        player.lives += 1
        return [f'{sprite.name} 诈死: 少损失1魔力']


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


@register("夺目")
class Dazzling(TraitHandler):
    """额外获得三个未携带的随机技能，非光系技能威力+25%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        # 随机技能部分需技能池基础设施 — 暂未实现
        return []

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if use.battle_skill.element != '光':
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.25
        return []


# ═══════════════════════════════════════════════════════════════
# Other conditional/passive traits
# ═══════════════════════════════════════════════════════════════

@register("最好的伙伴")
class BestPartner(TraitHandler):
    """造成克制伤害后，获得攻防速+20%并回复2能量。"""

    def on_damage(self, user: Sprite, target: Sprite, use: SkillUse,
                  battle: Battle, team: str) -> list[str]:
        type_mult = use.modifiers.get('type_mult', 1.0)
        if type_mult <= 1.0:
            return []
        for key in ('atk', 'def', 'speed'):
            user.add_effect(StatusEffect(
                name='攻防速+20%', category='stat', stat_key=key,
                steps=2, scope='battlefield', source='最好的伙伴'))
        gained = user.gain_energy(2)
        return [f'{user.name} 最好的伙伴: 克制! 攻防速+20%, +{gained}E']


@register("警惕")
class Vigilant(TraitHandler):
    """回合结束时，若自己能量为0则脱离。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        if sprite.energy <= 0:
            sprite._escape_pending = True
            return [f'{sprite.name} 警惕: 能量耗尽, 回合末脱离']
        return []


@register("星地善良")
class StargroundKind(TraitHandler):
    """回合结束时若场上己方精灵能量=0，自己立即替换之。"""
    # 实现在 Battle._phase_turn_end 中的 bench 扫描逻辑
    pass


@register("奔波命")
class RunningLife(TraitHandler):
    """使用防御技能后，回合结束时脱离。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.base.is_defense:
            user._escape_pending = True
        return []


@register("对流")
class Convection(TraitHandler):
    """自己的能耗增加变为降低；降低变为增加。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        total = sum(e.steps for e in user.effects if e.category == 'stat'
                    and e.stat_key == 'energy_cost')
        if total == 0:
            return []
        correction = 2 * total
        if correction > 0:
            user.gain_energy(correction)
            return [f'{user.name} 对流: 能耗反转, 返还{correction}E']
        else:
            user.lose_energy(-correction)
            return [f'{user.name} 对流: 能耗反转, 额外消耗{-correction}E']


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


@register("贪婪")
class Greedy(TraitHandler):
    """敌方精灵离场后，其增益/减益被换上来的精灵继承。"""

    def on_enemy_leave(self, sprite: Sprite, enemy_old: Sprite,
                        enemy_new: Sprite, battle: Battle, team: str) -> list[str]:
        inherited = [e for e in enemy_old.effects if e.scope == 'battlefield']
        if not inherited:
            return []
        for e in inherited:
            enemy_new.add_effect(e)
        return [f'{sprite.name} 贪婪: {enemy_new.name} 继承{len(inherited)}个效果']


@register("特殊清洁场景")
class SpecialCleaning(TraitHandler):
    """回合结束时偷取敌方1层印记，己方队伍获得1次随机奉献。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp_team = 'B' if team == 'A' else 'A'
        pos, neg = battle.globals.get_marks(opp_team)
        all_marks = pos + neg
        if not all_marks:
            return []
        target = all_marks[0]
        if target.stacks > 0:
            target.stacks -= 1
            # 转移到己方
            if target.name == '星陨印记':
                battle.globals.apply_mark(team, target.name, 'negative', 1)
            player = battle.get_player(team)
            player.lives += 1
            return [f'{sprite.name} 特殊清洁: 偷取{target.name}×1, 奉献+1']
        return []


@register("蚀刻")
class Etching(TraitHandler):
    """回合结束时，敌方每2层中毒转化为1层中毒印记。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp_team = 'B' if team == 'A' else 'A'
        target = battle.get_opponent(team).active
        poison = [e for e in target.effects if e.category == 'abnormal' and e.name == '中毒']
        total_stacks = sum(e.stacks for e in poison)
        if total_stacks >= 2:
            marks = total_stacks // 2
            # 移除消耗的中毒层数
            consumed = marks * 2
            for e in poison:
                remove = min(e.stacks, consumed)
                e.stacks -= remove
                consumed -= remove
                if consumed <= 0:
                    break
            battle.globals.apply_mark(opp_team, '中毒印记', 'negative', marks)
            return [f'{sprite.name} 蚀刻: 中毒→印记×{marks}']

        return []


@register("暴食")
class Gluttony(TraitHandler):
    """携带的龙系技能获得迅捷（先手+1）。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        for bs in sprite.skills:
            if bs.element == '龙' and bs.priority < 1:
                bs.base.priority += 1  # 直接修改 base priority
        return []


@register("电流刺激")
class ElectricStim(TraitHandler):
    """携带的攻击技能获得迸发：威力+40。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not user.first_action or not use.battle_skill.is_attack:
            return []
        use.battle_skill.power_mod += 40
        return [f'{user.name} 电流刺激迸发: {use.battle_skill.name} 威力+40']


@register("生物电")
class Bioelectricity(TraitHandler):
    """携带的电系技能获得迸发：能耗-2。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not user.first_action or use.battle_skill.element != '电':
            return []
        user.gain_energy(2)
        return [f'{user.name} 生物电迸发: {use.battle_skill.name} 能耗-2']


@register("超负荷")
class Overload(TraitHandler):
    """攻击技能获得迸发：敌方全技能能耗+1。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not user.first_action or not use.battle_skill.is_attack:
            return []
        target = battle.get_opponent(team).active
        if target.is_fainted:
            return []
        target.add_effect(StatusEffect(
            name='能耗+1', category='stat', stat_key='energy_cost',
            steps=1, scope='battlefield', source='超负荷'))
        return [f'{user.name} 超负荷迸发: {target.name} 全技能能耗+1']


@register("连续负荷")
class ContinuousLoad(TraitHandler):
    """技能的迸发效果延长1回合。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not user.first_action:
            return []
        if getattr(user, '_burst_extended_once', False):
            return []
        user._burst_extended_once = True
        user._burst_remaining = 1
        return [f'{user.name} 连续负荷: 迸发延长1回合']

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite._burst_extended_once = False
        return []


@register("起飞加速")
class TakeoffAccel(TraitHandler):
    """本场战斗首次使用的技能获得迅捷（先手+1）。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if getattr(user, '_first_skill_used', False):
            return []
        user._first_skill_used = True
        use.battle_skill.priority_mod_temp = use.battle_skill.priority_mod_temp + 1
        return [f'{user.name} 起飞加速: {use.battle_skill.name} 先手+1']


@register("缩壳")
class ShellShrink(TraitHandler):
    """携带的防御技能能耗-2。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        for bs in sprite.skills:
            if bs.base.is_defense:
                bs.base.energy_cost = max(0, bs.base.energy_cost - 2)
        return []


@register("向心力")
class Centripetal(TraitHandler):
    """1号位和2号位技能获得传动1和威力+30。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        for i in (0, 1):
            if i < len(sprite.skills):
                bs = sprite.skills[i]
                if bs._transmission < 1:
                    bs._transmission = 1
                    bs.power_mod += 30
        return []


@register("翼轴")
class WingAxis(TraitHandler):
    """1号位技能获得迅捷和传动1。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        if sprite.skills:
            bs = sprite.skills[0]
            if bs._transmission < 1:
                bs._transmission = 1
                bs.base.priority += 1
        return []


@register("快锤")
class QuickHammer(TraitHandler):
    """能耗<3的技能获得迅捷。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        for bs in sprite.skills:
            if bs.energy_cost < 3:
                bs.priority_mod_perm = bs.priority_mod_perm + 1 if hasattr(bs, 'priority_mod_perm') else 1
                bs.base.priority += 1
        return []


# ═══════════════════════════════════════════════════════════════
# 裂口组 / 复杂状态转换
# ═══════════════════════════════════════════════════════════════

@register("衡量")
class MeasureTrait(TraitHandler):
    """入场时复制敌方增益；在场时若敌方获得增益自己也会获得。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp = battle.get_opponent(team).active
        buffs = [e for e in opp.effects if e.is_stat and e.steps > 0]
        if not buffs:
            return []
        for e in buffs:
            sprite.add_effect(StatusEffect(
                name=f'复制{e.name}', category=e.category, stat_key=e.stat_key,
                steps=e.steps, scope='battlefield', source='衡量'))
        return [f'{sprite.name} 衡量: 复制{len(buffs)}个增益']


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


@register("铃兰晚钟")
class LilyBell(TraitHandler):
    """首次入场时失去一半当前生命。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        if sprite.counters.get('times_entered', 0) > 1:
            return []
        lost = round(sprite.current_hp * 0.5)
        sprite.take_damage(lost)
        return [f'{sprite.name} 铃兰晚钟: 失去{lost}HP']


@register("月牙雪糕")
class CrescentIceCream(TraitHandler):
    """使用攻击技能时，敌方每层冻结视为1层额外星陨印记。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.base.is_attack:
            return []
        opp_team = 'B' if team == 'A' else 'A'
        target = battle.get_opponent(team).active
        freeze = [e for e in target.effects
                  if e.category == 'abnormal' and e.name == '冻结']
        total_freeze = sum(e.stacks for e in freeze)
        if total_freeze <= 0:
            return []
        battle.globals.apply_mark(opp_team, '星陨印记', 'negative', total_freeze)
        return [f'{user.name} 月牙雪糕: 冻结×{total_freeze} → 星陨印记×{total_freeze}']


@register("灰色肖像")
class GreyPortrait(TraitHandler):
    """攻击使敌方已有的减益层数+3。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if not skill.base.is_attack:
            return []
        target = battle.get_opponent(team).active
        if target.is_fainted:
            return []
        debuffs = [e for e in target.effects if e.is_stat and e.steps < 0]
        if not debuffs:
            return []
        for e in debuffs:
            e.steps -= 3
        return [f'{user.name} 灰色肖像: {target.name} {len(debuffs)}个减益+3层']


@register("守望星")
class Starguard(TraitHandler):
    """触发星陨时消耗一半层数，仍造成满层伤害。"""
    # 实现在 GlobalEffects.consume_starfall_stacks: 若 user 有此特性 → 消耗减半


@register("仁心")
class Benevolence(TraitHandler):
    """敌方受到灼烧伤害时，自己回复等量生命。"""

    def on_abnormal_tick(self, sprite: Sprite, effect_name: str, damage: int,
                         battle: Battle, team: str) -> list[str]:
        if effect_name != '灼烧' or damage <= 0:
            return []
        healed = sprite.heal(damage)
        return [f'{sprite.name} 仁心: +{healed}HP'] if healed else []


@register("耐活王")
class SurvivalKing(TraitHandler):
    """敌方受到中毒伤害时，自己回复等量生命。"""

    def on_abnormal_tick(self, sprite: Sprite, effect_name: str, damage: int,
                         battle: Battle, team: str) -> list[str]:
        if effect_name != '中毒' or damage <= 0:
            return []
        healed = sprite.heal(damage)
        return [f'{sprite.name} 耐活王: +{healed}HP'] if healed else []


@register("悲悯")
class Compassion(TraitHandler):
    """己方队伍每有1只力竭精灵，双攻+30%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        fainted = sum(1 for s in player.team if s.is_fainted)
        if fainted <= 0:
            return []
        steps = fainted * 3
        for key in ('atk', 'sp_atk'):
            sprite.add_effect(StatusEffect(
                name='双攻+', category='stat', stat_key=key,
                steps=steps, scope='battlefield', source='悲悯'))
        return [f'{sprite.name} 悲悯: 双攻+{fainted*30}%({fainted}只力竭)']


@register("悼亡")
class Mourning(TraitHandler):
    """双方队伍每有1只力竭精灵，双攻+30%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp_team = 'B' if team == 'A' else 'A'
        my_fainted = sum(1 for s in battle.get_player(team).team if s.is_fainted)
        opp_fainted = sum(1 for s in battle.get_player(opp_team).team if s.is_fainted)
        total = my_fainted + opp_fainted
        if total <= 0:
            return []
        steps = total * 3
        for key in ('atk', 'sp_atk'):
            sprite.add_effect(StatusEffect(
                name='双攻+', category='stat', stat_key=key,
                steps=steps, scope='battlefield', source='悼亡'))
        return [f'{sprite.name} 悼亡: 双攻+{total*30}%({total}只力竭)']


@register("腐植循环")
class HumusCycle(TraitHandler):
    """每回复1能量，同时回复5%生命。"""

    def on_energy_change(self, sprite: Sprite, delta: int, new_energy: int,
                         battle: Battle, team: str) -> list[str]:
        if delta > 0:
            amount = round(sprite.max_hp * 0.05 * delta)
            healed = sprite.heal(amount)
            return [f'{sprite.name} 腐植循环: +{healed}HP'] if healed else []
        return []


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


@register("游弋")
class Cruising(TraitHandler):
    """蓄力时可使用任一携带技能，且获得双防+100%。"""

    def on_turn_start(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        if not getattr(sprite, '_charging', False):
            return []
        for key in ('def', 'sp_def'):
            # 避免重复叠加
            if any(e.source == '游弋' and e.stat_key == key for e in sprite.effects):
                continue
            sprite.add_effect(StatusEffect(
                name='双防+100%', category='stat', stat_key=key,
                steps=10, scope='battlefield', source='游弋'))
        return []


@register("嫉妒")
class Jealousy(TraitHandler):
    """蓄力状态下可使用任一携带技能。"""
    # agent 已处理：蓄力中 + 嫉妒 trait → 跳过 charged_skill_index 强制选择


@register("天通地明")
class HeavenEarth(TraitHandler):
    """攻击时若敌方血脉是污染血脉，威力+100%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.is_attack:
            return []
        opp = battle.get_opponent(team).active
        if opp.bloodline == '污染':
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 1.0
        return []


@register("月光审判")
class MoonJudgment(TraitHandler):
    """攻击时若敌方血脉是首领血脉，威力+100%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.is_attack:
            return []
        opp = battle.get_opponent(team).active
        if opp.bloodline == '首领':
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 1.0
        return []


@register("绒粉星光")
class VelvetStarlight(TraitHandler):
    """攻击时若敌方血脉是非本系的系别血脉，威力+100%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.is_attack:
            return []
        opp = battle.get_opponent(team).active
        from scripts.common.constants import ELEMENTAL_BLOODLINES
        if opp.bloodline in ELEMENTAL_BLOODLINES and opp.bloodline not in user.species.attributes:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 1.0
        return []


@register("虫群突袭")
class SwarmRaid(TraitHandler):
    """队伍中每有1只其他虫系精灵，入场时获得攻防速+15%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        count = sum(1 for s in player.team if '虫' in s.species.elements and s is not sprite)
        if count <= 0:
            return []
        steps = count * 3  # 15% per = 3 steps per
        for key in ('atk', 'def', 'speed'):
            sprite.add_effect(StatusEffect(
                name='攻防速+', category='stat', stat_key=key,
                steps=steps, scope='battlefield', source='虫群突袭'))
        return [f'{sprite.name} 虫群突袭: 攻防速+{count*15}% ({count}只虫系)']


@register("虫群鼓舞")
class SwarmInspire(TraitHandler):
    """队伍中每有1只其他虫系精灵，入场时获得攻防速+10%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        count = sum(1 for s in player.team if '虫' in s.species.elements and s is not sprite)
        if count <= 0:
            return []
        steps = count * 2  # 10% per = 2 steps per
        for key in ('atk', 'def', 'speed'):
            sprite.add_effect(StatusEffect(
                name='攻防速+', category='stat', stat_key=key,
                steps=steps, scope='battlefield', source='虫群鼓舞'))
        return [f'{sprite.name} 虫群鼓舞: 攻防速+{count*10}% ({count}只虫系)']


@register("守护者")
class Guardian(TraitHandler):
    """己方其他精灵每有1层萌化，自己入场时全技能能耗-1。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        moe = 0
        for s in player.team:
            if s is sprite or s.is_fainted:
                continue
            moe += sum(e.stacks for e in s.effects
                       if e.category == 'abnormal' and e.name == '萌化')
        if moe <= 0:
            return []
        sprite.add_effect(StatusEffect(
            name='能耗-', category='stat', stat_key='energy_cost',
            steps=-moe, scope='battlefield', source='守护者'))
        return [f'{sprite.name} 守护者: 全技能能耗-{moe}']


@register("抓到你")
class Gotcha(TraitHandler):
    """入场时敌方获得2层冻结；使敌方冻结时也使其全技能能耗+1。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        target = battle.get_opponent(team).active
        if not target.is_fainted:
            target.add_effect(StatusEffect(
                name='冻结', category='abnormal', stacks=2, source='抓到你'))
            return [f'{sprite.name} 抓到你: {target.name} 冻结×2']
        return []

    def on_inflict(self, user: Sprite, target: Sprite, effect_name: str,
                   battle: Battle, team: str) -> list[str]:
        if effect_name == '冻结':
            target.add_effect(StatusEffect(
                name='能耗+1', category='stat', stat_key='energy_cost',
                steps=1, scope='battlefield', source='抓到你'))
            return [f'{user.name} 抓到你: {target.name} 全技能能耗+1']
        return []


@register("蓄电池")
class Battery(TraitHandler):
    """每入场1次，永久获得双攻+20%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        count = sprite.counters.get('times_entered', 0)
        if count <= 1:
            return []  # 首次入场不触发（0 → 1，入场后 increment 到 1）
        # 从第二次入场开始，每入场一次 +20%
        steps = count * 2
        # 清除旧效果，重新应用
        for e in list(sprite.effects):
            if e.source == '蓄电池':
                sprite.effects.remove(e)
        sprite.add_effect(StatusEffect(
            name='双攻+', category='stat', stat_key='atk',
            steps=steps, scope='permanent', source='蓄电池'))
        sprite.add_effect(StatusEffect(
            name='双攻+', category='stat', stat_key='sp_atk',
            steps=steps, scope='permanent', source='蓄电池'))
        return [f'{sprite.name} 蓄电池: 第{count}次入场, 双攻+{count*20}%']


@register("超级电池")
class SuperBattery(TraitHandler):
    """每入场1次，获得双攻永久+30%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        count = sprite.counters.get('times_entered', 0)
        if count <= 1:
            return []
        steps = count * 3
        for e in list(sprite.effects):
            if e.source == '超级电池':
                sprite.effects.remove(e)
        sprite.add_effect(StatusEffect(
            name='双攻+', category='stat', stat_key='atk',
            steps=steps, scope='permanent', source='超级电池'))
        sprite.add_effect(StatusEffect(
            name='双攻+', category='stat', stat_key='sp_atk',
            steps=steps, scope='permanent', source='超级电池'))
        return [f'{sprite.name} 超级电池: 第{count}次入场, 双攻+{count*30}%']


@register("泛音列")
class OvertoneSeries(TraitHandler):
    """使用状态技能后，敌方获得聒噪效果持续3回合。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if not skill.base.is_status:
            return []
        target = battle.get_opponent(team).active
        if target.is_fainted:
            return []
        target.add_effect(StatusEffect(
            name='聒噪', category='abnormal', stacks=1,
            scope='battlefield', source='泛音列'))
        return [f'{user.name} 泛音列: {target.name} 获得聒噪']


@register("贪心算法")
class GreedyAlgorithm(TraitHandler):
    """1号位技能获得传动1，且使用后使敌方获得6层灼烧。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        if sprite.skills:
            sprite.skills[0]._transmission = 1
        return []

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill is not user.skills[0]:
            return []
        target = battle.get_opponent(team).active
        if target.is_fainted:
            return []
        target.add_effect(StatusEffect(
            name='灼烧', category='abnormal', stacks=6, source='贪心算法'))
        return [f'{user.name} 贪心算法: {target.name} 灼烧×6']


@register("机械变式")
class MechanicalVariation(TraitHandler):
    """技能每回合位置变化时，该技能能耗-1。"""
    # 实现在 Battle._phase_turn_start 传动后检查中


@register("洄游")
class Migration(TraitHandler):
    """每次进入蓄力状态，获得全技能能耗永久-1。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        charging = getattr(user, '_charging', False)
        if charging and not getattr(user, '_migration_cycle', False):
            user._migration_cycle = True
            user.add_effect(StatusEffect(
                name='能耗-1', category='stat', stat_key='energy_cost',
                steps=-1, scope='permanent', source='洄游'))
            return [f'{user.name} 洄游: 全技能能耗-1']
        elif not charging:
            user._migration_cycle = False  # 蓄力释放后重置
        return []


@register("溶解腐蚀")
class DissolveCorrosion(TraitHandler):
    """每携带1个毒系技能进入战斗，水系技能使敌方获得2层中毒。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element != '水':
            return []
        poison_count = sum(1 for bs in user.skills if bs.element == '毒')
        if poison_count <= 0:
            return []
        target = battle.get_opponent(team).active
        stacks = poison_count * 2
        target.add_effect(StatusEffect(
            name='中毒', category='abnormal', stacks=stacks, source='溶解腐蚀'))
        return [f'{user.name} 溶解腐蚀: {target.name} 中毒×{stacks}({poison_count}毒技)']


@register("溶解扩散")
class DissolveSpread(TraitHandler):
    """每携带1个毒系技能进入战斗，水系技能使敌方获得1层中毒。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element != '水':
            return []
        poison_count = sum(1 for bs in user.skills if bs.element == '毒')
        if poison_count <= 0:
            return []
        target = battle.get_opponent(team).active
        target.add_effect(StatusEffect(
            name='中毒', category='abnormal', stacks=poison_count, source='溶解扩散'))
        return [f'{user.name} 溶解扩散: {target.name} 中毒×{poison_count}({poison_count}毒技)']


@register("灵魂灼伤")
class SoulBurn(TraitHandler):
    """冰系技能使敌方获得4层灼烧，火系技能使敌方获得2层冻结。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        target = battle.get_opponent(team).active
        if skill.element == '冰':
            target.add_effect(StatusEffect(
                name='灼烧', category='abnormal', stacks=4, source='灵魂灼伤'))
            return [f'{user.name} 灵魂灼伤: {target.name} 灼烧×4']
        elif skill.element == '火':
            target.add_effect(StatusEffect(
                name='冻结', category='abnormal', stacks=2, source='灵魂灼伤'))
            return [f'{user.name} 灵魂灼伤: {target.name} 冻结×2']
        return []


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


@register("飓风")
class HurricaneTrait(TraitHandler):
    """被击败时额外损失1魔力。"""

    def on_faint(self, sprite: Sprite, killer: Sprite | None,
                 battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        player.lives -= 1
        return [f'{sprite.name} 飓风: 额外损失1魔力({player.name} 魔力→{player.lives})']


@register("御驾亲征")
class RoyalCampaign(TraitHandler):
    """棋契陛下大幅提升种族资质，力竭时扣除4魔力。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        for key in ['atk', 'sp_atk', 'def', 'sp_def', 'speed']:
            sprite.add_effect(StatusEffect(
                name='御驾亲征', category='stat', stat_key=key, steps=3,
                scope='permanent', source='御驾亲征'))
        return [f'{sprite.name} 御驾亲征: 全属性+30%']

    def on_faint(self, sprite: Sprite, killer: Sprite | None,
                 battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        extra = 3  # 总共扣4魔力（默认已扣1，再扣3）
        player.lives = max(0, player.lives - extra)
        return [f'{sprite.name} 御驾亲征: 额外扣除3魔力({player.name} 魔力→{player.lives})']


