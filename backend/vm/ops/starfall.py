"""starfall_trigger opcode — 手动触发星陨印记（引力偏转）。

引力偏转「减伤80%，应对攻击：以魔法伤害触发敌方的星陨效果」：本 op 只是把
「何时触发、以什么伤害类型触发」交给数据，结算完全复用
`backend/sim/globals.py:GlobalEffects.trigger_starfall()`
（= 攻击命中后的自然结算路径：消耗印记层数 + `X² + 24X − 24` 幻系伤害）。
"""

from ..ctx import Ctx
from ..journal import Mutation, StarfallTrigger


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_starfall_trigger(ctx: Ctx, effect) -> list[Mutation]:
    """触发 target（印记持有方）的星陨印记。"""
    target = _get(effect, "target", "sprite_opp")
    damage_type = _get(effect, "damage_type", "魔攻") or "魔攻"
    return [StarfallTrigger(target=target, damage_type=damage_type)]
