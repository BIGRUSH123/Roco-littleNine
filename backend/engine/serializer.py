"""backend/engine/serializer.py — 对战状态序列化/反序列化

Unified to_dict()/from_dict() for all stateful objects.
Backtracking and import/export share this layer.
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════
# Effect serialization
# ═══════════════════════════════════════════════════════════════

def effect_to_dict(effect: Any) -> dict:
    """Serialize any EffectObject subclass to dict."""
    from backend.vm.effect import (
        AbnormalEffect, EffectObject, MarkEffect, ModifierEffect,
        ObserverEffect, StatBuffEffect, StateEffect,
    )
    base = {
        "name": effect.name,
        "source": effect.source,
        "scope": effect.scope,
        "ttl": effect.ttl,
        "_type": type(effect).__name__,
    }
    if isinstance(effect, StatBuffEffect):
        base["stat_key"] = effect.stat_key
        base["steps"] = effect.steps
        if effect.display_mult is not None:
            base["display_mult"] = effect.display_mult
        if effect.display_value is not None:
            base["display_value"] = effect.display_value
        if effect.is_inherent:
            base["is_inherent"] = True
    elif isinstance(effect, AbnormalEffect):
        base["stacks"] = effect.stacks
        base["tick_damage_pct"] = effect.tick_damage_pct
        base["tick_element"] = effect.tick_element
        base["decay_on_tick"] = effect.decay_on_tick
        base["max_stacks"] = effect.max_stacks
        base["tick_per_stack"] = effect.tick_per_stack
    elif isinstance(effect, StateEffect):
        base["state_type"] = effect.state_type
        base["params"] = dict(effect.params) if effect.params else {}
    elif isinstance(effect, ModifierEffect):
        base["target"] = effect.target
        base["attr"] = effect.attr
        base["value"] = effect.value
        base["mode"] = effect.mode
        if effect.skill_where:
            base["skill_where"] = effect.skill_where
    elif isinstance(effect, MarkEffect):
        base["stacks"] = effect.stacks
        base["category"] = effect.category
        base["power_bonus"] = effect.power_bonus
        base["damage_mult"] = effect.damage_mult
        base["speed_penalty"] = effect.speed_penalty
        base["energy_mod"] = effect.energy_mod
        base["turn_end_energy"] = effect.turn_end_energy
        base["turn_end_damage_pct"] = effect.turn_end_damage_pct
        base["switch_damage_pct"] = effect.switch_damage_pct
        base["switch_energy_loss"] = effect.switch_energy_loss
        base["starfall_damage"] = effect.starfall_damage
        if effect.condition:
            base["condition"] = effect.condition
    elif isinstance(effect, ObserverEffect):
        base["cond"] = effect.cond
        base["then"] = effect.then
        base["listen"] = list(effect.listen) if effect.listen else []
        base["threshold"] = effect.threshold
        base["reset_on_fire"] = effect.reset_on_fire
    return base


def effect_from_dict(d: dict) -> Any:
    """Deserialize a dict back to an EffectObject subclass."""
    from backend.vm.effect import (
        AbnormalEffect, MarkEffect, ModifierEffect,
        ObserverEffect, StatBuffEffect, StateEffect,
    )
    _type = d.get("_type", "")
    if _type == "StatBuffEffect":
        return StatBuffEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), stat_key=d.get("stat_key", ""),
            steps=d.get("steps", 0),
            display_mult=d.get("display_mult"),
            display_value=d.get("display_value"),
            is_inherent=d.get("is_inherent", False),
        )
    if _type == "AbnormalEffect":
        return AbnormalEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), stacks=d.get("stacks", 0),
            tick_damage_pct=d.get("tick_damage_pct", 0.0),
            tick_element=d.get("tick_element", ""),
            decay_on_tick=d.get("decay_on_tick", False),
            max_stacks=d.get("max_stacks", 0),
            tick_per_stack=d.get("tick_per_stack", True),
        )
    if _type == "StateEffect":
        return StateEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), state_type=d.get("state_type", ""),
            params=d.get("params", {}),
        )
    if _type == "ModifierEffect":
        return ModifierEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), target=d.get("target", "sprite_self"),
            attr=d.get("attr", ""), value=d.get("value", 0.0),
            mode=d.get("mode", "add"), skill_where=d.get("skill_where"),
        )
    if _type == "MarkEffect":
        return MarkEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "persistent"),
            ttl=d.get("ttl", 0), stacks=d.get("stacks", 0),
            category=d.get("category", "negative"),
            power_bonus=d.get("power_bonus", 0),
            damage_mult=d.get("damage_mult", 0.0),
            speed_penalty=d.get("speed_penalty", 0),
            energy_mod=d.get("energy_mod", 0),
            turn_end_energy=d.get("turn_end_energy", 0),
            turn_end_damage_pct=d.get("turn_end_damage_pct", 0.0),
            switch_damage_pct=d.get("switch_damage_pct", 0.0),
            switch_energy_loss=d.get("switch_energy_loss", 0),
            starfall_damage=d.get("starfall_damage", 0),
            condition=d.get("condition", ""),
        )
    if _type == "ObserverEffect":
        return ObserverEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), cond=d.get("cond", {}),
            then=d.get("then", []), listen=frozenset(d.get("listen", [])),
            threshold=d.get("threshold", 1),
            reset_on_fire=d.get("reset_on_fire", True),
        )
    raise ValueError(f"Unknown effect type: {_type}")


# ═══════════════════════════════════════════════════════════════
# BattleSkill serialization
# ═══════════════════════════════════════════════════════════════

def battle_skill_to_dict(bs) -> dict:
    """Serialize BattleSkill — only stores base skill name as reference."""
    return {
        "base_name": bs.base.name if bs.base else "",
        "_modifiers": dict(bs._modifiers),
        "sealed": bs.sealed,
        "_transmission": bs._transmission,
        "_burst_effects": list(bs._burst_effects),
        "is_temporary": bs.is_temporary,
        "cooldown": bs.cooldown,
        "next_attack_mult": bs.next_attack_mult,
        "_element_override": bs._element_override,
        "_mech_energy_reduction": bs._mech_energy_reduction,
    }


def battle_skill_from_dict(d: dict, skill_loader) -> Any:
    """Reconstruct BattleSkill from dict.

    skill_loader: callable(name) -> BattleSkill — from SimFactory._build_skill_list
    """
    base_name = d.get("base_name", "")
    if not base_name or skill_loader is None:
        return None
    skills = skill_loader([base_name])
    if not skills:
        return None
    bs = skills[0]
    bs._modifiers = dict(d.get("_modifiers", {}))
    bs.sealed = d.get("sealed", False)
    bs._transmission = d.get("_transmission", 0)
    bs._burst_effects = list(d.get("_burst_effects", []))
    bs.is_temporary = d.get("is_temporary", False)
    bs.cooldown = d.get("cooldown", 0)
    bs.next_attack_mult = d.get("next_attack_mult", 1.0)
    bs._element_override = d.get("_element_override", "")
    bs._mech_energy_reduction = d.get("_mech_energy_reduction", 0)
    return bs
