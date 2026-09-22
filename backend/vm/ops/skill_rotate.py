"""skill_rotate opcode — 跨精灵技能轮转（过山车）。

过山车「使己方队伍中的所有精灵携带的技能跨精灵向下移动1个位置」：本 op 只是把
「轮转哪一队、转几位」交给数据，落地由可复用 pass
`backend/sim/battle_mechanics.py:BattleMechanicsMixin.rotate_team_skills()` 完成
（口径见 data/IR_GUIDE.md §3C `skill_rotate`）。
"""

from ..ctx import Ctx
from ..journal import Mutation, SkillRotate


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_skill_rotate(ctx: Ctx, effect) -> list[Mutation]:
    """轮转 target 队伍携带的技能。"""
    target = _get(effect, "target", "team_own")
    try:
        offset = int(_get(effect, "offset", 1))
    except (TypeError, ValueError):
        offset = 1
    return [SkillRotate(target=target, offset=offset)]
