"""redirect opcode — redirect damage target."""

from ..ctx import Ctx
from ..journal import Redirect, Mutation


def op_redirect(ctx: Ctx, effect: dict) -> list[Mutation]:
    """Redirect this skill's damage to a different target."""
    target = effect.get("target", "sprite_self")
    return [Redirect(target=target)]
