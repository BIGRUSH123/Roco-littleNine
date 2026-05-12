"""scripts/sim/resolver.py — 技能效果解析器

处理技能全效果：六维变化 + 异常状态 + 印记 + 天气 + 特殊效果 + 条件触发 + 伤害。
v2：类型化效果分派（dispatch），移除 regex 运行时解析。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from scripts.common import STAT_KEYS
from .effects import SpecialName, EffectLayer

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
    countered_skill: 'BattleSkill | None' = None


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
    'combo': '连击', 'life_drain': '吸血', 'combo_mult': '连击倍率',
}

# 步数换算
_STEP_UNIT: dict[str, int] = {
    'power': 10, 'priority': 1, 'energy_cost': 1, 'combo': 1,
    'life_drain': 10, 'combo_mult': 100,
}
_SPEED_STEP = 10
_STEP_PCT = 10

_DAMAGE_SPECIALS = SpecialName.DAMAGE_SPECIALS

# Special 效果 → 管线层映射。L0/L4/L5 效果在各层独立方法中处理；L3 效果由 dispatch_L3 统一分派。
_SPECIAL_LAYER: dict[str, int] = {
    # L0: modifier 注入（attack → SkillUse.modifiers；non-attack → BattleSkill 状态）
    SpecialName.POWER_BONUS: EffectLayer.MODIFIER,
    SpecialName.POWER_MULT: EffectLayer.MODIFIER,
    SpecialName.DAMAGE_MULT: EffectLayer.MODIFIER,
    SpecialName.DAMAGE_REDUCTION: EffectLayer.MODIFIER,
    SpecialName.MULTI_HIT: EffectLayer.MODIFIER,
    SpecialName.IGNORE_MODS: EffectLayer.MODIFIER,
    SpecialName.ADJACENT_POWER_BONUS: EffectLayer.MODIFIER,

    # L1: 动态威力（battle.py 内联处理）
    SpecialName.POWER_BY_ENEMY_ENERGY: EffectLayer.POWER,
    SpecialName.POWER_BY_ADJACENT: EffectLayer.POWER,

    # L2: 伤害 per-hit（burst 在 calc_damage 消费，life_drain 在连击循环消费）
    SpecialName.BURST: EffectLayer.DAMAGE,
    SpecialName.LIFE_DRAIN: EffectLayer.DAMAGE,

    # L3: 状态变更（dispatch_L3 分派）
    SpecialName.HEAL: EffectLayer.STATE,
    SpecialName.DIRECT_HEAL: EffectLayer.STATE,
    SpecialName.GAIN_ENERGY: EffectLayer.STATE,
    SpecialName.STEAL_ENERGY: EffectLayer.STATE,
    SpecialName.GAIN_ENERGY_BY_ENEMY: EffectLayer.STATE,
    SpecialName.CHARGE: EffectLayer.STATE,
    SpecialName.DISPEL_POSITIVE: EffectLayer.STATE,
    SpecialName.DISPEL_NEGATIVE: EffectLayer.STATE,
    SpecialName.DOUBLE_POSITIVE: EffectLayer.STATE,
    SpecialName.DOUBLE_NEGATIVE: EffectLayer.STATE,
    SpecialName.REFLECT_DAMAGE: EffectLayer.STATE,
    SpecialName.INTERRUPT: EffectLayer.STATE,
    SpecialName.EXCHANGE_HP_RATIO: EffectLayer.STATE,
    SpecialName.EXCHANGE_EFFECTS: EffectLayer.STATE,
    SpecialName.EXCHANGE_SKILLS: EffectLayer.STATE,
    SpecialName.RANDOM_DEVOTION: EffectLayer.STATE,
    SpecialName.PRIORITY_BONUS: EffectLayer.STATE,
    SpecialName.COMBO_INCREMENT: EffectLayer.POST_USE,
    SpecialName.POWER_INCREMENT: EffectLayer.POST_USE,
    SpecialName.ENERGY_COST_INCREMENT: EffectLayer.POST_USE,

    # L4: 反击伤害（resolve_counter_damage 独立公式）
    SpecialName.COUNTER_DAMAGE: EffectLayer.COUNTER,

    # L5: 换宠/返场（battle.py 后处理）
    SpecialName.ESCAPE: EffectLayer.SWITCH,
    SpecialName.ESCAPE_INHERIT: EffectLayer.SWITCH,
    SpecialName.FORCE_RETURN: EffectLayer.SWITCH,
    SpecialName.RETURN_SELF: EffectLayer.SWITCH,
    SpecialName.BORROW_SKILL: EffectLayer.SWITCH,
}

# 奉献池（虫系特有机制）：每次随机奉献从中选一
_DEVOTION_POOL: list[dict] = [
    {'kind': 'stat', 'target': 'self', 'stat': 'power', 'steps': 2, 'scope': 'battlefield'},
    {'kind': 'special', 'name': SpecialName.LIFE_DRAIN, 'value': 0.1, 'target': 'self'},
    {'kind': 'stat', 'target': 'self', 'stat': 'combo', 'steps': 1, 'scope': 'battlefield'},
    {'kind': 'abnormal', 'target': 'opp', 'name': '中毒', 'stacks': 2},
]

# Special 效果 handler 签名
# 每个 handler 接收 (user, target, effect, globals, ctx, use) → list[str]
from typing import Callable
_SpecialHandler = Callable[
    ['Sprite', 'Sprite', 'SpecialEffect', 'GlobalEffects',
     'TurnContext | None', 'SkillUse | None'],
    list[str],
]

# ── Special 效果处理器注册表 ──
# 新增特殊效果：在此添加映射 + 写 handler 函数即可，dispatch 逻辑不动。
_SPECIAL_HANDLERS: dict[str, _SpecialHandler] = {}


class SkillResolver:
    """技能效果解析器（无状态，纯方法）。v2：类型化分派。"""

    # ═══════════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # L0: modifier 预计算（需要 sprite 上下文的部分）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def dispatch_modifiers(user: 'Sprite', use: 'SkillUse') -> list[str]:
        """L0: 处理需要 sprite 上下文的 Modifier 层 special 效果。

        DAMAGE_SPECIALS（攻击技能）→ 已在 SkillUse._collect_modifiers 中处理。
        DAMAGE_SPECIALS（非攻击技能）→ 转为 BattleSkill 状态（next_attack_mult / power_mod）。
        adjacent_power_bonus → 相邻技能 power_mod。
        """
        events: list[str] = []
        for effect in use.battle_skill.effects:
            if getattr(effect, 'kind', '') != 'special':
                continue
            if _SPECIAL_LAYER.get(effect.name) != EffectLayer.MODIFIER:
                continue
            is_attack = use.battle_skill.is_attack
            if effect.name in _DAMAGE_SPECIALS:
                if is_attack:
                    continue  # 已在 use.modifiers 中
                events += SkillResolver._handle_non_attack_damage_special(user, effect, use)
            else:
                handler = _SPECIAL_HANDLERS.get(effect.name)
                if handler:
                    events += handler(user, None, effect, None, None, use)
        return events

    # ═══════════════════════════════════════════════════════════════
    # L3: 状态变更分派
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def dispatch_L3(
        user: 'Sprite', target: 'Sprite', use: 'SkillUse',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None' = None,
        team: str = 'A',
    ) -> list[str]:
        """L3: 状态变更层。遍历 effects 数组顺序执行，跳过 L4/L5 层 special 效果。"""
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
                layer = _SPECIAL_LAYER.get(effect.name, EffectLayer.STATE)
                if layer >= EffectLayer.COUNTER:
                    continue  # L4/L5 效果由各自层方法处理
                events += SkillResolver._handle_special(user, target, effect, globals_, ctx, use)
            elif kind == 'conditional':
                events += SkillResolver._eval_conditional(
                    user, target, effect, globals_, ctx, team, use,
                )
        return events

    @staticmethod
    def dispatch(
        user: 'Sprite', target: 'Sprite', use: 'SkillUse',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None' = None,
        team: str = 'A',
    ) -> list[str]:
        """[兼容] 完整效果分派。等同于 dispatch_L3。"""
        return SkillResolver.dispatch_L3(user, target, use, globals_, ctx, team)

    @staticmethod
    def dispatch_post_use(
        user: 'Sprite', target: 'Sprite', use: 'SkillUse',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None' = None,
    ) -> list[str]:
        """技能使用后永久增长（连击/威力/能耗递增）。在 L3+per-hit 循环之后调用。"""
        events: list[str] = []
        for effect in use.battle_skill.effects:
            if getattr(effect, 'kind', '') != 'special':
                continue
            if _SPECIAL_LAYER.get(effect.name) != EffectLayer.POST_USE:
                continue
            events += SkillResolver._handle_special(user, target, effect, globals_, ctx, use)
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
        if effect.stat in ('power', 'priority', 'energy_cost', 'speed', 'combo'):
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
        use: 'SkillUse | None' = None,
    ) -> list[str]:
        """L3 special 效果分派。L0/L4/L5 效果由各自层方法处理，此处跳过。"""
        if effect.name in _DAMAGE_SPECIALS:
            return []  # L0 — 已在 dispatch_modifiers 中处理
        handler = _SPECIAL_HANDLERS.get(effect.name)
        if handler is not None:
            return handler(user, target, effect, globals_, ctx, use)
        return []

    @staticmethod
    def _handle_non_attack_damage_special(
        user: 'Sprite', effect: 'Effect', use: 'SkillUse | None',
    ) -> list[str]:
        """非攻击技能的 damage specials → 转为 BattleSkill 状态变更。

        与攻击路径的 SkillUse._collect_modifiers 对称：
        - power_mult  → next_attack_mult（热身）
        - power_bonus → 相邻技能 power_mod（联动装置）
        """
        events: list[str] = []
        if effect.name == SpecialName.POWER_MULT:
            val = getattr(effect, 'value', 1.0) or 1.0
            for bs in user.skills:
                bs.next_attack_mult = max(bs.next_attack_mult, val)
            events.append(f'{user.name} 下次攻击威力×{val}')
        elif effect.name == SpecialName.POWER_BONUS:
            val = int(getattr(effect, 'value', 0) or getattr(effect, 'amount', 0))
            if use and use.skill_index >= 0:
                for offset in (-1, 1):
                    idx = use.skill_index + offset
                    if 0 <= idx < len(user.skills):
                        user.skills[idx].power_mod += val
                        events.append(f'{user.skills[idx].name} 威力永久+{val}')
        return events

    # ═══════════════════════════════════════════════════════════════
    # L4: 反击伤害（独立简化公式，不走 L2 calc_damage）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def resolve_counter_damage(
        user: 'Sprite', target: 'Sprite', use: 'SkillUse',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None' = None,
    ) -> list[str]:
        """L4: 反击伤害。使用独立简化公式（power × atk/def × type），
        不含 STAB、天气、印记、burst 倍率。
        始终检查原始技能（.base）的效果，而非可能被 reflect_damage 替换后的 skill。"""
        events: list[str] = []
        for effect in use.battle_skill.base.effects:
            if getattr(effect, 'kind', '') != 'special':
                continue
            if effect.name != SpecialName.COUNTER_DAMAGE:
                continue
            handler = _SPECIAL_HANDLERS.get(SpecialName.COUNTER_DAMAGE)
            if handler:
                events += handler(user, target, effect, globals_, ctx, use)
        return events

    # ── Special 效果处理器（链式注册）──
    # 每个 handler 签名与 _SpecialHandler 一致。
    # 新增效果：写一个 handler，在 _build_special_registry() 注册即可。

    @staticmethod
    def _special_burst(user, _target, _effect, _g, _ctx, _use):
        if user.first_action:
            return [f'{user.name} 迸发']
        return []

    @staticmethod
    def _special_charge(user, _target, _effect, _g, _ctx, _use):
        if getattr(user, '_charging', False):
            # 蓄力释放：清空蓄力状态
            user._charging = False
            user._charged_skill_index = -1
            return [f'{user.name} 蓄力释放']
        else:
            # 进入蓄力
            user._charging = True
            user._charged_skill_index = _use.skill_index if _use else -1
            return [f'{user.name} 蓄力']

    @staticmethod
    def _special_escape(user, _target, _effect, _g, _ctx, _use):
        return [f'{user.name} 触发脱离/折返']

    @staticmethod
    def _special_steal_energy(user, target, effect, _g, _ctx, _use):
        amount = effect.amount or 1
        stolen = target.lose_energy(amount)
        user.gain_energy(stolen)
        return [f'{user.name} 偷取{stolen}E']

    @staticmethod
    def _special_life_drain(user, _target, effect, _g, _ctx, _use):
        pct = effect.value / 100.0 if effect.value > 1 else effect.value
        return [f'{user.name} 吸血{pct*100:.0f}%']

    @staticmethod
    def _special_direct_heal(user, _target, effect, _g, _ctx, _use):
        healed = user.heal(effect.amount or 0)
        if healed:
            return [f'{user.name} 回复+{healed}HP']
        return []

    @staticmethod
    def _special_gain_energy(user, _target, effect, _g, _ctx, _use):
        gained = user.gain_energy(effect.amount or 0)
        if gained:
            return [f'{user.name} 回复+{gained}E']
        return []

    @staticmethod
    def _special_gain_energy_by_enemy(user, target, effect, _g, _ctx, _use):
        total_e = sum(bs.energy_cost for bs in target.skills)
        amount = max(1, int(total_e * (effect.value or 0.5)))
        gained = user.gain_energy(amount)
        if gained:
            return [f'{user.name} 回复+{gained}E(敌总耗{total_e})']
        return []

    @staticmethod
    def _special_heal(user, _target, effect, _g, _ctx, _use):
        pct = effect.value
        healed = user.heal(round(user.max_hp * pct))
        if healed:
            return [f'{user.name} 回复+{healed}HP']
        return []

    @staticmethod
    def _special_combo_increment(user, _target, effect, _g, _ctx, use):
        amount = int(effect.amount or effect.value or 1)
        use.battle_skill.combo_mod += amount
        return [f'{user.name} {use.battle_skill.name}连击+{amount}(→{use.battle_skill.combo})']

    @staticmethod
    def _special_power_increment(user, _target, effect, _g, _ctx, use):
        amount = int(effect.amount or effect.value or 0)
        use.battle_skill.power_mod += amount
        return [f'{user.name} {use.battle_skill.name}威力+{amount}(→{use.battle_skill.power})']

    @staticmethod
    def _special_energy_cost_increment(user, _target, effect, _g, _ctx, use):
        amount = int(effect.amount or effect.value or 0)
        use.battle_skill.energy_cost_mod += amount
        sign = '+' if amount >= 0 else ''
        return [f'{user.name} {use.battle_skill.name}能耗{sign}{amount}(→{use.battle_skill.energy_cost})']

    @staticmethod
    def _special_dispel_positive(user, target, effect, _g, _ctx, _use):
        sprite = user if getattr(effect, 'target', 'opp') == 'self' else target
        n = sprite.dispel_positive(effect.amount or -1)
        if n:
            return [f'{sprite.name} 驱散{n}个正面效果']
        return []

    @staticmethod
    def _special_dispel_negative(user, target, effect, _g, _ctx, _use):
        sprite = user if getattr(effect, 'target', 'opp') == 'self' else target
        n = sprite.dispel_negative(effect.amount or -1)
        if n:
            return [f'{sprite.name} 驱散{n}个负面效果']
        return []

    @staticmethod
    def _special_double_positive(user, target, effect, _g, _ctx, _use):
        sprite = user if getattr(effect, 'target', 'opp') == 'self' else target
        n = sprite.double_positive()
        if n:
            return [f'{sprite.name} {n}个正面效果翻倍']
        return []

    @staticmethod
    def _special_double_negative(user, target, effect, _g, _ctx, _use):
        sprite = user if getattr(effect, 'target', 'opp') == 'self' else target
        n = sprite.double_negative()
        if n:
            return [f'{sprite.name} {n}个负面效果翻倍']
        return []

    @staticmethod
    def _special_reflect_damage(user, _target, _effect, _g, _ctx, use):
        if use and use.countered_skill:
            use.battle_skill.replaced_by = use.countered_skill.base
            return [f'{user.name} {use.battle_skill.name}→{use.countered_skill.name}']
        return [f'{user.name} 反射伤害']

    @staticmethod
    def _special_adjacent_power_bonus(user, _target, effect, _g, _ctx, use):
        events: list[str] = []
        val = int(getattr(effect, 'value', 0) or getattr(effect, 'amount', 0))
        if use and use.skill_index >= 0:
            for offset in (-1, 1):
                idx = use.skill_index + offset
                if 0 <= idx < len(user.skills):
                    user.skills[idx].power_mod += val
                    events.append(f'{user.skills[idx].name} 威力永久+{val}')
        return events

    @staticmethod
    def _special_priority_bonus(_user, _target, _effect, _g, _ctx, _use):
        return []  # 由 battle._effective_priority 处理

    @staticmethod
    def _special_interrupt(user, target, _effect, _g, _ctx, use):
        if use and use.countered_skill:
            use.countered_skill.nullified = True
            return [f'{user.name} 打断 {target.name} 的技能']
        return [f'{user.name} 打断']

    @staticmethod
    def _special_counter_damage(user, target, effect, _g, ctx, use):
        if not (ctx and ctx.countered_skill):
            return []
        power = int(effect.value)
        atk_val = user.effective_stat('atk')
        def_val = target.effective_stat('def')
        base = round((37 / 41) * power * atk_val / def_val) if def_val > 0 else 0
        if base <= 0:
            return []
        elem = use.battle_skill.element if use else ''
        def_elem = (target.species.attributes or '').split('/')[0] if target.species else ''
        type_mult = _TYPE_CHART.get(elem, {}).get(def_elem, 1.0)
        damage = max(1, round(base * type_mult))
        target.take_damage(damage)
        return [f'{user.name} 反击 {target.name} -{damage}HP']

    @staticmethod
    def _special_exchange_hp_ratio(user, target, _effect, _g, _ctx, _use):
        if user.max_hp > 0 and target.max_hp > 0:
            ur = user.current_hp / user.max_hp
            tr = target.current_hp / target.max_hp
            user.current_hp = max(1, round(tr * user.max_hp))
            target.current_hp = max(1, round(ur * target.max_hp))
            return [f'{user.name} 与 {target.name} 交换了生命比例']
        return []

    @staticmethod
    def _special_exchange_effects(user, target, _effect, _g, _ctx, _use):
        user.effects, target.effects = target.effects, user.effects
        return [f'{user.name} 与 {target.name} 交换了增益和减益']

    @staticmethod
    def _special_exchange_skills(user, target, _effect, _g, _ctx, _use):
        user.skills, target.skills = target.skills, user.skills
        return [f'{user.name} 与 {target.name} 交换了技能']

    @staticmethod
    def _special_escape_inherit(user, _target, _effect, _g, _ctx, _use):
        return [f'{user.name} 脱离(继承增益)']

    @staticmethod
    def _special_force_return(_user, _target, _effect, _g, _ctx, _use):
        return []  # 由 battle.py 在 dispatch 后处理

    @staticmethod
    def _special_return_self(user, _target, _effect, _g, _ctx, _use):
        user.pending_return = True
        return [f'{user.name} 蓄力返场']

    @staticmethod
    def _special_ignore_mods(_user, _target, _effect, _g, _ctx, _use):
        return []  # 由 SkillUse._collect_modifiers 注入

    @staticmethod
    def _special_random_devotion(user, target, effect, _g, _ctx, _use):
        import random
        from .effects import effect_from_dict
        events: list[str] = []
        amount = getattr(effect, 'amount', 1)
        for _ in range(amount):
            pick = random.choice(_DEVOTION_POOL)
            sub = effect_from_dict(pick)
            kind = sub.kind
            if kind == 'stat':
                events += SkillResolver._handle_stat(user, target, sub)
            elif kind == 'abnormal':
                events += SkillResolver._handle_abnormal(user, target, sub)
        events.append(f'{user.name} 随机奉献×{amount}')
        return events

    @staticmethod
    def _special_borrow_skill(_user, _target, _effect, _g, _ctx, _use):
        return []  # 由 battle.py 在回合开始阶段处理

    # ── 条件求值 ──

    @staticmethod
    def _eval_conditional(
        user: 'Sprite', target: 'Sprite', effect: 'Effect',
        globals_: 'GlobalEffects', ctx: 'TurnContext | None',
        team: str = 'A',
        use: 'SkillUse | None' = None,
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
                    events += SkillResolver._handle_special(user, target, sub, globals_, ctx, use)
                elif kind == 'conditional':
                    events += SkillResolver._eval_conditional(
                        user, target, sub, globals_, ctx, team, use,
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
            return ctx is not None and ctx.countered_skill is not None

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
        """伤害公式 (v2):
        伤害 = 39/41 * 基础攻击/基础防御 * (技能威力*应对加成+固定威力) * 百分比威力
               * 本系加成(125%) * 克制关系 * 天气影响 * (1-减伤) * (1+攻方修正-防方修正)
        """
        events: list[str] = []
        bs = use.battle_skill

        keys = bs.get_atk_def_keys(attacker)
        if not keys:
            return 0, events

        atk_key, def_key = keys
        ignore_mods = use.modifiers.get('ignore_mods', False)

        # 基础攻击/防御（不含 stat stage）
        atk_base = attacker.initial_stats.get(atk_key, 0)
        def_base = defender.initial_stats.get(def_key, 0)
        if atk_base <= 0 or def_base <= 0:
            return 0, events

        # stat stage 百分比修正（1步 = 10%）
        atk_steps = attacker._sum_steps(atk_key)
        def_steps = defender._sum_steps(def_key)
        if ignore_mods:
            atk_steps = max(0, atk_steps)
            def_steps = min(0, def_steps)
        atk_stage = atk_steps / _STEP_PCT
        def_stage = def_steps / _STEP_PCT

        # 迸发
        burst_mult = 1.5 if (
            SpecialName.BURST in [e.name for e in bs.effects if getattr(e, 'kind', '') == 'special']
            and attacker.first_action
        ) else 1.0

        # 应对百分比加成 = 迸发倍率 * 技能伤害倍率
        counter_mult = burst_mult * use.damage_mult
        additive_power = (
            attacker.power_mod * 10
            + globals_.mark_power_bonus(attacker_team, bs)
            + use.modifiers.get('power_bonus', 0)
        )
        power_term = round((bs.power * counter_mult + additive_power) * use.power_mult)
        if power_term <= 0:
            return 0, events

        # 克制关系
        type_mult = SkillResolver._get_type_mult(bs, attacker, defender)
        use.modifiers['type_mult'] = type_mult  # 供 trait 读取
        weather_mult = globals_.weather_damage_mult(bs.element or '')

        # 印记加成归入 stat stage 项
        mark_mult = globals_.mark_damage_mult(attacker_team, use.is_first)
        mark_bonus = mark_mult - 1.0

        # 本系加成 125%
        stab_mult = SkillResolver._get_stab(bs, attacker)

        # 核心公式
        core = (37 / 41) * atk_base / def_base * power_term
        core *= stab_mult * type_mult * weather_mult * (1.0 - use.damage_reduction)
        core *= (1.0 + atk_stage - def_stage + mark_bonus)
        core *= use.multi_hit

        damage = max(1, round(core))
        return damage, events

    @staticmethod
    def resolve_starfall(user: 'Sprite', target: 'Sprite', skill: 'BattleSkill',
                         globals_: 'GlobalEffects', defender_team: str) -> tuple[int, list[str]]:
        """星陨结算：非幻系攻击技能消耗星陨印记，造成额外幻系伤害。
        伤害公式：y = x² + 24x - 24，其中 x = 消耗前层数。
        返回 (额外伤害, 事件列表)。"""
        events: list[str] = []
        if not skill.is_attack or skill.element == '幻':
            return 0, events

        _, neg = globals_.get_marks(defender_team)
        starfall = next((m for m in neg if m.name == '星陨印记'), None)
        if not starfall or starfall.stacks <= 0:
            return 0, events

        x = starfall.stacks
        consumed = globals_.consume_starfall_stacks(defender_team, x, user)
        if consumed <= 0:
            return 0, events

        # 用原始层数 x 计算伤害（守望星消耗一半但按全额算）
        dmg = max(1, x * x + 24 * x - 24)
        actual = target.take_damage(dmg)
        events.append(f'{target.name} 星陨爆发 -{actual}HP(消耗{consumed}层)')
        return actual, events

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
            return 1.25
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


# ═══════════════════════════════════════════════════════════════
# 注册 Special 效果 handlers（模块加载时执行一次）
# ═══════════════════════════════════════════════════════════════

def _build_special_registry() -> dict[str, _SpecialHandler]:
    """构建 SpecialName → handler 的映射表。

    新增特殊效果：在此函数添加一行映射 + 在 SkillResolver 中写 handler 方法。
    """
    R = SkillResolver
    return {
        SpecialName.BURST:              R._special_burst,
        SpecialName.CHARGE:             R._special_charge,
        SpecialName.ESCAPE:             R._special_escape,
        SpecialName.STEAL_ENERGY:       R._special_steal_energy,
        SpecialName.LIFE_DRAIN:         R._special_life_drain,
        SpecialName.DIRECT_HEAL:        R._special_direct_heal,
        SpecialName.GAIN_ENERGY:        R._special_gain_energy,
        SpecialName.GAIN_ENERGY_BY_ENEMY: R._special_gain_energy_by_enemy,
        SpecialName.HEAL:               R._special_heal,
        SpecialName.COMBO_INCREMENT:    R._special_combo_increment,
        SpecialName.POWER_INCREMENT:    R._special_power_increment,
        SpecialName.ENERGY_COST_INCREMENT: R._special_energy_cost_increment,
        SpecialName.DISPEL_POSITIVE:    R._special_dispel_positive,
        SpecialName.DISPEL_NEGATIVE:    R._special_dispel_negative,
        SpecialName.DOUBLE_POSITIVE:    R._special_double_positive,
        SpecialName.DOUBLE_NEGATIVE:    R._special_double_negative,
        SpecialName.REFLECT_DAMAGE:     R._special_reflect_damage,
        SpecialName.ADJACENT_POWER_BONUS: R._special_adjacent_power_bonus,
        SpecialName.PRIORITY_BONUS:     R._special_priority_bonus,
        SpecialName.INTERRUPT:          R._special_interrupt,
        SpecialName.COUNTER_DAMAGE:     R._special_counter_damage,
        SpecialName.EXCHANGE_HP_RATIO:  R._special_exchange_hp_ratio,
        SpecialName.EXCHANGE_EFFECTS:   R._special_exchange_effects,
        SpecialName.EXCHANGE_SKILLS:    R._special_exchange_skills,
        SpecialName.ESCAPE_INHERIT:     R._special_escape_inherit,
        SpecialName.FORCE_RETURN:       R._special_force_return,
        SpecialName.RETURN_SELF:        R._special_return_self,
        SpecialName.IGNORE_MODS:        R._special_ignore_mods,
        SpecialName.RANDOM_DEVOTION:    R._special_random_devotion,
        SpecialName.BORROW_SKILL:       R._special_borrow_skill,
    }


_SPECIAL_HANDLERS.update(_build_special_registry())
