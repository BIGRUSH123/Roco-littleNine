"""EffectFactory — deserialize JSON effect dicts to EffectObject instances.

Pure function: from_dict(dict, source) -> EffectObject | None.
Returns None for opcodes that don't need objectification (raw VM ops like hit, mark, etc.).
"""

from __future__ import annotations

from .effect import AbnormalEffect, EffectObject, MarkEffect, ModifierEffect, ObserverEffect


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

    # ── AbnormalEffect ──
    if op == "abnormal":
        from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
        template = ABNORMAL_TEMPLATES.get(name, ABNORMAL_TEMPLATES.get(d.get("template", "")))
        if template is not None:
            return AbnormalEffect(
                name=name or template.name,
                source=source,
                scope=d.get("scope", template.scope),
                ttl=d.get("ttl", template.ttl),
                stacks=d.get("stacks", 0),
                tick_damage_pct=d.get("tick_damage_pct", template.tick_damage_pct),
                tick_element=d.get("tick_element", template.tick_element),
                decay_on_tick=d.get("decay_on_tick", template.decay_on_tick),
                max_stacks=d.get("max_stacks", template.max_stacks),
                tick_per_stack=d.get("tick_per_stack", template.tick_per_stack),
            )
        return AbnormalEffect(
            name=name or source,
            source=source,
            scope=scope,
            ttl=ttl,
            stacks=d.get("stacks", 0),
        )

    # ── MarkEffect ──
    if op == "mark":
        from backend.engine.mark_config import MARK_TEMPLATES
        template = MARK_TEMPLATES.get(name)
        if template is not None:
            return MarkEffect(
                name=name or template.name,
                source=source,
                scope=d.get("scope", template.scope),
                ttl=d.get("ttl", template.ttl),
                stacks=d.get("stacks", 0),
                category=d.get("category", template.category),
                power_bonus=d.get("power_bonus", template.power_bonus),
                damage_mult=d.get("damage_mult", template.damage_mult),
                speed_penalty=d.get("speed_penalty", template.speed_penalty),
                energy_mod=d.get("energy_mod", template.energy_mod),
                turn_end_energy=d.get("turn_end_energy", template.turn_end_energy),
                turn_end_damage_pct=d.get("turn_end_damage_pct", template.turn_end_damage_pct),
                switch_damage_pct=d.get("switch_damage_pct", template.switch_damage_pct),
                switch_energy_loss=d.get("switch_energy_loss", template.switch_energy_loss),
                starfall_damage=d.get("starfall_damage", template.starfall_damage),
                condition=d.get("condition", template.condition),
            )
        return MarkEffect(
            name=name or source,
            source=source,
            scope=scope,
            ttl=ttl,
            stacks=d.get("stacks", 0),
            category=d.get("category", "negative"),
        )

    # ── Raw VM ops (hit, mark, abnormal, heal, etc.) — no wrapper needed ──
    return None
