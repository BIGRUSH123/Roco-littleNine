"""scripts/sim/battleskill.py — 战斗中技能实例 + 使用时快照"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .skill import Skill

# 伤害相关的特殊效果名 — 与 resolver._DAMAGE_SPECIALS 同步
_DAMAGE_SPECIALS: frozenset[str] = frozenset({
    'power_bonus', 'power_mult', 'damage_mult', 'damage_reduction',
    'multi_hit',
})


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
        return getattr(self.skill, name)


@dataclass
class SkillUse:
    """技能一次使用的快照。预计算 modifiers，打包 is_countered / is_first。

    构造后只读，由 Battle._execute_single_action 创建并传递给
    calc_damage / dispatch。
    """

    battle_skill: BattleSkill
    is_countered: bool = False
    is_first: bool = False
    countered_skill: 'BattleSkill | None' = None   # 被应对的对方技能
    skill_index: int = -1                           # 在 sprite.skills 中的位置

    # __post_init__ 预计算
    modifiers: dict = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.modifiers = self._collect_modifiers()

    def _collect_modifiers(self) -> dict:
        """收集伤害修正：仅攻击技能收集 damage specials。
        非攻击技能的 damage specials 由 dispatch 转为 BattleSkill 状态变更。
        """
        modifiers: dict = {}
        is_attack = self.battle_skill.is_attack

        for effect in self.battle_skill.effects:
            kind = getattr(effect, 'kind', '')

            if kind == 'special':
                if not is_attack:
                    continue
                if getattr(effect, 'name', '') in _DAMAGE_SPECIALS:
                    modifiers[effect.name] = getattr(effect, 'value', 0) or getattr(effect, 'amount', 0)

            elif kind == 'conditional':
                when = getattr(effect, 'when', None)
                then = getattr(effect, 'then', None)
                if not when or not then:
                    continue
                if when.get('kind') == 'counter_succeeded' and self.countered_skill is None:
                    continue
                for sub in then:
                    if not is_attack:
                        continue
                    if getattr(sub, 'kind', '') == 'special' and getattr(sub, 'name', '') in _DAMAGE_SPECIALS:
                        modifiers[sub.name] = getattr(sub, 'value', 0) or getattr(sub, 'amount', 0)

        return modifiers

    # ── 便捷属性 ──

    @property
    def power_mult(self) -> float:
        return self.modifiers.get('power_mult', 1.0)

    @property
    def damage_mult(self) -> float:
        return self.modifiers.get('damage_mult', 1.0)

    @property
    def damage_reduction(self) -> float:
        return self.modifiers.get('damage_reduction', 0.0)

    @property
    def multi_hit(self) -> float:
        return self.modifiers.get('multi_hit', 1.0)
