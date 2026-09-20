"""backend/sim/action.py — 回合行动"""

from dataclasses import dataclass


@dataclass(slots=True)
class Action:
    """玩家在一个回合中选择的操作。"""

    kind: str               # "gather" | "switch" | "skill" | "item"
    skill_index: int | None = None   # skills 索引
    switch_index: int | None = None  # 换宠目标索引（team 中的位置）
    branch: int | None = None        # 「选择」分支索引（含 choices 的技能）
    variant: int | None = None       # 道具形态槽位（进化之力 0-4 → 首领形态候选）

    def __repr__(self) -> str:
        if self.kind == 'skill':
            return f'Action(skill[{self.skill_index}])'
        if self.kind == 'switch':
            return f'Action(switch→[{self.switch_index}])'
        if self.kind == 'item':
            return f'Action(item[{self.variant}])' if self.variant is not None else 'Action(item)'
        return f'Action({self.kind})'
