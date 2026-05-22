"""inherit_effects opcode — transfer effects between sprites on switch.

V2: Typed InheritEffects op.
"""

from ..ctx import Ctx
from ..journal import InheritEffectsMutation, Mutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_inherit_effects(ctx: Ctx, effect) -> list[Mutation]:
    source = _get(effect, "source", "self")
    inherit_target = _get(effect, "inherit_target", "enemy_new")
    scope = _get(effect, "scope", "battlefield")
    via_pending = _get(effect, "via_pending", False)
    return [InheritEffectsMutation(
        source_key=source,
        target_key=inherit_target,
        scope=scope,
        via_pending=via_pending,
    )]
