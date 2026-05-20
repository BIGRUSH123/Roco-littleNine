"""weather opcode — set or change battlefield weather.

V2: Supports typed WeatherOp alongside backward-compat dict.
"""

from ..ctx import Ctx
from ..journal import Mutation, WeatherSet


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_weather(ctx: Ctx, effect) -> list[Mutation]:
    """Set battlefield weather for a number of turns."""
    weather = _get(effect, "weather")
    turns = _get(effect, "turns", 5)
    return [WeatherSet(weather=weather, turns=turns)]
