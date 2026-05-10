"""scripts/sim/traits/_conditional.py — Conditional 级特性（条件判断 / 多步效果）

每个特性 5-20 行，基于 if/else 条件判断。
"""

from . import register, TraitHandler
from scripts.sim.sprite import StatusEffect, Sprite
from scripts.sim.battle import Battle
from scripts.sim.battleskill import BattleSkill, SkillUse
from scripts.sim.globals import GlobalEffects


# ═══════════════════════════════════════════════════════════════
# on_modifier — 修改技能参数（条件判断）
# ═══════════════════════════════════════════════════════════════

@register("勇敢")
class Brave(TraitHandler):
    """能耗>3的技能威力+40%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if use.battle_skill.energy_cost > 3:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.4
        return []


@register("共鸣")
class Resonance(TraitHandler):
    """【虫鸣】技能威力+20。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if use.battle_skill.name == '虫鸣':
            use.battle_skill.power_mod += 20
        return []


@register("挺起胸脯")
class ChestOut(TraitHandler):
    """能耗为1的技能威力+50%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if use.battle_skill.energy_cost == 1:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.5
        return []


@register("涂鸦")
class Graffiti(TraitHandler):
    """使用非本系技能时威力+50%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        skill_elem = use.battle_skill.element
        if skill_elem and skill_elem not in user.species.elements:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.5
        return []


@register("目空")
class Arrogant(TraitHandler):
    """非光系技能威力+25%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        if use.battle_skill.element != '光':
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.25
        return []


@register("冰钻")
class IceDrill(TraitHandler):
    """敌方技能总能耗每1点 → 攻击威力+10%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        target = battle.get_opponent(team).active
        total = sum(bs.energy_cost for bs in target.skills)
        use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + total * 0.1
        return []


@register("变形活画")
class LivingPainting(TraitHandler):
    """敌方每有1层增益，攻击威力+10%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        target = battle.get_opponent(team).active
        buffs = sum(e.steps for e in target.effects if e.is_stat and e.steps > 0)
        if buffs > 0:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + buffs * 0.1
        return []


@register("血型吸引")
class BloodAttract(TraitHandler):
    """敌方每携带1种系别的技能，攻击威力+10%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        target = battle.get_opponent(team).active
        elements = set(bs.element for bs in target.skills if bs.element)
        use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + len(elements) * 0.1
        return []


@register("坠星")
class FallingStar(TraitHandler):
    """敌方每有1层星陨印记，技能威力+15%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        opp_team = 'B' if team == 'A' else 'A'
        _, neg = battle.globals.get_marks(opp_team)
        if neg and neg.name == '星陨印记':
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + neg.stacks * 0.15
        return []


@register("观星")
class Stargazing(TraitHandler):
    """敌方每有1层星陨印记，地系技能威力+15%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        if use.battle_skill.element != '地':
            return []
        opp_team = 'B' if team == 'A' else 'A'
        _, neg = battle.globals.get_marks(opp_team)
        if neg and neg.name == '星陨印记':
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + neg.stacks * 0.15
        return []


@register("破空")
class Skybreaker(TraitHandler):
    """先于敌方攻击时，威力+75%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        if use.is_first:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.75
        return []


@register("顺风")
class Tailwind(TraitHandler):
    """先于敌方攻击时，威力+50%。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        if not use.battle_skill.is_attack:
            return []
        if use.is_first:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) + 0.5
        return []


@register("得寸进尺")
class PushLuck(TraitHandler):
    """天气为雨天或水系环境时，双攻+100%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return self._check_weather(sprite, battle)

    def on_turn_start(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return self._check_weather(sprite, battle)

    def _check_weather(self, sprite: Sprite, battle: Battle) -> list[str]:
        weather = battle.globals.weather
        if weather in ('rain',):
            existing = [e for e in sprite.effects if e.source == '得寸进尺']
            if not existing:
                sprite.add_effect(StatusEffect(
                    name='双攻+100%', category='stat', stat_key='atk',
                    steps=10, scope='battlefield', source='得寸进尺'))
                sprite.add_effect(StatusEffect(
                    name='双攻+100%', category='stat', stat_key='sp_atk',
                    steps=10, scope='battlefield', source='得寸进尺'))
                return [f'{sprite.name} 得寸进尺: 双攻+100%']
            return []
        else:
            removed = False
            for e in list(sprite.effects):
                if e.source == '得寸进尺':
                    sprite.effects.remove(e)
                    removed = True
            return [f'{sprite.name} 得寸进尺: 效果消失'] if removed else []


@register("保守派")
class Conservative(TraitHandler):
    """总技能能耗<4时，双防+80%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return self._apply(sprite)

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        return self._apply(user)

    def _apply(self, sprite: Sprite) -> list[str]:
        total_cost = sum(bs.energy_cost for bs in sprite.skills)
        if total_cost < 4:
            existing = [e for e in sprite.effects if e.source == '保守派']
            if not existing:
                sprite.add_effect(StatusEffect(
                    name='双防+80%', category='stat', stat_key='def',
                    steps=8, scope='persistent', source='保守派'))
                sprite.add_effect(StatusEffect(
                    name='双防+80%', category='stat', stat_key='sp_def',
                    steps=8, scope='persistent', source='保守派'))
                return [f'{sprite.name} 保守派: 双防+80%']
        return []


@register("侵蚀")
class Erosion(TraitHandler):
    """敌方每有1层中毒，自己连击数+1。"""

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        target = battle.get_opponent(team).active
        poison = sum(
            e.stacks for e in target.effects
            if e.category == 'abnormal' and e.name == '中毒'
        )
        if poison > 0:
            use.modifiers['multi_hit'] = use.modifiers.get('multi_hit', 0) + poison
        return []


# ═══════════════════════════════════════════════════════════════
# on_entry — 入场触发（条件判断）
# ═══════════════════════════════════════════════════════════════

@register("壮胆")
class Embolden(TraitHandler):
    """队伍存在虫系精灵 → 双攻+50%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        has_bug = any('虫' in s.species.elements for s in player.team if not s.is_fainted)
        if has_bug:
            sprite.add_effect(StatusEffect(
                name='双攻+50%', category='stat', stat_key='atk',
                steps=5, scope='battlefield', source='壮胆',
            ))
            sprite.add_effect(StatusEffect(
                name='双攻+50%', category='stat', stat_key='sp_atk',
                steps=5, scope='battlefield', source='壮胆',
            ))
            return [f'{sprite.name} 壮胆: 双攻+50%']
        return []


@register("图书守卫者")
class BookGuardian(TraitHandler):
    """入场时若己方魔力值为1，获得双攻+50%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        if player.lives == 1:
            sprite.add_effect(StatusEffect(
                name='双攻+50%', category='stat', stat_key='atk',
                steps=5, scope='battlefield', source='图书守卫者',
            ))
            sprite.add_effect(StatusEffect(
                name='双攻+50%', category='stat', stat_key='sp_atk',
                steps=5, scope='battlefield', source='图书守卫者',
            ))
            return [f'{sprite.name} 图书守卫者: 双攻+50%']
        return []


@register("构装契约者")
class ContractBuilder(TraitHandler):
    """入场时若敌方魔力值为1，获得双防+50%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp = battle.get_opponent(team)
        if opp.lives == 1:
            sprite.add_effect(StatusEffect(
                name='双防+50%', category='stat', stat_key='def',
                steps=5, scope='battlefield', source='构装契约者',
            ))
            sprite.add_effect(StatusEffect(
                name='双防+50%', category='stat', stat_key='sp_def',
                steps=5, scope='battlefield', source='构装契约者',
            ))
            return [f'{sprite.name} 构装契约者: 双防+50%']
        return []


@register("全神贯注")
class FullFocus(TraitHandler):
    """入场时获得物攻+100%，每次行动后-20%。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.add_effect(StatusEffect(
            name='物攻+100%', category='stat', stat_key='atk',
            steps=10, scope='battlefield', source='全神贯注',
        ))
        return [f'{sprite.name} 全神贯注: 物攻+100%']

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        for e in user.effects:
            if e.source == '全神贯注' and e.stat_key == 'atk':
                e.steps -= 2
                if e.steps <= 0:
                    user.effects.remove(e)
                    return [f'{user.name} 全神贯注: 效果消失']
        return []


# ═══════════════════════════════════════════════════════════════
# on_skill_use — 使用技能后触发
# ═══════════════════════════════════════════════════════════════

@register("助燃")
class Combustion(TraitHandler):
    """使用火系技能后，获得双攻+20%。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '火':
            user.add_effect(StatusEffect(
                name='双攻+20%', category='stat', stat_key='atk',
                steps=2, scope='battlefield', source='助燃',
            ))
            user.add_effect(StatusEffect(
                name='双攻+20%', category='stat', stat_key='sp_atk',
                steps=2, scope='battlefield', source='助燃',
            ))
            return [f'{user.name} 助燃: 双攻+20%']
        return []


@register("爆燃")
class ExplosiveBurn(TraitHandler):
    """使用火系技能后，获得双攻+30%。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '火':
            user.add_effect(StatusEffect(
                name='双攻+30%', category='stat', stat_key='atk',
                steps=3, scope='battlefield', source='爆燃',
            ))
            user.add_effect(StatusEffect(
                name='双攻+30%', category='stat', stat_key='sp_atk',
                steps=3, scope='battlefield', source='爆燃',
            ))
            return [f'{user.name} 爆燃: 双攻+30%']
        return []


@register("氧循环")
class OxygenCycle(TraitHandler):
    """使用草系技能后回复10%生命。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '草':
            amount = round(user.max_hp * 0.1)
            healed = user.heal(amount)
            return [f'{user.name} 氧循环: +{healed}HP'] if healed else []
        return []


@register("深层氧循环")
class DeepOxygen(TraitHandler):
    """使用草系技能后回复15%生命。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '草':
            amount = round(user.max_hp * 0.15)
            healed = user.heal(amount)
            return [f'{user.name} 深层氧循环: +{healed}HP'] if healed else []
        return []


@register("浸润")
class Infiltration(TraitHandler):
    """使用水系技能后，全技能能耗-1。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '水':
            user.add_effect(StatusEffect(
                name='能耗-1', category='stat', stat_key='energy_cost',
                steps=-1, scope='battlefield', source='浸润',
            ))
            return [f'{user.name} 浸润: 全技能能耗-1']
        return []


@register("浪潮")
class Wave(TraitHandler):
    """使用水系技能后，全技能能耗-2。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '水':
            user.add_effect(StatusEffect(
                name='能耗-2', category='stat', stat_key='energy_cost',
                steps=-2, scope='battlefield', source='浪潮',
            ))
            return [f'{user.name} 浪潮: 全技能能耗-2']
        return []


@register("乘风连击")
class WindCombo(TraitHandler):
    """使用翼系技能后，连击数+1。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element == '翼':
            user.add_effect(StatusEffect(
                name='连击+1', category='stat', stat_key='combo',
                steps=1, scope='battlefield', source='乘风连击',
            ))
            return [f'{user.name} 乘风连击: 连击+1']
        return []


@register("碰瓷")
class Provoke(TraitHandler):
    """使用恶系技能后，敌方失去2能量。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.element != '恶':
            return []
        target = battle.get_opponent(team).active
        if target.is_fainted:
            return []
        lost = target.lose_energy(2)
        return [f'{user.name} 碰瓷: {target.name} -{lost}E'] if lost else []


@register("鼓气")
class PepUp(TraitHandler):
    """使用能耗为3的技能时，获得攻防+20%。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.energy_cost == 3:
            for key in ('atk', 'def'):
                user.add_effect(StatusEffect(
                    name='攻防+20%', category='stat', stat_key=key,
                    steps=2, scope='battlefield', source='鼓气',
                ))
            return [f'{user.name} 鼓气: 攻防+20%']
        return []


@register("三鼓作气")
class TriplePep(TraitHandler):
    """使用能耗为3的技能时，获得攻防永久+20%。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if skill.energy_cost == 3:
            for key in ('atk', 'def'):
                user.add_effect(StatusEffect(
                    name='攻防+20%', category='stat', stat_key=key,
                    steps=2, scope='permanent', source='三鼓作气',
                ))
            return [f'{user.name} 三鼓作气: 攻防永久+20%']
        return []


@register("咔咔冲刺")
class ClickSprint(TraitHandler):
    """先于敌方行动时，行动后连击数+1。"""

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        if not skill.is_attack:
            return []
        # is_first is on SkillUse, not available here in on_skill_use
        # approximate: check speed comparison
        opp = battle.get_opponent(team).active
        if user.effective_stat('speed') >= opp.effective_stat('speed'):
            user.add_effect(StatusEffect(
                name='连击+1', category='stat', stat_key='combo',
                steps=1, scope='battlefield', source='咔咔冲刺',
            ))
            return [f'{user.name} 咔咔冲刺: 连击+1']
        return []


# ═══════════════════════════════════════════════════════════════
# on_counter_success — 应对成功后触发
# ═══════════════════════════════════════════════════════════════

@register("圣火骑士")
class HolyFireKnight(TraitHandler):
    """应对成功后，下次攻击威力翻倍。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        for bs in user.skills:
            if bs.is_attack:
                bs.next_attack_mult = 2.0
        return [f'{user.name} 圣火骑士: 下次攻击威力翻倍']


@register("斗技")
class CombatSkill(TraitHandler):
    """应对成功后，全技能威力永久+20。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        for bs in user.skills:
            bs.power_mod += 20
        return [f'{user.name} 斗技: 全技能威力+20']


@register("指挥家")
class Conductor(TraitHandler):
    """应对成功后，永久获得双攻+20%。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        user.add_effect(StatusEffect(
            name='双攻+20%', category='stat', stat_key='atk',
            steps=2, scope='permanent', source='指挥家',
        ))
        user.add_effect(StatusEffect(
            name='双攻+20%', category='stat', stat_key='sp_atk',
            steps=2, scope='permanent', source='指挥家',
        ))
        return [f'{user.name} 指挥家: 双攻永久+20%']


@register("思维之盾")
class MindShield(TraitHandler):
    """应对成功后，下次行动技能能耗-5。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        user.add_effect(StatusEffect(
            name='能耗-5(次)', category='stat', stat_key='energy_cost',
            steps=-5, scope='battlefield', source='思维之盾',
        ))
        return [f'{user.name} 思维之盾: 下次行动能耗-5']


@register("野性感官")
class WildSense(TraitHandler):
    """应对成功后，下次行动先手+1。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        user.add_effect(StatusEffect(
            name='先手+1', category='stat', stat_key='priority',
            steps=1, scope='battlefield', source='野性感官',
        ))
        return [f'{user.name} 野性感官: 下次行动先手+1']


@register("威慑")
class Deterrence(TraitHandler):
    """打断敌方时，被打断技能进入2回合冷却。"""

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        countered_skill.cooldown = 2
        return [f'{user.name} 威慑: {countered_skill.name} 冷却+2']


# ═══════════════════════════════════════════════════════════════
# on_energy_change — 能量变化时触发
# ═══════════════════════════════════════════════════════════════

@register("囤积")
class Hoard(TraitHandler):
    """每有1能量，获得双防+10%。动态更新。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return self._update(sprite, sprite.energy)

    def on_energy_change(self, sprite: Sprite, delta: int, new_energy: int,
                         battle: Battle, team: str) -> list[str]:
        return self._update(sprite, new_energy)

    def _update(self, sprite: Sprite, energy: int) -> list[str]:
        for e in list(sprite.effects):
            if e.source == '囤积':
                sprite.effects.remove(e)
        if energy <= 0:
            return []
        sprite.add_effect(StatusEffect(
            name='囤积双防', category='stat', stat_key='def',
            steps=energy, scope='persistent', source='囤积'))
        sprite.add_effect(StatusEffect(
            name='囤积双防', category='stat', stat_key='sp_def',
            steps=energy, scope='persistent', source='囤积'))
        return [f'{sprite.name} 囤积: 双防+{energy*10}%']


# ═══════════════════════════════════════════════════════════════
# on_take_damage — 受到攻击后触发
# ═══════════════════════════════════════════════════════════════

@register("坚韧铠甲")
class SturdyArmor(TraitHandler):
    """每受1次攻击，己方队伍获得1次随机奉献。"""

    def on_take_damage(self, target: Sprite, attacker: Sprite, damage: int,
                       battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        player.lives += 1
        return [f'{target.name} 坚韧铠甲: 奉献+1']


@register("嫁祸")
class Scapegoat(TraitHandler):
    """每失去25%生命，连击数+2。"""

    _THRESHOLDS: dict[str, int] = {}

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        self._THRESHOLDS[id(sprite)] = 0
        return []

    def on_take_damage(self, target: Sprite, attacker: Sprite, damage: int,
                       battle: Battle, team: str) -> list[str]:
        hp_pct = target.current_hp / target.max_hp
        lost_quarters = int((1.0 - hp_pct) / 0.25)
        prev = self._THRESHOLDS.get(id(target), 0)
        if lost_quarters > prev:
            gained = lost_quarters - prev
            self._THRESHOLDS[id(target)] = lost_quarters
            target.add_effect(StatusEffect(
                name='嫁祸连击', category='stat', stat_key='combo',
                steps=gained * 2, scope='battlefield', source='嫁祸'))
            return [f'{target.name} 嫁祸: 连击+{gained*2}']
        return []


# ═══════════════════════════════════════════════════════════════
# on_inflict — 对敌方施加效果时触发
# ═══════════════════════════════════════════════════════════════

@register("加个雪球")
class AddSnowball(TraitHandler):
    """使敌方获得冻结时，额外+2层冻结。"""

    def on_inflict(self, user: Sprite, target: Sprite, effect_name: str,
                   battle: Battle, team: str) -> list[str]:
        if effect_name != '冻结':
            return []
        target.add_effect(StatusEffect(
            name='冻结', category='abnormal', stacks=2, source='加个雪球'))
        return [f'{user.name} 加个雪球: {target.name} 额外冻结+2']


@register("捉迷藏")
class HideSeek(TraitHandler):
    """使敌方获得冻结时，全技能能耗+1。"""

    def on_inflict(self, user: Sprite, target: Sprite, effect_name: str,
                   battle: Battle, team: str) -> list[str]:
        if effect_name != '冻结':
            return []
        target.add_effect(StatusEffect(
            name='能耗+1', category='stat', stat_key='energy_cost',
            steps=1, scope='battlefield', source='捉迷藏'))
        return [f'{user.name} 捉迷藏: {target.name} 全技能能耗+1']


@register("毒牙")
class PoisonFang(TraitHandler):
    """使敌方获得中毒时，魔攻和魔防-40%。"""

    def on_inflict(self, user: Sprite, target: Sprite, effect_name: str,
                   battle: Battle, team: str) -> list[str]:
        if effect_name != '中毒':
            return []
        target.add_effect(StatusEffect(
            name='魔攻-40%', category='stat', stat_key='sp_atk',
            steps=-4, scope='battlefield', source='毒牙'))
        target.add_effect(StatusEffect(
            name='魔防-40%', category='stat', stat_key='sp_def',
            steps=-4, scope='battlefield', source='毒牙'))
        return [f'{user.name} 毒牙: {target.name} 魔攻魔防-40%']


# ═══════════════════════════════════════════════════════════════
# on_turn_end — 回合结束时触发（条件判断）
# ═══════════════════════════════════════════════════════════════

@register("复方汤剂")
class CompoundPotion(TraitHandler):
    """回合结束时，中毒效果触发次数+1（本回合多扣一次血）。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp = battle.get_opponent(team).active
        poison = [e for e in opp.effects if e.category == 'abnormal' and e.name == '中毒']
        if not poison:
            return []
        # 触发额外一次中毒伤害（标准中毒每层扣8% HP）
        total_stacks = sum(e.stacks for e in poison)
        dmg = round(opp.max_hp * 0.08 * total_stacks)
        if dmg > 0:
            opp.take_damage(dmg)
            return [f'{sprite.name} 复方汤剂: {opp.name} 额外中毒-{dmg}HP']
        return []


@register("扫拖一体")
class SweepMop(TraitHandler):
    """回合结束时驱散敌方1层印记，己方队伍获得1次奉献。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp_team = 'B' if team == 'A' else 'A'
        pos, neg = battle.globals.get_marks(opp_team)
        target = pos or neg
        if target and target.stacks > 0:
            target.stacks -= 1
            player = battle.get_player(team)
            player.lives += 1
            return [f'{sprite.name} 扫拖一体: 驱散{target.name}×1, 奉献+1']
        return []


@register("石天平")
class StoneScales(TraitHandler):
    """若使用技能能耗高于敌方，回合末敌方失去能耗之差的能量。"""

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        opp = battle.get_opponent(team).active
        if opp.is_fainted:
            return []
        my_cost = sum(bs.energy_cost for bs in sprite.skills)
        opp_cost = sum(bs.energy_cost for bs in opp.skills)
        diff = my_cost - opp_cost
        if diff > 0:
            lost = opp.lose_energy(diff)
            return [f'{sprite.name} 石天平: {opp.name} -{lost}E']
        return []


# ═══════════════════════════════════════════════════════════════
# on_ko_enemy — 击败敌方时触发
# ═══════════════════════════════════════════════════════════════

@register("恶魔的晚宴")
class DemonFeast(TraitHandler):
    """主动击败敌方时，获得双攻+50%。"""

    def on_ko_enemy(self, user: Sprite, victim: Sprite,
                    battle: Battle, team: str) -> list[str]:
        user.add_effect(StatusEffect(
            name='双攻+50%', category='stat', stat_key='atk',
            steps=5, scope='battlefield', source='恶魔的晚宴'))
        user.add_effect(StatusEffect(
            name='双攻+50%', category='stat', stat_key='sp_atk',
            steps=5, scope='battlefield', source='恶魔的晚宴'))
        return [f'{user.name} 恶魔的晚宴: 双攻+50%']


@register("振奋虫心")
class BugHeart(TraitHandler):
    """主动击败敌方后，己方队伍获得5次奉献。"""

    def on_ko_enemy(self, user: Sprite, victim: Sprite,
                    battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        player.lives += 5
        return [f'{user.name} 振奋虫心: 奉献+5']


# ═══════════════════════════════════════════════════════════════
# on_faint — 自身力竭时触发
# ═══════════════════════════════════════════════════════════════

@register("付给恶魔的赎价")
class DemonRansom(TraitHandler):
    """击败敌方时，敌方额外-1魔力；被击败时，自己额外-1魔力。"""

    def on_ko_enemy(self, user: Sprite, victim: Sprite,
                    battle: Battle, team: str) -> list[str]:
        opp = battle.get_opponent(team)
        if opp.lives > 0:
            opp.lives -= 1
            return [f'{user.name} 恶魔赎价: {opp.name} 额外-1魔力']
        return []

    def on_faint(self, sprite: Sprite, killer: Sprite | None,
                 battle: Battle, team: str) -> list[str]:
        player = battle.get_player(team)
        if player.lives > 0:
            player.lives -= 1
            return [f'{sprite.name} 恶魔赎价: 额外-1魔力']
        return []


@register("虚假宝箱")
class FakeChest(TraitHandler):
    """自己力竭时，敌方获得攻防+20%。"""

    def on_faint(self, sprite: Sprite, killer: Sprite | None,
                 battle: Battle, team: str) -> list[str]:
        if killer is None:
            return []
        killer.add_effect(StatusEffect(
            name='攻防+20%', category='stat', stat_key='atk',
            steps=2, scope='battlefield', source='虚假宝箱'))
        killer.add_effect(StatusEffect(
            name='攻防+20%', category='stat', stat_key='def',
            steps=2, scope='battlefield', source='虚假宝箱'))
        return [f'{sprite.name} 虚假宝箱: {killer.name} 攻防+20%']


# ═══════════════════════════════════════════════════════════════
# on_gain_effect — 获得效果时触发
# ═══════════════════════════════════════════════════════════════

@register("营养液泡")
class NutriVacuole(TraitHandler):
    """获得增益时，额外获得层数+2。"""

    def on_gain_effect(self, sprite: Sprite, effect,
                       battle: Battle, team: str) -> list[str]:
        if not effect.is_stat or effect.steps <= 0:
            return []
        effect.steps += 2
        return [f'{sprite.name} 营养液泡: {effect.name} 额外+2']


@register("自由飘")
class FreeFloat(TraitHandler):
    """每有1层萌化，连击数+2。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return self._apply(sprite)

    def on_gain_effect(self, sprite: Sprite, effect,
                       battle: Battle, team: str) -> list[str]:
        if effect.name != '萌化':
            return []
        return self._apply(sprite)

    def _apply(self, sprite: Sprite) -> list[str]:
        for e in list(sprite.effects):
            if e.source == '自由飘':
                sprite.effects.remove(e)
        moe = sum(
            e.stacks for e in sprite.effects
            if e.category == 'abnormal' and e.name == '萌化'
        )
        if moe > 0:
            sprite.add_effect(StatusEffect(
                name='连击+', category='stat', stat_key='combo',
                steps=moe * 2, scope='persistent', source='自由飘'))
            return [f'{sprite.name} 自由飘: 连击+{moe*2}']
        return []


# ═══════════════════════════════════════════════════════════════
# on_fatal_damage — 致命伤害前触发
# ═══════════════════════════════════════════════════════════════

@register("惊吓")
class Fright(TraitHandler):
    """能量=0的精灵无法对自己造成伤害。"""

    def on_fatal_damage(self, sprite: Sprite, damage: int,
                        battle: Battle, team: str) -> bool:
        opp_team = 'B' if team == 'A' else 'A'
        attacker = battle.get_opponent(team).active
        return attacker.energy == 0


@register("逐魂鸟")
class SoulChaser(TraitHandler):
    """能耗<=1的攻击技能无法对自己造成伤害。"""

    def on_fatal_damage(self, sprite: Sprite, damage: int,
                        battle: Battle, team: str) -> bool:
        # We don't have access to the attacking skill here, so this is approximate
        return False  # needs L2-level access; handled via _execute_single_action
