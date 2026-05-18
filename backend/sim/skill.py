"""scripts/sim/skill.py — 战斗技能（自包含，从 JSON 反序列化）"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .effects import effect_from_dict, Effect

if TYPE_CHECKING:
    from .sprite import Sprite


_TYPE_ATK_DEF: dict[str, tuple[str, str] | None] = {
    '物攻': ('atk', 'def'),
    '魔攻': ('sp_atk', 'sp_def'),
    '动态攻击': None,  # 取精灵物攻/魔攻最高项
}

_ATTACK_TYPES: frozenset[str] = frozenset({'物攻', '魔攻', '动态攻击'})


@dataclass
class Skill:
    """战斗技能。从 JSON 反序列化，不再依赖 wiki/SkillInfo。"""

    id: int = 0
    name: str = ''
    element: str = ''
    skill_type: str = ''       # 物攻|魔攻|动态攻击|防御|状态
    power: int = 0
    energy_cost: int = 0
    counter: str = '无'        # 无|攻击|防御|状态
    priority: int = 0
    combo: int = 1             # 连击数：技能重复执行次数
    effects: list[Effect] = field(default_factory=list)
    exclusive_to: str = ''     # 专属技能归属精灵名（萌化后不匹配则封印）

    @classmethod
    def load(cls, data: dict) -> 'Skill':
        """从 JSON dict 反序列化。"""
        effects_raw = data.get('effects', [])
        effects = [effect_from_dict(e) for e in effects_raw]
        return cls(
            id=data.get('id', 0),
            name=data['name'],
            element=data.get('element', ''),
            skill_type=data.get('skill_type', ''),
            power=data.get('power', 0),
            energy_cost=data.get('energy_cost', 0),
            counter=data.get('counter', '无'),
            priority=data.get('priority', 0),
            combo=data.get('combo', 1),
            effects=effects,
            exclusive_to=data.get('exclusive_to', ''),
        )

    @classmethod
    def null(cls) -> 'Skill':
        """空技能：打断后被替换为此，无属性/无威力/无效果。"""
        return cls(name='(打断)', element='', skill_type='物攻', power=0, energy_cost=0)

    # ── 类型判定 ──

    @property
    def is_attack(self) -> bool:
        return self.skill_type in _ATTACK_TYPES

    @property
    def is_defense(self) -> bool:
        return self.skill_type == '防御'

    @property
    def is_status(self) -> bool:
        return self.skill_type == '状态'

    def get_atk_def_keys(self, sprite: 'Sprite | None' = None) -> tuple[str, str] | None:
        """返回 (攻击键, 防御键)。动态攻击需传入精灵以判定物/魔。"""
        mapping = _TYPE_ATK_DEF.get(self.skill_type)
        if mapping is not None:
            return mapping
        if self.skill_type == '动态攻击' and sprite is not None:
            if sprite.effective_stat('atk') >= sprite.effective_stat('sp_atk'):
                return ('atk', 'def')
            return ('sp_atk', 'sp_def')
        return None
