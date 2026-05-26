"""EffectObject — unified effect identity and lifecycle.

EffectObject sits ABOVE the VM layer. It wraps JSON effect dicts with
identity (name, source) and lifecycle (scope, ttl). The VM still receives
raw dicts — EffectObject is purely metadata.

Two subtypes unify the currently-scattered effect systems:
  - ObserverEffect   → replaces ad-hoc Observer identity fields
  - ModifierEffect   → replaces sprite._direct_mod_tracked dict

Both funnel into sprite.active_effects for a single queryable source of truth.
Engine hooks (max_energy, starfall_consume_ratio) are handled as ModifierEffect
with sprite-level attrs read by the consuming property methods.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EffectObject:
    """Base class for all effects — identity + lifecycle.

    Does NOT replace VM ops, Mutations, or Observers.
    It wraps them with metadata so effects can be tracked, queried, and cleaned up.
    """

    name: str                           # "冰封-入场封锁"
    source: str                         # "冰封" (trait or skill name)
    scope: str = "battlefield"          # turn | battlefield | persistent | permanent
    ttl: int = 0                        # remaining turns (0 = infinite)

    def should_clear(self, reason: str) -> bool:
        """Unified lifecycle gate. No more per-system scope logic.

        Reasons:
            reload      — always clear (prevents duplicate registration)
            leave       — sprite switches out
            faint       — sprite is KO'd
            turn_end    — end of turn cleanup
        """
        if reason == "reload":
            return True
        if self.scope == "turn":
            return reason == "turn_end"
        if self.scope == "battlefield":
            return reason in ("leave", "faint")
        if self.scope == "persistent":
            return reason == "faint"
        return False  # permanent — never cleared by engine events


@dataclass
class ObserverEffect(EffectObject):
    """Passive trigger-based effect: condition → sub-effects.

    Carries the same config fields as the Observer dataclass,
    serving as its upstream identity wrapper.
    """

    cond: dict = field(default_factory=dict)
    then: list = field(default_factory=list)
    listen: frozenset = field(default_factory=frozenset)
    threshold: int = 1
    reset_on_fire: bool = True


@dataclass
class ModifierEffect(EffectObject):
    """Direct stat/skill modifier applied immediately on trait load.

    Replaces the ad-hoc _direct_mod_tracked dict on sprite.
    """

    target: str = "sprite_self"         # sprite_self | sprite_opp | skill_off_0
    attr: str = ""                      # power | atk | energy_cost | damage_mult
    value: float = 0.0
    mode: str = "add"                   # set | add | multiply
    skill_where: dict | None = None     # per-skill conditional filter
