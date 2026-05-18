"""tick opcode — trigger abnormal tick damage/effect."""

from ..ctx import Ctx
from ..journal import Tick, Mutation


def op_tick(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Trigger a single tick of an abnormal status on a target."""
    target = effect.get("target", "sprite_opp")
    name = effect["name"]
    return [Tick(target=target, abnormal_name=name)]
