"""backend/sim/battleskill.py — 战斗中技能实例 + 使用时快照"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .effects import SPECIAL_KINDS, SpecialName

if TYPE_CHECKING:
    from .skill import Skill


@dataclass
class BattleSkill:
    """战斗中一个技能槽的实例。持有静态 Skill + 可变战斗状态。"""

    base: Skill

    # ── 可变状态 ──
    _modifiers: dict[str, float] = field(default_factory=dict)  # 技能级修饰符（power/energy_cost/combo/power_mult等）
    replaced_by: Skill | None = None  # 技能替换（镜像反射）
    cooldown: int = 0               # 剩余冷却回合（防御技能）
    next_attack_mult: float = 1.0   # 下次攻击威力倍率（热身），使用后重置为 1
    nullified: bool = False         # 打断标记：技能被无效化但不破坏 base
    sealed: bool = False            # 封印标记：此槽位不可选用（宝剑王牌/正位宝剑）
    is_temporary: bool = False      # 临时技能标记（gain_skills 等，战斗结束后清理）
    _transmission: int = 0          # 传动等级：-1=主轴，0=普通，1+=传动
    _element_override: str = ''     # 属性覆写（元素转换特性）
    _mech_energy_reduction: int = 0 # 机械变式：传动后位置变化技能能耗-1

    def __post_init__(self) -> None:
        if self.base.transmission:
            self._transmission = self.base.transmission

    def load_permanent_mods(self, sprite_modifiers: dict[str, float]) -> None:
        """Load permanent skill-scoped modifiers from sprite._modifiers.

        Permanent modifiers are stored as skill.{skill_name}.{stat} keys.
        Called after BattleSkill is created and added to a sprite.
        """
        if not self.base.name:
            return
        prefix = f"skill.{self.base.name}."
        for key, value in sprite_modifiers.items():
            if key.startswith(prefix):
                stat = key[len(prefix):]
                self._modifiers[stat] = value

    @property
    def skill(self) -> Skill:
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
        return self.skill.combo + int(self._modifiers.get("combo", 0))

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

    # ── 合成属性（从 _modifiers 统一读取）──

    @property
    def power(self) -> int:
        return self.skill.power + int(self._modifiers.get("power", 0))

    @property
    def energy_cost(self) -> int:
        return self.skill.energy_cost + int(self._modifiers.get("energy_cost", 0)) + self._mech_energy_reduction


@dataclass
class SkillUse:
    """技能一次使用的快照。预计算 modifiers，打包 is_countered / is_first。

    构造后只读，由 Battle._execute_single_action 创建并传递给
    calc_damage / dispatch。
    """

    battle_skill: BattleSkill
    is_countered: bool = False
    is_first: bool = False
    countered_skill: BattleSkill | None = None    # 我方反击的对方技能（reflect_damage 用）
    countering_skill: BattleSkill | None = None   # 反击我方的对方技能（damage_reduction 注入用）
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

            if kind in SPECIAL_KINDS:
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
                    if getattr(sub, 'kind', '') not in SPECIAL_KINDS:
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
                if kind in SPECIAL_KINDS:
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
                        if getattr(sub, 'kind', '') not in SPECIAL_KINDS:
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
    def counter_power_mult(self) -> float:
        return self.modifiers.get('counter_power_mult', 1.0)

    @property
    def damage_mult(self) -> float:
        return self.modifiers.get('damage_mult', 1.0)

    @property
    def damage_reduction(self) -> float:
        return self.modifiers.get('damage_reduction', 0.0)

    @property
    def multi_hit(self) -> float:
        return self.modifiers.get('multi_hit', 1.0)
