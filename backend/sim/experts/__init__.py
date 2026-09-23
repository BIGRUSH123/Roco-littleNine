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
from .bug import BugExpert
from .iron_seal import IronSealExpert
from .poison import PoisonExpert
from .rain import RainExpert
from .shadow import ShadowPoisonExpert
from .squirrel import SquirrelExpert
from .wing_king import WingKingExpert
from .starfall import StarfallExpert

# 队名（meta_teams.json 的 name）→ 专家类
_EXPERTS = {
    "星陨队": StarfallExpert,
    "魔偶雨天队": RainExpert,
    "首领毒": PoisonExpert,
    "新地武": GroundWuExpert,
    "【搬运】黑影平衡毒": ShadowPoisonExpert,
    "铁头海豹平衡队": IronSealExpert,
    "电羊松鼠平衡队": SquirrelExpert,
    "羽刃翼王铁头队": WingKingExpert,
    "虫": BugExpert,
}


def expert_for_team(team_name: str):
    """按队名取专家类；没有专精的队返回 None（调用方回落通用专家）。

    注意： /  的默认规则集是**空的**（试点三版/两版都实测为负，见 docs §25），
    取到的类行为上等同通用专家，规则保留供复测。
    """
    return _EXPERTS.get(team_name)


def expert_names() -> dict[str, str]:
    return {k: v.__name__ for k, v in _EXPERTS.items()}


def expert_by_class_name(name: str):
    """按类名取专家类（没有则 None）。

    对局计划要跨进程传（multiprocessing spawn），**不能带类对象**，所以计划里存类名、
    worker 侧再用本函数还原。见 `native/tools/gen_bc_data.py --expert team`。
    """
    for cls in _EXPERTS.values():
        if cls.__name__ == name:
            return cls
    return None


__all__ = ["expert_for_team", "expert_names", "expert_by_class_name",
           "StarfallExpert", "RainExpert",
           "PoisonExpert", "GroundWuExpert", "ShadowPoisonExpert",
           "IronSealExpert", "SquirrelExpert", "WingKingExpert", "BugExpert"]
