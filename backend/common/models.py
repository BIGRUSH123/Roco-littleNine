"""
scripts/common/models.py — 共享数据模型

精灵种族值模型 SpeciesStats、属性计算结果 StatsResult。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SpeciesStats:
    """精灵种族值（物种基础属性）。"""
    name: str
    number: str = ""
    form: str = ""
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

    def __post_init__(self):
        if self.bloodline_skills is None:
            self.bloodline_skills = {}

    def base_dict(self) -> dict[str, int]:
        return {
            'hp': self.hp, 'atk': self.atk, 'sp_atk': self.sp_atk,
            'def': self.def_, 'sp_def': self.sp_def, 'speed': self.speed,
        }

    @property
    def elements(self) -> list[str]:
        return [e.strip() for e in self.attributes.split(',') if e.strip()]

    def display_name(self) -> str:
        return f"{self.name}（{self.form}）" if self.form else self.name


@dataclass
class StatsResult:
    """精灵六维属性的完整计算结果。"""
    species: SpeciesStats
    nature: Optional[str]
    iv: dict[str, int]
    mods: list[str]
    ability: str
    base_stats: dict[str, int]       # 种族值
    raw_stats: dict[str, int]        # 公式中括号内（×2 + iv×6 + +5/100）四舍五入后
    nature_stats: dict[str, int]     # 性格修正后
    final_stats: dict[str, int]      # 应用能力修正后
