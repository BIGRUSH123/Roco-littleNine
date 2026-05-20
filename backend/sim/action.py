"""backend/sim/action.py — 回合行动"""

from dataclasses import dataclass


@dataclass
class Action:
    """玩家在一个回合中选择的操作。"""

    kind: str               # "gather" | "switch" | "skill" | "item"
    skill_index: int | None = None   # skills 索引
    switch_index: int | None = None  # 换宠目标索引（team 中的位置）

    def __repr__(self) -> str:
        if self.kind == 'skill':
            return f'Action(skill[{self.skill_index}])'
        if self.kind == 'switch':
            return f'Action(switch→[{self.switch_index}])'
        if self.kind == 'item':
            return 'Action(item)'
        return f'Action({self.kind})'
