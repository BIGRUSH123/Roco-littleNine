"""backend/sim/skill.py — 战斗技能（自包含，从 JSON 反序列化）"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    """战斗技能。从 JSON 反序列化，不再依赖 wiki/SkillInfo。

    效果**只有一种表示**：JSON `effects[]` 的 IR，由 `SkillCompiler` 编译成
    `CompiledSkill.effects`（VM 执行、规则层经 `sim/skill_ir.py` 只读查询）。
    sim 层因此不再持有任何效果对象——旧版 `kind` 形式的 `SpecialName` /
    `effect_from_dict` 层与 `Skill.effects` 字段已于 2026-09-22 删除
    （0 数据用量；遗留读取方已按「保持行为不变」清理）。
    """

    id: int = 0
    name: str = ''
    element: str = ''
    skill_type: str = ''       # 物攻|魔攻|动态攻击|防御|状态
    power: int = 0
    energy_cost: int = 0
    counter: str = '无'        # 无|攻击|防御|状态
    priority: int = 0
    combo: int = -1            # 基础释放次数（连击词条的值）
    combo_keyword: bool | None = None  # 连击词条：True/False 由加载器按「JSON 有没有 combo 键」
                               # 写入；None = 未知（直接构造的合成技能）→ 回退按 combo≥2 判
    exclusive_to: str = ''     # 专属技能归属精灵名（萌化后不匹配则封印）
    transmission: int = 0      # 传动：-1=主轴（不参与传动），0=普通，1+=传动等级
    tag: str = ''              # 机制标签：'迅捷' / '传动'（技能自带，入场迅捷/传动 pass 读它）
    description: str = ''      # 人类可读描述（API/前端展示用）
    usable_while_charging: bool = False  # 蓄力期间是否可使用
    qiaobian: object = None    # 巧变类别（str 池名 或 spec dict；使用后变为该类别的技能）
    morph: object = None       # 变身声明（dict；每回合开始时本槽变为池中随机技能，见 IR_GUIDE「变身」）

    @classmethod
    def load(cls, data: dict) -> Skill:
        """从 JSON dict 反序列化（只取元数据；技能效果是 IR，见下方类注释）。"""
        return cls(
            id=data.get('id', 0),
            name=data['name'],
            element=data.get('element', ''),
            skill_type=data.get('skill_type', ''),
            power=data.get('power', 0),
            energy_cost=data.get('energy_cost', 0),
            counter=data.get('counter', '无'),
            priority=data.get('priority', 0),
            # 缺省 1=单次（与 SimFactory._build_skill_list 一致）。combo_keyword
            # 记录「JSON 是否写了 combo 键」= 是否带连击词条：写了（哪怕 "combo": 1，
            # 游戏原文里的「1连击」）才能吃精灵级连击增益；没写则恒 1 次。
            # 见 data/IR_GUIDE.md §八「连击语义」。
            combo=data.get('combo', 1),
            combo_keyword=('combo' in data),
            exclusive_to=data.get('exclusive_to', ''),
            transmission=data.get('transmission', 0),
            tag=data.get('tag', ''),
            description=data.get('description', ''),
            usable_while_charging=data.get('usable_while_charging', False),
            qiaobian=data.get('qiaobian'),
            morph=data.get('morph'),
        )

    @classmethod
    def null(cls) -> Skill:
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

    def get_atk_def_keys(self, sprite: Sprite | None = None) -> tuple[str, str] | None:
        """返回 (攻击键, 防御键)。动态攻击需传入精灵以判定物/魔。"""
        mapping = _TYPE_ATK_DEF.get(self.skill_type)
        if mapping is not None:
            return mapping
        if self.skill_type == '动态攻击' and sprite is not None:
            if sprite.effective_stat('atk') >= sprite.effective_stat('sp_atk'):
                return ('atk', 'def')
            return ('sp_atk', 'sp_def')
        return None
