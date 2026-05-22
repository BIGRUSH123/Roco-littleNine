"""transform opcode — change sprite species and optionally skills.

V2: Typed Transform op.
"""

from ..ctx import Ctx
from ..journal import Mutation, TransformMutation


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def op_transform(ctx: Ctx, effect) -> list[Mutation]:
    species = _get(effect, "species", "")
    skills = _get(effect, "skills", None)
    reset_hp = _get(effect, "reset_hp", False)
    reset_energy = _get(effect, "reset_energy", False)
    return [TransformMutation(
        species=species,
        skills=tuple(skills) if skills else None,
        reset_hp=reset_hp,
        reset_energy=reset_energy,
    )]
