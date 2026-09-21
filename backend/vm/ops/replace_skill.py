"""replace_skill opcode — 把对手本回合的技能替换为指定技能（本回合有效）。

用于「应对状态时被应对的技能变为透射」（透镜实验）。
落点在 replayer（需要 battle 引用定位对手本回合的技能槽），
回合末由 `Battle._phase_turn_end()` 还原（见 IR_GUIDE §3C replace_skill）。
"""

from ..ctx import Ctx
from ..journal import Mutation, ReplaceSkill


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_replace_skill(ctx: Ctx, effect) -> list[Mutation]:
    target = _get(effect, "target", "skill_opp_current")
    skill = _get(effect, "skill", "")
    scope = _get(effect, "scope", "turn")
    if not skill:
        return []
    return [ReplaceSkill(target=target, skill=skill, scope=scope)]
