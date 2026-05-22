"""trait_interaction opcode — suppress, remove, or copy a trait.

V2: Typed TraitInteraction op.
"""

from ..ctx import Ctx
from ..journal import Mutation, TraitInteractionMutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_trait_interaction(ctx: Ctx, effect) -> list[Mutation]:
    action = _get(effect, "action", "suppress")
    target = _get(effect, "target", "sprite_opp")
    copy_from = _get(effect, "copy_from", None)
    new_ability = _get(effect, "new_ability", None)
    return [TraitInteractionMutation(
        action=action,
        target=target,
        copy_from=copy_from,
        new_ability=new_ability,
    )]
