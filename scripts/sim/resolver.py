"""scripts/sim/resolver.py — 技能效果解析器

处理技能全效果：六维变化 + 异常状态 + 印记 + 天气 + 特殊效果 + 条件触发 + 伤害。
v2：类型化效果分派（dispatch），移除 regex 运行时解析。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from scripts.common import STAT_KEYS

if TYPE_CHECKING:
    from .sprite import Sprite, StatusEffect
    from .skill import Skill
    from .battleskill import BattleSkill, SkillUse
    from .globals import GlobalEffects
    from .effects import Effect


@dataclass
class TurnContext:
    """回合快照 — 技能执行时的战场事实。"""
    turn: int
    is_first: bool = False
    opponent_switched: bool = False
    opponent_gathered: bool = False


# 系别克制表（18 系）
_TYPE_CHART: dict[str, dict[str, float]] = {
    '火': {'草': 2.0, '冰': 2.0, '虫': 0.5, '水': 0.5, '石': 0.5},
    '水': {'火': 2.0, '石': 2.0, '地': 2.0, '草': 0.5, '电': 0.5},
    '草': {'水': 2.0, '地': 2.0, '火': 0.5, '虫': 0.5, '冰': 0.5, '毒': 0.5, '翼': 0.5},
    '电': {'水': 2.0, '翼': 2.0, '地': 0.5, '草': 0.5},
    '冰': {'草': 2.0, '地': 2.0, '翼': 2.0, '龙': 2.0, '火': 0.5, '冰': 0.5, '石': 0.5},
    '地': {'火': 2.0, '电': 2.0, '石': 2.0, '毒': 2.0, '草': 0.5, '水': 2.0, '冰': 0.5},
    '翼': {'草': 2.0, '虫': 2.0, '武': 2.0, '石': 0.5, '电': 0.5},
    '武': {'冰': 2.0, '石': 2.0, '普通': 2.0, '翼': 0.5, '幽': 0.5, '超': 0.5},
    '石': {'火': 2.0, '冰': 2.0, '翼': 2.0, '虫': 2.0, '水': 0.5, '草': 0.5, '武': 0.5, '地': 0.5},
    '毒': {'草': 2.0, '萌': 2.0, '地': 0.5, '石': 0.5, '幽': 0.5},
    '虫': {'草': 2.0, '超': 2.0, '恶': 2.0, '火': 0.5, '翼': 0.5, '毒': 0.5, '武': 0.5},
    '超': {'武': 2.0, '毒': 2.0, '虫': 0.5, '幽': 0.5, '恶': 0.5},
    '幽': {'超': 2.0, '幽': 2.0, '恶': 0.5, '普通': 0.5},
    '恶': {'超': 2.0, '幽': 2.0, '虫': 0.5, '武': 0.5, '萌': 0.5},
    '龙': {'龙': 2.0, '冰': 0.5, '萌': 0.5},
    '萌': {'龙': 2.0, '恶': 2.0, '武': 2.0, '毒': 0.5, '石': 0.5},
    '光': {'恶': 2.0, '幽': 2.0, '毒': 0.5, '电': 0.5},
    '普通': {'武': 0.5, '幽': 0.5},
}

# 属性显示名
_STAT_LABEL_REV: dict[str, str] = {
    'atk': '物攻', 'sp_atk': '魔攻', 'def': '物防', 'sp_def': '魔防',
    'speed': '速度', 'power': '威力', 'priority': '先手', 'energy_cost': '能耗',
}

# 步数换算
_STEP_UNIT: dict[str, int] = {
    'power': 10, 'priority': 1, 'energy_cost': 1,
}
_SPEED_STEP = 10
_STEP_PCT = 10

# 伤害相关的特殊效果名（由 calc_damage 的 modifiers 处理，dispatch 中跳过）
_DAMAGE_SPECIALS: frozenset[str] = frozenset({
    'power_bonus', 'power_mult', 'damage_mult', 'damage_reduction',
    'multi_hit',
})


class SkillResolver:
    """技能效果解析器（无状态，纯方法）。v2：类型化分派。"""

    # ═══════════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def dispatch(
        user: 'Sprite', target: 'Sprite', use: 'SkillUse',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None' = None,
        team: str = 'A',
    ) -> list[str]:
        """遍历 use.battle_skill.effects，按 kind 分派到对应 handler。"""
        events: list[str] = []
        for effect in use.battle_skill.effects:
            kind = effect.kind
            if kind == 'stat':
                events += SkillResolver._handle_stat(user, target, effect)
            elif kind == 'abnormal':
                events += SkillResolver._handle_abnormal(user, target, effect)
            elif kind == 'mark':
                events += SkillResolver._handle_mark(globals_, effect, team)
            elif kind == 'weather':
                events += SkillResolver._handle_weather(globals_, effect)
            elif kind == 'special':
                events += SkillResolver._handle_special(user, target, effect, globals_, ctx)
            elif kind == 'conditional':
                events += SkillResolver._eval_conditional(
                    user, target, effect, globals_, ctx, team,
                )
        return events

    # ── 效果分派 ──

    @staticmethod
    def _handle_stat(user: 'Sprite', target: 'Sprite', effect: 'Effect') -> list[str]:
        from .sprite import StatusEffect

        sprite = user if effect.target == 'self' else target
        step_unit = _STEP_UNIT.get(effect.stat, _STEP_PCT)
        raw_val = effect.steps * step_unit

        # 生成显示名
        label = _STAT_LABEL_REV.get(effect.stat, effect.stat)
        if effect.stat in ('power', 'priority', 'energy_cost', 'speed'):
            sign = '+' if effect.steps > 0 else ''
            display = f'{label}{sign}{raw_val}'
        else:
            sign = '+' if effect.steps > 0 else ''
            display = f'{label}{sign}{raw_val}%'

        se = StatusEffect(
            name=display, category='stat', stat_key=effect.stat,
            steps=effect.steps, scope=effect.scope, source='skill',
        )
        sprite.add_effect(se)
        return [f'{sprite.name} {display}']

    @staticmethod
    def _handle_abnormal(user: 'Sprite', target: 'Sprite', effect: 'Effect') -> list[str]:
        from .sprite import StatusEffect

        sprite = user if effect.target == 'self' else target
        stacks = getattr(effect, 'stacks', 1)
        se = StatusEffect(
            name=effect.name, category='abnormal',
            scope=effect.scope, source='skill', stacks=stacks,
        )
        sprite.add_effect(se)
        total = sprite.get_stacks(effect.name)
        return [f'{sprite.name} {effect.name}+{stacks}(共{total}层)']

    @staticmethod
    def _handle_mark(globals_: 'GlobalEffects', effect: 'Effect', team: str) -> list[str]:
        target_team = team if effect.target == 'own_team' else ('B' if team == 'A' else 'A')
        category = globals_.classify_mark(effect.name)
        globals_.apply_mark(target_team, effect.name, category, effect.stacks)
        return [f'{target_team}方 {effect.name}+{effect.stacks}']

    @staticmethod
    def _handle_weather(globals_: 'GlobalEffects', effect: 'Effect') -> list[str]:
        globals_.set_weather(effect.weather, effect.turns)
        return [f'天气→{effect.weather}({effect.turns}回合)']

    @staticmethod
    def _handle_special(
        user: 'Sprite', target: 'Sprite', effect: 'Effect',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None' = None,
    ) -> list[str]:
        events: list[str] = []

        # 伤害相关 → 由 calc_damage / modifiers 处理，此处跳过
        if effect.name in _DAMAGE_SPECIALS:
            return events

        if effect.name == 'burst':
            if user.first_action:
                events.append(f'{user.name} 迸发')
        elif effect.name == 'charge':
            events.append(f'{user.name} 蓄力')
        elif effect.name == 'escape':
            events.append(f'{user.name} 触发脱离/折返')
        elif effect.name == 'steal_energy':
            amount = effect.amount or 1
            stolen = target.lose_energy(amount)
            user.gain_energy(stolen)
            events.append(f'{user.name} 偷取{stolen}E')
        elif effect.name == 'life_drain':
            pct = effect.value / 100.0 if effect.value > 1 else effect.value
            # 吸血量在 battle 中基于实际伤害计算
            events.append(f'{user.name} 吸血{pct*100:.0f}%')
        elif effect.name == 'direct_heal':
            healed = user.heal(effect.amount or 0)
            if healed:
                events.append(f'{user.name} 回复+{healed}HP')
        elif effect.name == 'heal':
            pct = effect.value
            healed = user.heal(round(user.max_hp * pct))
            if healed:
                events.append(f'{user.name} 回复+{healed}HP')
        elif effect.name == 'reflect_damage':
            events.append(f'{user.name} 反射伤害')
        elif effect.name == 'priority_bonus':
            pass  # 已在 battle 中通过 effective_priority 处理

        return events

    # ── 条件求值 ──

    @staticmethod
    def _eval_conditional(
        user: 'Sprite', target: 'Sprite', effect: 'Effect',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None',
        team: str = 'A',
    ) -> list[str]:
        if SkillResolver._check_condition(effect.when, user, target, globals_, ctx):
            events: list[str] = []
            for sub in (effect.then or []):
                kind = sub.kind
                if kind == 'stat':
                    events += SkillResolver._handle_stat(user, target, sub)
                elif kind == 'abnormal':
                    events += SkillResolver._handle_abnormal(user, target, sub)
                elif kind == 'mark':
                    events += SkillResolver._handle_mark(globals_, sub, team)
                elif kind == 'weather':
                    events += SkillResolver._handle_weather(globals_, sub)
                elif kind == 'special':
                    events += SkillResolver._handle_special(user, target, sub, globals_, ctx)
                elif kind == 'conditional':
                    events += SkillResolver._eval_conditional(
                        user, target, sub, globals_, ctx, team,
                    )
            return events
        return []

    @staticmethod
    def _check_condition(
        cond: dict | None, user: 'Sprite', target: 'Sprite',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None',
    ) -> bool:
        """求值单个条件 dict。"""
        if not cond:
            return True

        kind = cond.get('kind', '')

        if kind == 'counter_succeeded':
            return True  # 调用方在 countered 时才遍历此 conditional

        if kind == 'is_first':
            return bool(ctx and ctx.is_first)

        if kind == 'opp_switched':
            return bool(ctx and ctx.opponent_switched)

        if kind == 'hp_below':
            ratio = cond.get('ratio', 0.5)
            hp_pct = user.current_hp / user.max_hp if user.max_hp else 0
            return hp_pct < ratio

        if kind == 'has_abnormal':
            name = cond.get('name', '')
            return user.get_stacks(name) > 0

        if kind == 'weather_is':
            weather = cond.get('weather', '')
            return globals_.weather == weather

        if kind == 'counter_ge':
            key = cond.get('key', '')
            value = cond.get('value', 0)
            return user.get_counter(key) >= value

        if kind == 'and':
            return all(
                SkillResolver._check_condition(c, user, target, globals_, ctx)
                for c in cond.get('conditions', [])
            )

        if kind == 'or':
            return any(
                SkillResolver._check_condition(c, user, target, globals_, ctx)
                for c in cond.get('conditions', [])
            )

        return True

    # ── 应对 ──

    @staticmethod
    def resolve_counter(atk_skill: 'Skill', def_skill: 'Skill') -> bool:
        """返回 def_skill 是否应对了 atk_skill。"""
        if def_skill.counter == '攻击' and atk_skill.is_attack:
            return True
        if def_skill.counter == '防御' and atk_skill.is_defense:
            return True
        if def_skill.counter == '状态' and atk_skill.is_status:
            return True
        return False

    # ── 伤害计算 ──

    @staticmethod
    def calc_damage(
        attacker: 'Sprite', defender: 'Sprite',
        use: 'SkillUse', globals_: 'GlobalEffects',
        attacker_team: str = 'A',
    ) -> tuple[int, list[str]]:
        events: list[str] = []
        bs = use.battle_skill

        keys = bs.get_atk_def_keys(attacker)
        if not keys:
            return 0, events

        atk_key, def_key = keys
        atk_val = attacker.effective_stat(atk_key)
        def_val = defender.effective_stat(def_key)

        # 威力修正：技能基础 + 精灵威力步数 + 印记 + SkillUse modifiers
        effective_power = (
            bs.power
            + attacker.power_mod * 10
            + globals_.mark_power_bonus(attacker_team, bs)
            + use.modifiers.get('power_bonus', 0)
        )
        effective_power = round(effective_power * use.power_mult)
        if effective_power <= 0:
            return 0, events

        if def_val == 0:
            return 0, events

        base = round((37 / 41) * effective_power * atk_val / def_val)
        type_mult = SkillResolver._get_type_mult(bs, attacker, defender)
        weather_mult = globals_.weather_damage_mult(bs.element or '')
        mark_mult = globals_.mark_damage_mult(attacker_team, use.is_first)
        stab_mult = SkillResolver._get_stab(bs, attacker)

        burst_mult = 1.5 if ('burst' in [e.name for e in bs.effects if getattr(e, 'kind', '') == 'special'] and attacker.first_action) else 1.0

        damage = round(base * type_mult * weather_mult * mark_mult * stab_mult * burst_mult * use.damage_mult * use.multi_hit)
        damage = round(damage * (1.0 - use.damage_reduction))
        damage = max(1, damage)

        return damage, events

    @staticmethod
    def _get_type_mult(skill: 'Skill', attacker: 'Sprite', defender: 'Sprite') -> float:
        elem = skill.element
        if not elem:
            return 1.0
        def_elem = (defender.species.attributes or '').split('/')[0]
        if not def_elem:
            return 1.0
        chart = _TYPE_CHART.get(elem, {})
        return chart.get(def_elem, 1.0)

    @staticmethod
    def _get_stab(skill: 'Skill', attacker: 'Sprite') -> float:
        elem = skill.element
        if not elem:
            return 1.0
        attrs = attacker.species.attributes or ''
        if elem in attrs:
            return 1.5
        return 1.0

    # ═══════════════════════════════════════════════════════════════
    # 回合结束结算
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def turn_end(
        sprites: dict[str, 'Sprite'], globals_: 'GlobalEffects',
    ) -> list[str]:
        """回合末：中毒/灼烧/冻结/寄生 + 冷却递减 + 印记 + 天气递减。"""
        from .sprite import StatusEffect

        events: list[str] = []
        all_sprites = list(sprites.values())

        for s in all_sprites:
            if s.is_fainted:
                continue

            # 中毒（3% 最大HP/层）
            poison_stacks = s.get_stacks('中毒')
            if poison_stacks > 0:
                dmg = max(1, round(s.max_hp * 0.03 * poison_stacks))
                s.take_damage(dmg)
                events.append(f'{s.name} 中毒-{dmg}HP')

            # 灼烧（2% 最大HP/层，层数减半向上取整）
            burn_stacks = s.get_stacks('灼烧')
            if burn_stacks > 0:
                dmg = max(1, round(s.max_hp * 0.02 * burn_stacks))
                s.take_damage(dmg)
                new_stacks = (burn_stacks + 1) // 2
                s.update_stacks('灼烧', new_stacks)
                events.append(f'{s.name} 灼烧-{dmg}HP(剩{new_stacks}层)')

            # 寄生（6% 最大HP）
            if s.get_stacks('寄生') > 0:
                dmg = max(1, round(s.max_hp * 0.06))
                s.take_damage(dmg)
                events.append(f'{s.name} 寄生-{dmg}HP')

            # 冷却递减
            for bs in s.skills:
                if bs.cooldown > 0:
                    bs.cooldown -= 1

        events += globals_.weather_turn_effects(all_sprites)
        globals_.tick_weather()
        events += globals_.mark_turn_end_effects(sprites)

        return events
