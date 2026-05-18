"""scripts/sim/battleskill.py — 战斗中技能实例 + 使用时快照"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .effects import SpecialName

if TYPE_CHECKING:
    from .skill import Skill


@dataclass
class BattleSkill:
    """战斗中一个技能槽的实例。持有静态 Skill + 可变战斗状态。"""

    base: 'Skill'

    # ── 可变状态 ──
    power_mod: int = 0              # 永久威力变化（联动装置等）
    combo_mod: int = 0              # 永久连击数变化（聚盐/乘胜追击等）
    energy_cost_mod: int = 0        # 永久能耗变化（水炮/重击等）
    power_override: int | None = None  # 动态威力（冰锋横扫/钢钻），覆盖 base.power
    replaced_by: 'Skill | None' = None  # 技能替换（镜像反射）
    cooldown: int = 0               # 剩余冷却回合（防御技能）
    next_attack_mult: float = 1.0   # 下次攻击威力倍率（热身），使用后重置为 1
    nullified: bool = False         # 打断标记：技能被无效化但不破坏 base
    sealed: bool = False            # 封印标记：此槽位不可选用（宝剑王牌/正位宝剑）
    _transmission: int = 0          # 传动等级（向心力/翼轴）
    _main_axis: bool = False        # 主轴技能：不参与传动
    _element_override: str = ''     # 属性覆写（元素转换特性）

    @property
    def skill(self) -> 'Skill':
        """当前生效的技能（可能被替换/打断）。"""
        if self.nullified:
            from .skill import Skill
            return Skill.null()
        return self.replaced_by or self.base

    # ── Skill 属性显式委托（替代 __getattr__）──

    @property
    def name(self) -> str:
        return self.skill.name

    @property
    def element(self) -> str:
        return self._element_override or self.skill.element

    @property
    def skill_type(self) -> str:
        return self.skill.skill_type

    @property
    def counter(self) -> str:
        return self.skill.counter

    @property
    def priority(self) -> int:
        return self.skill.priority

    @property
    def combo(self) -> int:
        return self.skill.combo + self.combo_mod

    @property
    def effects(self) -> list:
        return self.skill.effects

    @property
    def is_attack(self) -> bool:
        return self.skill.is_attack

    @property
    def is_defense(self) -> bool:
        return self.skill.is_defense

    @property
    def is_status(self) -> bool:
        return self.skill.is_status

    def get_atk_def_keys(self, sprite=None) -> tuple[str, str] | None:
        return self.skill.get_atk_def_keys(sprite)

    # ── 合成属性 ──

    @property
    def power(self) -> int:
        base = self.skill.power
        if self.power_override is not None:
            base = self.power_override
        return base + self.power_mod

    @property
    def energy_cost(self) -> int:
        return self.skill.energy_cost + self.energy_cost_mod


@dataclass
class SkillUse:
    """技能一次使用的快照。预计算 modifiers，打包 is_countered / is_first。

    构造后只读，由 Battle._execute_single_action 创建并传递给
    calc_damage / dispatch。
    """

    battle_skill: BattleSkill
    is_countered: bool = False
    is_first: bool = False
    countered_skill: 'BattleSkill | None' = None    # 我方反击的对方技能（reflect_damage 用）
    countering_skill: 'BattleSkill | None' = None   # 反击我方的对方技能（damage_reduction 注入用）
    skill_index: int = -1                            # 在 sprite.skills 中的位置

    # __post_init__ 预计算
    modifiers: dict = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.modifiers = self._collect_modifiers()

    def _collect_modifiers(self) -> dict:
        """收集伤害修正：自身技能 + 对方防御技能的 countered_skill 注入。"""
        modifiers: dict = {}
        is_attack = self.battle_skill.is_attack

        # ── 自身技能效果 ──
        for effect in self.battle_skill.effects:
            kind = getattr(effect, 'kind', '')

            if kind == 'special':
                name = getattr(effect, 'name', '')
                if name == SpecialName.IGNORE_MODS:
                    modifiers['ignore_mods'] = True
                if not is_attack:
                    continue
                if name in SpecialName.DAMAGE_SPECIALS:
                    modifiers[name] = getattr(effect, 'value', 0) or getattr(effect, 'amount', 0)

            elif kind == 'conditional':
                when = getattr(effect, 'when', None)
                then = getattr(effect, 'then', None)
                otherwise = getattr(effect, 'otherwise', None)
                if not when:
                    continue
                cond_met = not (when.get('kind') == 'counter_succeeded' and self.countered_skill is None)
                branches = then if cond_met else otherwise
                if not branches:
                    continue
                for sub in branches:
                    if getattr(sub, 'kind', '') != 'special':
                        continue
                    sub_name = getattr(sub, 'name', '')
                    if sub_name == SpecialName.IGNORE_MODS:
                        modifiers['ignore_mods'] = True
                    if not is_attack:
                        continue
                    if sub_name in SpecialName.DAMAGE_SPECIALS:
                        modifiers[sub.name] = getattr(sub, 'value', 0) or getattr(sub, 'amount', 0)

        # ── 对方防御技能注入（我被 counter 了，对方的防御效果削弱我的伤害）──
        if self.is_countered and self.countering_skill:
            for effect in self.countering_skill.effects:
                kind = getattr(effect, 'kind', '')
                if kind == 'special':
                    name = getattr(effect, 'name', '')
                    val = getattr(effect, 'value', 0) or 0
                    if name == SpecialName.IGNORE_MODS:
                        modifiers['ignore_mods'] = True
                    if name == SpecialName.DAMAGE_REDUCTION:
                        modifiers['damage_reduction'] = max(
                            modifiers.get('damage_reduction', 0), val)
                    elif name == SpecialName.DAMAGE_MULT:
                        modifiers['damage_mult'] = min(
                            modifiers.get('damage_mult', 1.0), val or 1.0)
                elif kind == 'conditional':
                    when = getattr(effect, 'when', None)
                    then = getattr(effect, 'then', None)
                    if not when or not then:
                        continue
                    if when.get('kind') != 'counter_succeeded':
                        continue
                    for sub in then:
                        if getattr(sub, 'kind', '') != 'special':
                            continue
                        name = getattr(sub, 'name', '')
                        val = getattr(sub, 'value', 0) or 0
                        if name == SpecialName.IGNORE_MODS:
                            modifiers['ignore_mods'] = True
                        if name == SpecialName.DAMAGE_REDUCTION:
                            modifiers['damage_reduction'] = max(
                                modifiers.get('damage_reduction', 0), val)
                        elif name == SpecialName.DAMAGE_MULT:
                            modifiers['damage_mult'] = min(
                                modifiers.get('damage_mult', 1.0), val or 1.0)

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
