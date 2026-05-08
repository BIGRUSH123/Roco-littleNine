"""scripts/sim/effects.py — 类型化技能效果原语

定义 6 种效果类型，替代旧的 regex 字符串标签。
"""

from __future__ import annotations
from dataclasses import dataclass, field


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
    value: float = 0.0          # 数值（倍率/百分比/次数）
    amount: int = 0             # 整数参数（偷能量点数等）


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
