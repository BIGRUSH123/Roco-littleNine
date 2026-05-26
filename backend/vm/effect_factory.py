"""EffectFactory — deserialize JSON effect dicts to EffectObject instances.

Pure function: from_dict(dict, source) -> EffectObject | None.
Returns None for opcodes that don't need objectification (raw VM ops like hit, mark, etc.).
"""

from __future__ import annotations

from .effect import EffectObject, ModifierEffect, ObserverEffect


def _normalize_listen(listen) -> frozenset:
    """Accept string, list, or None; always return frozenset."""
    if listen is None:
        return frozenset()
    if isinstance(listen, str):
        return frozenset({listen})
    if isinstance(listen, (list, tuple, set, frozenset)):
        return frozenset(listen)
    return frozenset()


def from_dict(d: dict, *, source: str = "") -> EffectObject | None:
    """Convert a JSON effect dict to the appropriate EffectObject subtype.

    Returns None for opcodes handled directly by the VM (hit, mark, etc.)
    that don't need identity wrapping.
    """
    op = d.get("op", "")
    scope = d.get("scope", "battlefield")
    ttl = d.get("ttl", 0)
    name = d.get("name", "")

    # ── ObserverEffect ──
    if op == "observer":
        return ObserverEffect(
            name=name or source,
            source=source,
            scope=d.get("scope", "persistent"),
            ttl=ttl,
            cond=d.get("cond", {}),
            then=d.get("then", []),
            listen=_normalize_listen(d.get("listen")),
            threshold=d.get("threshold", 1),
            reset_on_fire=d.get("reset_on_fire", True),
        )

    # ── ModifierEffect (power_mod / mult_mod / stat_stage) ──
    if op in ("power_mod", "mult_mod", "stat_stage"):
        attr = d.get("attr", d.get("stat", ""))
        value = d.get("value", d.get("delta", d.get("steps", 0)))
        return ModifierEffect(
            name=name or f"{source}-{attr}",
            source=source,
            scope=scope,
            ttl=ttl,
            target=d.get("target", "sprite_self"),
            attr=attr,
            value=value,
            mode=d.get("mode", "add"),
            skill_where=d.get("skill_where"),
        )

    # ── Raw VM ops (hit, mark, abnormal, heal, etc.) — no wrapper needed ──
    return None
