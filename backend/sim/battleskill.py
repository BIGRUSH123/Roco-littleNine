"""backend/sim/battleskill.py — 战斗中技能实例 + 使用时快照"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
    _morph_temp: bool = False       # 巧变产物：槽位当前技能为巧变结果（能耗-1，使用后还原）
    _mech_energy_reduction: int = 0 # 机械变式：传动后位置变化技能能耗-1
    # 迸发效果列表：**IR**（`[RiscIROp]`，与技能 effects[]、observer then 同格式，见 IR_GUIDE §3D）。
    # 三条写入路径都在写入前编译：技能显式 then（编译期）、from:"triggered"（池里就是 IR）、
    # 特性 direct-mods 通道（trait_loader 自己 compile_effects_batch）。
    # 读它时按 IR 字段访问（`getattr(e, "source", None)`），不要用 dict 的 `.get()`。
    _burst_effects: list = field(default_factory=list)

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
        if self.nullified:
            return "(打断)"
        skill = self.replaced_by or self.base
        return skill.name

    @property
    def element(self) -> str:
        if self._element_override:
            return self._element_override
        if self.nullified:
            return ""
        skill = self.replaced_by or self.base
        return skill.element

    @property
    def skill_type(self) -> str:
        if self.nullified:
            return "物攻"
        skill = self.replaced_by or self.base
        return skill.skill_type

    @property
    def counter(self) -> str:
        if self.nullified:
            return "无"
        skill = self.replaced_by or self.base
        return skill.counter

    @property
    def priority(self) -> int:
        if self.nullified:
            return 0
        skill = self.replaced_by or self.base
        # 技能级先手修正（power_mod attr:"priority"）：此前写进 _modifiers 但没人读，
        # 先发制人/扬尘/俯冲三条数据因此完全无效
        return skill.priority + int(self._modifiers.get("priority", 0))

    @property
    def combo(self) -> int:
        base_combo = -1 if self.nullified else (self.replaced_by or self.base).combo
        # mode:"set"（强制过滤「连击数固定为1」）走 combo_set 绝对语义，
        # 不能再当成 +N（会把 2 连击的引雷抬到 3 段）
        combo_set = int(self._modifiers.get("combo_set", 0))
        if combo_set > 0:
            return combo_set
        return base_combo + int(self._modifiers.get("combo", 0))

    @property
    def combo_keyword(self) -> bool:
        """是否带**连击词条**（= 技能 JSON 写了 combo 键，`"combo": 1` 也算）。

        词条决定能否吃**精灵级**连击增益（连击+N / 连击率）；技能自身的连击
        文本（`skill_off_0` 修正、`skill.<名>.combo`）不受门控。
        无该属性的旧对象（测试里直接构造的 Skill）回退到「基础连击数 ≥2」。
        """
        if self.nullified:
            return False
        skill = self.replaced_by or self.base
        flag = getattr(skill, 'combo_keyword', None)
        if flag is None:
            return getattr(skill, 'combo', 1) >= 2
        return bool(flag)

    @property
    def is_attack(self) -> bool:
        if self.nullified:
            return True
        skill = self.replaced_by or self.base
        return skill.is_attack

    @property
    def is_defense(self) -> bool:
        if self.nullified:
            return False
        skill = self.replaced_by or self.base
        return skill.is_defense

    @property
    def is_status(self) -> bool:
        if self.nullified:
            return False
        skill = self.replaced_by or self.base
        return skill.is_status

    def get_atk_def_keys(self, sprite=None) -> tuple[str, str] | None:
        if self.nullified:
            return ("atk", "def")
        skill = self.replaced_by or self.base
        return skill.get_atk_def_keys(sprite)

    # ── 合成属性（从 _modifiers 统一读取）──

    @property
    def power(self) -> int:
        base_power = 0 if self.nullified else (self.replaced_by or self.base).power
        return base_power + int(self._modifiers.get("power", 0))

    @property
    def energy_cost(self) -> int:
        base_cost = 0 if self.nullified else (self.replaced_by or self.base).energy_cost
        return base_cost + int(self._modifiers.get("energy_cost", 0)) + self._mech_energy_reduction

    @property
    def has_burst(self) -> bool:
        return bool(self._burst_effects)


@dataclass
class SkillUse:
    """技能一次使用的快照。打包 is_countered / is_first。

    构造后只读，由 Battle._execute_single_action 创建并传递给
    calc_damage / dispatch。

    `modifiers` 是**估伤专用**的临时字典：旧版 kind 效果层删除后，静态效果
    不再向这里写值（`sim/resolver.calc_damage` 改为直接读技能级 / 精灵级
    修正，与实战同口径），只保留供调用方写入派生值（如 `type_mult`）。
    """

    battle_skill: BattleSkill
    is_countered: bool = False
    is_first: bool = False
    countered_skill: BattleSkill | None = None    # 我方反击的对方技能（reflect_damage 用）
    countering_skill: BattleSkill | None = None   # 反击我方的对方技能（damage_reduction 注入用）
    skill_index: int = -1                            # 在 sprite.skills 中的位置
    branch: int | None = None                        # 「选择」分支索引（None = 0 号分支，同引擎）

    modifiers: dict = field(default_factory=dict)

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


def combo_base_count(battle_skill: BattleSkill, sprite=None) -> int:
    """连击段数中**不含 `combo_mult`** 的部分（= 引擎 `ctx.combo_self` 的口径）。

    引擎把 `combo_mult` 留到 `engine/modifiers.effective_combo_count` 最后乘入
    （保证「set/add 之后再乘倍率」的次序），估伤要与之逐项对齐，所以这里把
    「基础段数」单独拆出来；`effective_combo` 在此之上乘倍率，两者的终值一致。
    """
    combo = max(1, battle_skill.combo)
    if sprite is not None and battle_skill.combo_keyword:
        mods = getattr(sprite, '_modifiers', None) or {}
        combo_set = int(mods.get('combo_set', 0) or 0)
        if combo_set > 0:
            combo = max(1, combo_set)
        else:
            combo = max(1, combo + int(mods.get('combo', 0) or 0))
    return combo


def effective_combo(battle_skill: BattleSkill, sprite=None) -> int:
    """连击数 = 技能**释放次数**（AI 估伤唯一入口，与引擎同口径）。

    - 技能级：字段 combo + 技能自身修正（`_modifiers['combo'/'combo_set']`，
      含「每次使用后本技能连击数永久+N」与本次出招的 `skill_off_0` 修正）
    - 精灵级：`combo`/`combo_set`/`combo_mult`（暴风眼、热身运动、耀眼…）——
      只对带**连击词条**（技能 JSON 写了 combo 键，`"combo": 1` 也算）的技能生效
    - 引擎侧等价实现：`engine/snapshot.py` 的 `combo_self`（精灵级 `combo_mult`
      在 `engine/modifiers.adjust_damage` 乘入，顺序为 set/add 之后）
    """
    combo = combo_base_count(battle_skill, sprite)
    if sprite is not None and battle_skill.combo_keyword:
        mods = getattr(sprite, '_modifiers', None) or {}
        mult = float(mods.get('combo_mult', 0.0) or 0.0)
        if mult > 0:
            combo = max(1, round(combo * (1.0 + mult)))
    return combo
