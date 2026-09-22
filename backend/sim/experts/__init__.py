# -*- coding: utf-8 -*-
"""按队伍微调的专家（阵容专精层）。

与通用专家（`RuleAgentV2`）的关系：**继承 + 只加"这支队特有"的规则**，其余决策全部回落到
通用逻辑。设计依据是社区攻略（`rocopvp.tzrain.wiki` 的 popular/teams + builds 说明）与
`data/sprites`/`data/traits`/`data/skills` 的机制原文，见各模块 docstring。

注册表：`expert_for_team(队名) -> 专家类 | None`。用法：
    from backend.sim.experts import expert_for_team
    cls = expert_for_team("星陨队") or RuleAgentV2
    agent = cls("A", player, strategy=strategy)
"""
from __future__ import annotations

from .ground_wu import GroundWuExpert
from .poison import PoisonExpert
from .rain import RainExpert
from .shadow import ShadowPoisonExpert
from .starfall import StarfallExpert

# 队名（meta_teams.json 的 name）→ 专家类
_EXPERTS = {
    "星陨队": StarfallExpert,
    "魔偶雨天队": RainExpert,
    "首领毒": PoisonExpert,
    "新地武": GroundWuExpert,
    "【搬运】黑影平衡毒": ShadowPoisonExpert,
}


def expert_for_team(team_name: str):
    """按队名取专家类；没有专精的队返回 None（调用方回落通用专家）。"""
    return _EXPERTS.get(team_name)


def expert_names() -> dict[str, str]:
    return {k: v.__name__ for k, v in _EXPERTS.items()}


__all__ = ["expert_for_team", "expert_names", "StarfallExpert", "RainExpert",
           "PoisonExpert", "GroundWuExpert", "ShadowPoisonExpert"]
