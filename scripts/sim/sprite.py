"""scripts/sim/sprite.py — 战斗精灵实例 + 状态效果追踪"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from scripts.common import STAT_KEYS
from scripts.common.models import SpeciesStats, StatsResult

if TYPE_CHECKING:
    from .skill import Skill
    from .battleskill import BattleSkill

# 步数换算
_STEP_PCT = 10       # 非速度六维：1步=10%
_SPEED_STEP = 10     # 速度：1步=10点
_POWER_STEP = 10     # 威力：1步=10威力
_PRIORITY_STEP = 1   # 先手：1步=1
_ENERGY_STEP = 1     # 能耗：1步=1

# 非百分比型 stat_key（直接累加步数×单位，不做乘法）
_NON_PCT_KEYS: frozenset[str] = frozenset({'power', 'priority', 'energy_cost'})


@dataclass
class StatusEffect:
    """精灵身上的一个效果。自描述生命周期，用于换宠清除和属性计算。"""

    name: str                           # "物攻+20%" / "中毒" / "防御CD"
    category: str                       # "stat" | "abnormal" | "state"
    stat_key: str = ''                  # 六维键 / power / priority / energy_cost
    steps: int = 0                      # 步数（正=增益，负=减益）
    stacks: int = 1                     # 层数
    scope: str = 'battlefield'          # "permanent" | "persistent" | "battlefield"
    source: str = ''                    # 来源技能/特性名

    @property
    def is_stat(self) -> bool:
        return self.category == 'stat'

    @property
    def is_abnormal(self) -> bool:
        return self.category == 'abnormal'

    @property
    def is_state(self) -> bool:
        return self.category == 'state'


@dataclass
class Sprite:
    """对局中的精灵实例。持有种族值引用 + 实时战斗状态。"""

    # ── 静态 ──
    species: SpeciesStats
    skills: list['BattleSkill'] = field(default_factory=list)

    # ── 初始六维（nature + IV 后的最终值，mods 前） ──
    initial_stats: dict[str, int] = field(default_factory=dict)

    # ── 实时状态 ──
    current_hp: int = 0
    max_hp: int = 0
    energy: int = 10            # 0-10

    # 全部效果（增/减益 + 异常状态 + 特殊状态）
    effects: list[StatusEffect] = field(default_factory=list)

    # 进场回合
    entry_turn: int = 0

    # 通用计数器（"每使用N次技能" / "每受到N次伤害" / "每入场N次" 等）
    counters: dict[str, int] = field(default_factory=dict)

    # 迸发判定：进场后第一次行动
    first_action: bool = True

    # ── 有效属性 ──

    @property
    def is_fainted(self) -> bool:
        return self.current_hp <= 0

    @property
    def name(self) -> str:
        return self.species.display_name()

    def _sum_steps(self, stat_key: str) -> int:
        return sum(
            e.steps for e in self.effects
            if e.is_stat and e.stat_key == stat_key
        )

    def effective_stat(self, stat_key: str) -> int:
        """返回六维属性经效果修正后的有效值。"""
        if stat_key in _NON_PCT_KEYS:
            return self._sum_steps(stat_key)
        base = self.initial_stats.get(stat_key, 0)
        total_steps = self._sum_steps(stat_key)
        if stat_key == 'speed':
            return max(0, base + total_steps * _SPEED_STEP)
        return max(0, round(base * (1.0 + total_steps / _STEP_PCT)))

    @property
    def effective_stats(self) -> dict[str, int]:
        return {k: self.effective_stat(k) for k in STAT_KEYS}

    # ── 非六维修正（威力/先手/能耗） ──

    @property
    def power_mod(self) -> int:
        """威力修正步数。1步 = 10威力。"""
        return self._sum_steps('power')

    @property
    def priority_mod(self) -> int:
        """先手修正步数。1步 = 1先手值。"""
        return self._sum_steps('priority')

    @property
    def energy_cost_mod(self) -> int:
        """能耗修正步数。1步 = 1能耗。"""
        return self._sum_steps('energy_cost')

    # ── 效果管理 ──

    def add_effect(self, effect: StatusEffect) -> None:
        """添加效果。异常状态按同名合并层数；stat/state 追加（可叠加多条）。"""
        if effect.is_stat or effect.is_state:
            self.effects.append(effect)
            return
        for existing in self.effects:
            if existing.category == effect.category and existing.name == effect.name:
                existing.stacks += effect.stacks
                return
        self.effects.append(effect)

    def remove_effect(self, name: str, category: str = '') -> None:
        self.effects = [
            e for e in self.effects
            if e.name != name or (category and e.category != category)
        ]

    def get_effects(self, category: str = '') -> list[StatusEffect]:
        if category:
            return [e for e in self.effects if e.category == category]
        return list(self.effects)

    def get_stacks(self, name: str) -> int:
        for e in self.effects:
            if e.name == name:
                return e.stacks
        return 0

    def update_stacks(self, name: str, stacks: int) -> None:
        """直接设置异常状态层数（如灼烧衰减）。"""
        self.remove_effect(name, 'abnormal')
        if stacks > 0:
            self.add_effect(StatusEffect(
                name=name, category='abnormal', stacks=stacks,
                scope='battlefield', source='衰减',
            ))

    def clear_effects(self, scope: str) -> None:
        """清除指定 scope 的全部效果（换宠用）。"""
        self.effects = [
            e for e in self.effects
            if e.scope != scope
        ]

    def clear_all_effects(self) -> None:
        self.effects.clear()

    # ── 计数器 ──

    def inc_counter(self, key: str, delta: int = 1) -> int:
        self.counters[key] = self.counters.get(key, 0) + delta
        return self.counters[key]

    def get_counter(self, key: str) -> int:
        return self.counters.get(key, 0)

    # ── HP / 能量 ──

    def take_damage(self, amount: int) -> int:
        actual = min(self.current_hp, amount)
        self.current_hp -= actual
        return actual

    def heal(self, amount: int) -> int:
        actual = min(self.max_hp - self.current_hp, amount)
        self.current_hp += actual
        return actual

    def gain_energy(self, amount: int) -> int:
        actual = min(10 - self.energy, amount)
        self.energy += actual
        return actual

    def lose_energy(self, amount: int) -> int:
        actual = min(self.energy, amount)
        self.energy -= actual
        return actual

    # ── 构造 ──

    @classmethod
    def from_result(cls, result: StatsResult, energy: int = 10) -> 'Sprite':
        return cls(
            species=result.species,
            initial_stats=dict(result.final_stats),
            current_hp=result.final_stats['hp'],
            max_hp=result.final_stats['hp'],
            energy=energy,
        )
