"""weather opcode — set or change battlefield weather.

V2: Supports typed WeatherOp alongside backward-compat dict.

`extend: true` = 延长语义（IR_GUIDE §3B weather）：
  同天气 → 回合数累加；无天气 → 起天气；其它天气 → 不生效。
非 extend（默认）= 重设，行为与旧版一致。
"""

from ..ctx import Ctx
from ..journal import Mutation, WeatherSet


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_weather(ctx: Ctx, effect) -> list[Mutation]:
    """Set battlefield weather for a number of turns (or extend the current one)."""
    weather = _get(effect, "weather")
    turns = _get(effect, "turns", 5)
    extend = bool(_get(effect, "extend", False))
    return [WeatherSet(weather=weather, turns=turns, extend=extend)]
