"""scripts/sim/effects.py — 类型化技能效果原语

定义 6 种效果类型，替代旧的 regex 字符串标签。
"""

from __future__ import annotations
from dataclasses import dataclass, field


class SpecialName:
    """SpecialEffect.name 合法值集合。新增特殊效果只需在此添加。"""

    # ── 伤害相关（攻击技能，由 _collect_modifiers / calc_damage 处理）──
    POWER_BONUS = 'power_bonus'
    POWER_MULT = 'power_mult'
    DAMAGE_MULT = 'damage_mult'
    DAMAGE_REDUCTION = 'damage_reduction'
    MULTI_HIT = 'multi_hit'

    # ── 治疗 / 能量 ──
    HEAL = 'heal'
    DIRECT_HEAL = 'direct_heal'
    GAIN_ENERGY = 'gain_energy'
    STEAL_ENERGY = 'steal_energy'
    LIFE_DRAIN = 'life_drain'
    GAIN_ENERGY_BY_ENEMY = 'gain_energy_by_enemy'

    # ── 技能永久成长 ──
    COMBO_INCREMENT = 'combo_increment'
    POWER_INCREMENT = 'power_increment'
    ENERGY_COST_INCREMENT = 'energy_cost_increment'

    # ── 控制 ──
    BURST = 'burst'
    FIRST_ACTION = 'first_action'
    CHARGE = 'charge'
    INTERRUPT = 'interrupt'
    REFLECT_DAMAGE = 'reflect_damage'
    COUNTER_DAMAGE = 'counter_damage'

    # ── 场地 / 返场 ──
    ESCAPE = 'escape'
    ESCAPE_INHERIT = 'escape_inherit'
    FORCE_RETURN = 'force_return'
    RETURN_SELF = 'return_self'

    # ── 驱散 / 加倍 ──
    DISPEL_POSITIVE = 'dispel_positive'
    DISPEL_NEGATIVE = 'dispel_negative'
    DISPEL_MARK = 'dispel_mark'
    DOUBLE_POSITIVE = 'double_positive'
    DOUBLE_NEGATIVE = 'double_negative'
    DOUBLE_ABNORMAL = 'double_abnormal'
    ABNORMAL_TICK = 'abnormal_tick'
    DAMAGE_REDUCTION_BY_ABNORMAL = 'damage_reduction_by_abnormal'
    POWER_BY_ABNORMAL = 'power_by_abnormal'

    # ── 交换 ──
    EXCHANGE_HP_RATIO = 'exchange_hp_ratio'
    EXCHANGE_EFFECTS = 'exchange_effects'
    EXCHANGE_SKILLS = 'exchange_skills'

    # ── 萌化相关 ──
    TRANSFER_MOE = 'transfer_moe'
    COMBO_BY_MOE = 'combo_by_moe'

    # ── 动态缩放 ──
    STAT_BY_ABNORMAL = 'stat_by_abnormal'

    # ── 特殊计算 ──
    POWER_BY_ENEMY_ENERGY = 'power_by_enemy_energy'
    POWER_BY_ADJACENT = 'power_by_adjacent'
    ADJACENT_POWER_BONUS = 'adjacent_power_bonus'
    PRIORITY_BONUS = 'priority_bonus'
    IGNORE_MODS = 'ignore_mods'
    RANDOM_DEVOTION = 'random_devotion'
    BORROW_SKILL = 'borrow_skill'
    DEFENSE_COOLDOWN_REDUCE = 'defense_cooldown_reduce'

    # ── 聚合 ──
    ALL: frozenset[str] = frozenset({
        POWER_BONUS, POWER_MULT, DAMAGE_MULT, DAMAGE_REDUCTION, MULTI_HIT,
        HEAL, DIRECT_HEAL, GAIN_ENERGY, STEAL_ENERGY, LIFE_DRAIN, GAIN_ENERGY_BY_ENEMY,
        BURST, FIRST_ACTION, CHARGE, INTERRUPT, REFLECT_DAMAGE, COUNTER_DAMAGE,
        ESCAPE, ESCAPE_INHERIT, FORCE_RETURN, RETURN_SELF,
        DISPEL_POSITIVE, DISPEL_NEGATIVE, DISPEL_MARK, DOUBLE_POSITIVE, DOUBLE_NEGATIVE,
        DOUBLE_ABNORMAL, ABNORMAL_TICK, DAMAGE_REDUCTION_BY_ABNORMAL, POWER_BY_ABNORMAL,
        EXCHANGE_HP_RATIO, EXCHANGE_EFFECTS, EXCHANGE_SKILLS,
        TRANSFER_MOE, COMBO_BY_MOE, STAT_BY_ABNORMAL,
        POWER_BY_ENEMY_ENERGY, POWER_BY_ADJACENT, ADJACENT_POWER_BONUS,
        PRIORITY_BONUS, IGNORE_MODS, RANDOM_DEVOTION, BORROW_SKILL,
        COMBO_INCREMENT, POWER_INCREMENT, ENERGY_COST_INCREMENT,
        DEFENSE_COOLDOWN_REDUCE,
    })

    # 伤害相关子集 — 由 _collect_modifiers / calc_damage 处理
    DAMAGE_SPECIALS: frozenset[str] = frozenset({
        POWER_BONUS, POWER_MULT, DAMAGE_MULT, DAMAGE_REDUCTION, MULTI_HIT,
    })


class EffectLayer:
    """技能效果管线分层。值即执行顺序。"""

    MODIFIER = 0   # L0: 威力/伤害修正注入 → SkillUse.modifiers
    POWER = 1      # L1: 动态威力解算 → power_override
    DAMAGE = 2     # L2: per-hit 伤害 + 吸血
    STATE = 3      # L3: 状态变更（资源/状态/交换），按 effects 数组顺序
    COUNTER = 4    # L4: 反击伤害（独立公式）
    SWITCH = 5     # L5: 换宠/返场/借用
    TURN_END = 6   # L6: 回合末结算
    POST_USE = 7   # L3.5: 技能使用后永久增长（连击/威力/能耗递增）


@dataclass
class StatEffect:
    """持久属性变化。"""
    kind: str = "stat"
    target: str = "self"        # "self" | "opp"
    stat: str = ""              # atk|def|sp_atk|sp_def|speed|power|priority|energy_cost
    steps: int = 0              # 正=增益, 负=减益
    scope: str = "persistent"   # persistent|battlefield|permanent


@dataclass
class AbnormalEffect:
    """异常状态。"""
    kind: str = "abnormal"
    target: str = "opp"         # "self" | "opp"
    name: str = ""              # 中毒|灼烧|冻结|寄生|眩晕|萌化
    scope: str = "battlefield"
    stacks: int = 1
    heal_pct: float = 0.0       # 萌化成功后的生命回复比例
    energy_gain: int = 0        # 萌化成功后的能量回复


@dataclass
class MarkEffect:
    """印记赋予。"""
    kind: str = "mark"
    target: str = "own_team"    # "own_team" | "opp_team"
    name: str = ""              # 光合印记|减速印记|...
    stacks: int = 1


@dataclass
class WeatherEffect:
    """天气设置。"""
    kind: str = "weather"
    weather: str = ""           # rain|sand|snow
    turns: int = 8


@dataclass
class SpecialEffect:
    """瞬时特殊机制。"""
    kind: str = "special"
    name: str = ""              # power_bonus|power_mult|damage_mult|damage_reduction
                                # life_drain|steal_energy|escape|burst|charge|multi_hit
                                # direct_heal|heal|reflect_damage|priority_bonus
                                # dispel_positive|dispel_negative|double_positive|double_negative
                                # double_abnormal|abnormal_tick|damage_reduction_by_abnormal
    value: float = 0.0          # 数值（倍率/百分比/次数 / base reduction）
    amount: int = 0             # 整数参数（偷能量点数、驱散数量等）
    target: str = "opp"         # 效果施加对象（self|opp）
    abnormal_name: str = ""     # double_abnormal / abnormal_tick 的目标异常名
    per_stack_value: float = 0.0  # damage_reduction_by_abnormal: 每层追加值
    max_value: float = 1.0      # damage_reduction_by_abnormal: 上限


@dataclass
class ConditionalEffect:
    """条件触发效果。"""
    kind: str = "conditional"
    when: dict | None = None    # Condition dict
    then: list | None = None    # list[Effect]


# Condition dict 格式：
# {"kind": "hp_below",         "ratio": 0.5}
# {"kind": "counter_succeeded"}
# {"kind": "opp_switched"}
# {"kind": "is_first"}
# {"kind": "has_abnormal",     "name": "灼烧"}
# {"kind": "weather_is",       "weather": "rain"}
# {"kind": "counter_ge",       "key": "skills_used", "value": 3}
# {"kind": "and", "conditions": [...]}
# {"kind": "or",  "conditions": [...]}


# Union type for all effects
Effect = StatEffect | AbnormalEffect | MarkEffect | WeatherEffect | SpecialEffect | ConditionalEffect


_KIND_CLASS_MAP: dict[str, type] = {
    'stat': StatEffect,
    'abnormal': AbnormalEffect,
    'mark': MarkEffect,
    'weather': WeatherEffect,
    'special': SpecialEffect,
    'conditional': ConditionalEffect,
}


def effect_from_dict(data: dict) -> Effect:
    """从 JSON dict 反序列化 Effect。按 kind 分派到对应 dataclass。"""
    kind = data.get('kind', '')
    cls = _KIND_CLASS_MAP.get(kind)
    if cls is None:
        raise ValueError(f'Unknown effect kind: {kind!r}')
    # 过滤掉 data 中 cls 不接受的字段
    valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    # 递归处理 conditional 的 then 列表
    if kind == 'conditional' and 'then' in filtered and isinstance(filtered['then'], list):
        filtered['then'] = [effect_from_dict(e) for e in filtered['then']]
    return cls(**filtered)
