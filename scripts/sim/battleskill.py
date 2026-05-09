"""scripts/sim/battleskill.py — 战斗中技能实例（可变状态 + 静态 Skill）"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .skill import Skill


@dataclass
class BattleSkill:
    """战斗中一个技能槽的实例。持有静态 Skill + 可变战斗状态。

    通过 __getattr__ 将未覆写的属性委托给 effective skill，
    因此大多数只读代码无需改动。
    """

    base: 'Skill'

    # ── 可变状态 ──
    power_mod: int = 0              # 永久威力变化（联动装置等）
    replaced_by: 'Skill | None' = None  # 技能替换（镜像反射）
    cooldown: int = 0               # 剩余冷却回合（防御技能）
    next_attack_mult: float = 1.0   # 下次攻击威力倍率（热身），使用后重置为 1

    @property
    def skill(self) -> 'Skill':
        """当前生效的技能（可能被替换）。"""
        return self.replaced_by or self.base

    @property
    def power(self) -> int:
        return self.skill.power + self.power_mod

    @property
    def energy_cost(self) -> int:
        return self.skill.energy_cost

    def __getattr__(self, name: str):
        """未覆写的属性 → 委托给 effective skill。"""
        # __getattr__ 仅在常规查找失败时调用
        return getattr(self.skill, name)
