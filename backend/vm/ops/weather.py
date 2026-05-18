"""weather opcode — set or change battlefield weather."""

from ..ctx import Ctx
from ..journal import WeatherSet, Mutation


def op_weather(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Set battlefield weather for a number of turns."""
    weather = effect["weather"]
    turns = effect.get("turns", 5)
    return [WeatherSet(weather=weather, turns=turns)]
