"""
scripts/common/models.py — 共享数据模型

精灵种族值模型 SpeciesStats、属性计算结果 StatsResult。
"""

from dataclasses import dataclass, field


@dataclass
class SpeciesStats:
    """精灵种族值（物种基础属性）。

    form:       形态阶段标记 —— ''（基础）或 '首领形态'（首领阶段）
    appearance: 外观名 —— ''（默认外观）或「夏天的样子」「蜕皮时的样子」等
    两者是独立维度：任何阶段都可以有多个外观。
    """
    name: str
    number: str = ""
    form: str = ""
    appearance: str = ""
    hp: int = 0
    atk: int = 0
    sp_atk: int = 0
    def_: int = 0
    sp_def: int = 0
    speed: int = 0
    attributes: str = ""
    bloodline: str = ""
    ability: str = ""
    ability_id: int = 0
    pre_species: str = ""
    bloodline_skills: dict[str, int] = None  # type: ignore
    #: 精灵体重（kg）。来源 `data/sprites/_weights.json`（由
    #: `backend/tools/gen_sprite_weights.py` 从 nrc Catalog.lua 生成，区间取中点）。
    #: 缺失（sidecar 未生成 / 该形态无对应条目）时 0.0。
    weight: float = 0.0

    _elements: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self):
        if self.bloodline_skills is None:
            self.bloodline_skills = {}
        self._elements = tuple(
            e.strip() for e in self.attributes.split(',') if e.strip()
        ) if self.attributes else ()

    def base_dict(self) -> dict[str, int]:
        return {
            'hp': self.hp, 'atk': self.atk, 'sp_atk': self.sp_atk,
            'def': self.def_, 'sp_def': self.sp_def, 'speed': self.speed,
        }

    @property
    def elements(self) -> tuple[str, ...]:
        return self._elements

    def display_name(self) -> str:
        """展示名：外观优先 → 形态（首领形态等）→ 纯名字。

        2026-09-20 还原 64294b2「首领 JSON 去后缀」的语义：那次改动后首领形态
        不再拼接 form，导致同编号下「首领形态（无外观）」在展示层与纯名字条目
        无法区分；而外观之间种族值可能不同，用户要求以名字区分。此处外观优先
        （外观比首领形态更细），无外观时才退回 form。
        """
        if self.appearance:
            return f"{self.name}（{self.appearance}）"
        if self.form:
            return f"{self.name}（{self.form}）"
        return self.name

    def is_leader_stage(self) -> bool:
        return '首领' in (self.form or '')


@dataclass
class StatsResult:
    """精灵六维属性的完整计算结果。"""
    species: SpeciesStats
    nature: str | None
    iv: dict[str, int]
    mods: list[str]
    ability: str
    base_stats: dict[str, int]       # 种族值
    raw_stats: dict[str, int]        # 公式中括号内（×2 + iv×6 + +5/100）四舍五入后
    nature_stats: dict[str, int]     # 性格修正后
    final_stats: dict[str, int]      # 应用能力修正后
