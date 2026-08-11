"""backend/engine/serializer.py — 对战状态序列化/反序列化

Unified to_dict()/from_dict() for all stateful objects.
Backtracking and import/export share this layer.
"""

from __future__ import annotations

from copy import deepcopy
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


# ═══════════════════════════════════════════════════════════════
# Sprite serialization
# ═══════════════════════════════════════════════════════════════

def species_ref_to_dict(species) -> dict | None:
    """Serialize a species reference without embedding the database object."""
    if species is None:
        return None
    return {
        "name": species.name,
        "number": species.number,
        "form": species.form,
    }


def species_ref_from_dict(d: dict | None, species_db) -> Any:
    """Resolve a serialized species reference through the supplied database."""
    if not d:
        return None
    species = species_db(d["name"], d.get("form", ""))
    if species is None:
        raise ValueError(f"Species not found: {d['name']!r}")
    return species


def sprite_to_dict(sprite) -> dict:
    """Serialize Sprite — species as name/number/form identifier."""
    return {
        "species_name": sprite.species.name,
        "species_number": sprite.species.number,
        "species_form": sprite.species.form,
        "bloodline": sprite.bloodline,
        "bloodline_skills": dict(sprite.bloodline_skills),
        "initial_stats": dict(sprite.initial_stats),
        "nature": sprite.nature,
        "iv": dict(sprite.iv),
        "current_hp": sprite.current_hp,
        "max_hp": sprite.max_hp,
        "energy": sprite.energy,
        "active_effects": [effect_to_dict(e) for e in sprite.active_effects],
        "entry_turn": sprite.entry_turn,
        "counters": dict(sprite.counters),
        "first_action": sprite.first_action,
        "first_action_battle": sprite.first_action_battle,
        "pending_return": sprite.pending_return,
        "locked_turns": sprite.locked_turns,
        "interrupted": sprite.interrupted,
        "extra_skill_use": sprite.extra_skill_use,
        "_charging": sprite._charging,
        "_charged_skill_index": sprite._charged_skill_index,
        "_charged_skill_ref_index": next(
            (
                i for i, skill in enumerate(sprite.skills or [])
                if skill is sprite._charged_skill_ref
            ),
            -1,
        ),
        "_last_abnormal_dmg": dict(sprite._last_abnormal_dmg),
        "_moe_chain": [
            species_ref_to_dict(species) for species in sprite._moe_chain
        ],
        "_moe_position": sprite._moe_position,
        "_moe_origin": species_ref_to_dict(sprite._moe_origin),
        "_moe_origin_skills": [
            battle_skill_to_dict(skill) for skill in sprite._moe_origin_skills
        ],
        "_modifiers": dict(sprite._modifiers),
        "_mod_scopes": dict(sprite._mod_scopes),
        "_pending_effects": [
            (effect_to_dict(e), delay) for e, delay in sprite._pending_effects
        ],
        "_pending_modifiers": [
            {
                "stat": m.stat, "value": m.value, "mode": m.mode,
                "target": m.target, "scope": getattr(m, 'scope', 'turn'),
                "source": getattr(m, 'source', ''),
                "on_next": getattr(m, 'on_next', True),
                "skill_where": getattr(m, 'skill_where', None),
                "skill_filter": getattr(m, 'skill_filter', None),
            }
            for m in sprite._pending_modifiers
        ],
        "_trait_suppressed": sprite._trait_suppressed,
        "_trait_direct_effects": deepcopy(
            getattr(sprite, "_trait_direct_effects", None)
        ),
        "_direct_mod_tracked": deepcopy(
            getattr(sprite, "_direct_mod_tracked", None)
        ),
        "skills": [battle_skill_to_dict(bs) for bs in (sprite.skills or [])],
    }


def sprite_from_dict(d: dict, species_db, skill_loader) -> Any:
    """Reconstruct Sprite from dict."""
    from backend.common.models import SpeciesStats
    from backend.sim.sprite import Sprite

    species = species_db(d["species_name"], d.get("species_form", ""))
    if species is None:
        raise ValueError(f"Species not found: {d['species_name']!r}")

    sprite = Sprite(
        species=species,
        bloodline=d.get("bloodline", ""),
        bloodline_skills=dict(d.get("bloodline_skills", {})),
        initial_stats=dict(d.get("initial_stats", {})),
        current_hp=d.get("current_hp", 0),
        max_hp=d.get("max_hp", 0),
        energy=d.get("energy", 10),
        nature=d.get("nature"),
        iv=dict(d.get("iv", {})),
    )
    sprite.entry_turn = d.get("entry_turn", 0)
    sprite.counters = dict(d.get("counters", {}))
    sprite.first_action = d.get("first_action", True)
    sprite.first_action_battle = d.get("first_action_battle", True)
    sprite.pending_return = d.get("pending_return", False)
    sprite.locked_turns = d.get("locked_turns", 0)
    sprite.interrupted = d.get("interrupted", False)
    sprite.extra_skill_use = d.get("extra_skill_use", False)
    sprite._charging = d.get("_charging", False)
    sprite._charged_skill_index = d.get("_charged_skill_index", -1)
    sprite._last_abnormal_dmg = dict(d.get("_last_abnormal_dmg", {}))
    sprite._modifiers = dict(d.get("_modifiers", {}))
    sprite._mod_scopes = dict(d.get("_mod_scopes", {}))

    sprite.active_effects = [effect_from_dict(e) for e in d.get("active_effects", [])]

    sprite._pending_effects = [
        (effect_from_dict(e), delay)
        for e, delay in d.get("_pending_effects", [])
    ]

    from backend.vm.journal import ModifierInjection
    sprite._pending_modifiers = [
        ModifierInjection(
            stat=m["stat"], value=m["value"], mode=m["mode"],
            target=m.get("target", "sprite_self"),
            scope=m.get("scope", "turn"),
            source=m.get("source", ""),
            on_next=m.get("on_next", True),
            skill_where=m.get("skill_where"),
            skill_filter=m.get("skill_filter"),
        )
        for m in d.get("_pending_modifiers", [])
    ]

    sprite._trait_suppressed = d.get("_trait_suppressed", False)
    sprite._trait_direct_effects = deepcopy(d.get("_trait_direct_effects"))
    sprite._direct_mod_tracked = deepcopy(d.get("_direct_mod_tracked"))

    if skill_loader is not None:
        sprite.skills = [
            bs for bs in (
                battle_skill_from_dict(sd, skill_loader) for sd in d.get("skills", [])
            )
            if bs is not None
        ]
    else:
        sprite.skills = []

    charged_ref_index = d.get("_charged_skill_ref_index", -1)
    sprite._charged_skill_ref = (
        sprite.skills[charged_ref_index]
        if 0 <= charged_ref_index < len(sprite.skills)
        else None
    )
    sprite._moe_chain = [
        species_ref_from_dict(ref, species_db)
        for ref in d.get("_moe_chain", [])
    ]
    sprite._moe_position = d.get("_moe_position", 0)
    sprite._moe_origin = species_ref_from_dict(d.get("_moe_origin"), species_db)
    if skill_loader is not None:
        sprite._moe_origin_skills = [
            skill for skill in (
                battle_skill_from_dict(sd, skill_loader)
                for sd in d.get("_moe_origin_skills", [])
            )
            if skill is not None
        ]
    else:
        sprite._moe_origin_skills = []

    return sprite


# ═══════════════════════════════════════════════════════════════
# Player serialization
# ═══════════════════════════════════════════════════════════════

def player_to_dict(player) -> dict:
    """Serialize Player — sprites as list of dicts."""
    return {
        "name": player.name,
        "lives": player.lives,
        "active_index": player.active_index,
        "devotion": dict(player.devotion),
        "team": [sprite_to_dict(s) for s in player.team],
        "item": {
            "name": player.item.name,
            "max_uses": player.item.max_uses,
            "cooldown_turns": player.item.cooldown_turns,
            "uses": player.item.uses,
            "last_use_turn": player.item.last_use_turn,
        } if player.item else None,
    }


def player_from_dict(d: dict, species_db, skill_loader) -> Any:
    """Reconstruct Player from dict."""
    from backend.sim.player import Item, Player, PlayStyle

    team = [sprite_from_dict(sd, species_db, skill_loader) for sd in d.get("team", [])]
    item = None
    if d.get("item"):
        idata = d["item"]
        item = Item(
            name=idata["name"], max_uses=idata["max_uses"],
            cooldown_turns=idata.get("cooldown_turns", 0),
            uses=idata.get("uses", 0),
            last_use_turn=idata.get("last_use_turn", 0),
        )
    return Player(
        name=d["name"], team=team, style=PlayStyle(),
        lives=d.get("lives", 4), active_index=d.get("active_index", 0),
        item=item, devotion=dict(d.get("devotion", {})),
    )


# ═══════════════════════════════════════════════════════════════
# GlobalEffects serialization
# ═══════════════════════════════════════════════════════════════

def globals_to_dict(g) -> dict:
    """Serialize GlobalEffects."""
    return {
        "weather": g.weather,
        "weather_turns": g.weather_turns,
        "marks_a": [effect_to_dict(m) for m in g.mark_effects.get("A", [])],
        "marks_b": [effect_to_dict(m) for m in g.mark_effects.get("B", [])],
    }


def globals_from_dict(d: dict) -> Any:
    """Reconstruct GlobalEffects from dict."""
    from backend.sim.globals import GlobalEffects
    g = GlobalEffects()
    g.weather = d.get("weather", "")
    g.weather_turns = d.get("weather_turns", 0)
    g.mark_effects["A"] = [effect_from_dict(m) for m in d.get("marks_a", [])]
    g.mark_effects["B"] = [effect_from_dict(m) for m in d.get("marks_b", [])]
    return g


# ═══════════════════════════════════════════════════════════════
# VM engine state serialization
# ═══════════════════════════════════════════════════════════════

def vm_state_to_dict(vm_engine) -> dict:
    """Extract VM engine mutable state for serialization."""
    return {
        "counter_values": dict(vm_engine._counter_values),
        "burst_effects": {
            team: [(name, list(effects)) for name, effects in items]
            for team, items in vm_engine._burst_effects.items()
        },
        "burst_names": {
            team: list(names) for team, names in vm_engine._burst_names.items()
        },
        "skill_history": {
            str(sprite_id): [
                (name, list(effects), dict(tags))
                for name, effects, tags in history
            ]
            for sprite_id, history in vm_engine._skill_history.items()
        },
    }


def vm_state_restore(vm_engine, state: dict) -> None:
    """Restore VM engine mutable state from dict (in-place mutation)."""
    vm_engine._counter_values = dict(state.get("counter_values", {}))
    vm_engine._burst_effects = {
        team: [(name, list(effects)) for name, effects in items]
        for team, items in state.get("burst_effects", {}).items()
    }
    vm_engine._burst_names = {
        team: set(names)
        for team, names in state.get("burst_names", {}).items()
    }
    vm_engine._skill_history = {
        int(sprite_id): [
            (name, list(effects), dict(tags))
            for name, effects, tags in history
        ]
        for sprite_id, history in state.get("skill_history", {}).items()
    }


def observer_counters_to_dict(battle) -> list[dict]:
    """Serialize non-zero Observer counters using stable sprite positions."""
    records: list[dict] = []
    registry = battle._vm_engine.registry
    for team, player in (("A", battle.player_a), ("B", battle.player_b)):
        for sprite_index, sprite in enumerate(player.team):
            observers = [
                obs for obs in registry._observers
                if obs.owner_sprite_id == id(sprite)
            ]
            for observer_index, obs in enumerate(observers):
                if obs._hit_count <= 0:
                    continue
                records.append({
                    "team": team,
                    "sprite_index": sprite_index,
                    "observer_index": observer_index,
                    "source": obs.source,
                    "name": obs.name,
                    "listen": sorted(obs.listen),
                    "threshold": obs.threshold,
                    "hit_count": obs._hit_count,
                })
    return records


def observer_counters_restore(battle, records: list[dict]) -> None:
    """Restore Observer counters after traits have been re-registered."""
    registry = battle._vm_engine.registry
    players = {"A": battle.player_a, "B": battle.player_b}
    for record in records:
        player = players.get(record.get("team"))
        sprite_index = record.get("sprite_index", -1)
        if player is None or not 0 <= sprite_index < len(player.team):
            continue
        sprite = player.team[sprite_index]
        observers = [
            obs for obs in registry._observers
            if obs.owner_sprite_id == id(sprite)
        ]
        observer_index = record.get("observer_index", -1)
        if not 0 <= observer_index < len(observers):
            continue
        obs = observers[observer_index]
        signature_matches = (
            obs.source == record.get("source", "")
            and obs.name == record.get("name", "")
            and sorted(obs.listen) == record.get("listen", [])
            and obs.threshold == record.get("threshold", 1)
        )
        if signature_matches:
            obs._hit_count = max(0, int(record.get("hit_count", 0)))


# ═══════════════════════════════════════════════════════════════
# RoundRecord serialization
# ═══════════════════════════════════════════════════════════════

def round_record_to_dict(rec) -> dict:
    """Serialize RoundRecord to dict (supplements existing to_message())."""
    return {
        "turn": rec.turn,
        "weather": rec.weather,
        "sprite_a": rec.sprite_a,
        "sprite_b": rec.sprite_b,
        "first_team": rec.first_team,
        "turn_start_events": list(rec.turn_start_events),
        "action_a": {
            "team": rec.action_a.team,
            "actor": rec.action_a.actor,
            "kind": rec.action_a.kind,
            "skill_name": rec.action_a.skill_name,
            "events": list(rec.action_a.events),
        } if rec.action_a else None,
        "action_b": {
            "team": rec.action_b.team,
            "actor": rec.action_b.actor,
            "kind": rec.action_b.kind,
            "skill_name": rec.action_b.skill_name,
            "events": list(rec.action_b.events),
        } if rec.action_b else None,
        "turn_end_events": list(rec.turn_end_events),
    }


def round_record_from_dict(d: dict) -> Any:
    """Reconstruct RoundRecord from dict."""
    from backend.sim.round_record import ActionRecord, RoundRecord
    rec = RoundRecord(
        turn=d["turn"], weather=d.get("weather", ""),
        sprite_a=d.get("sprite_a", ""), sprite_b=d.get("sprite_b", ""),
        first_team=d.get("first_team", ""),
    )
    rec.turn_start_events = list(d.get("turn_start_events", []))
    rec.turn_end_events = list(d.get("turn_end_events", []))
    if d.get("action_a"):
        a = d["action_a"]
        rec.action_a = ActionRecord(
            team=a["team"], actor=a["actor"], kind=a["kind"],
            skill_name=a.get("skill_name", ""), events=list(a.get("events", [])),
        )
    if d.get("action_b"):
        b = d["action_b"]
        rec.action_b = ActionRecord(
            team=b["team"], actor=b["actor"], kind=b["kind"],
            skill_name=b.get("skill_name", ""), events=list(b.get("events", [])),
        )
    return rec


# ═══════════════════════════════════════════════════════════════
# Full battle serialization
# ═══════════════════════════════════════════════════════════════

def battle_to_dict(battle) -> dict:
    """Serialize full Battle state."""
    return {
        "version": "1.0",
        "type": "match",
        "turn": battle.turn,
        "winner": battle.winner,
        "player_a": player_to_dict(battle.player_a),
        "player_b": player_to_dict(battle.player_b),
        "globals": globals_to_dict(battle.globals),
        "weather": battle.globals.weather,
        "log": [round_record_to_dict(r) for r in battle.log],
        "vm_state": vm_state_to_dict(battle._vm_engine),
        "observer_counters": observer_counters_to_dict(battle),
        "team_counters": {
            "A": dict(battle.team_counters.get("A", {})),
            "B": dict(battle.team_counters.get("B", {})),
        },
        "pending_effects": {
            team: [effect_to_dict(e) for e in effects]
            for team, effects in battle.pending_effects.items()
        },
        "scheduled_effects": [
            {
                "turn": se["turn"], "phase": se["phase"],
                "effects": list(se["effects"]),
                "source_name": se["source"].name if se.get("source") else "",
                "ctx_snapshot": dict(se.get("ctx_snapshot", {})),
            }
            for se in battle.scheduled_effects
        ],
    }


def battle_from_dict(d: dict, species_db, skill_loader) -> Any:
    """Reconstruct full Battle from dict."""
    from backend.sim.battle import Battle

    player_a = player_from_dict(d["player_a"], species_db.get, skill_loader)
    player_b = player_from_dict(d["player_b"], species_db.get, skill_loader)

    battle = Battle(
        player_a=player_a, player_b=player_b,
        weather=d.get("weather", ""),
        verbose=False,
        initialize_entries=False,
    )
    battle.turn = d.get("turn", 0)
    battle.winner = d.get("winner")

    gd = d.get("globals", {})
    from backend.sim.globals import GlobalEffects
    battle.globals = globals_from_dict(gd)

    battle.log = [round_record_from_dict(r) for r in d.get("log", [])]

    vm_state_restore(battle._vm_engine, d.get("vm_state", {}))
    observer_counters_restore(battle, d.get("observer_counters", []))

    battle.team_counters = {
        "A": dict(d.get("team_counters", {}).get("A", {})),
        "B": dict(d.get("team_counters", {}).get("B", {})),
    }

    battle.pending_effects = {
        team: [effect_from_dict(e) for e in effects]
        for team, effects in d.get("pending_effects", {}).items()
    }

    battle.scheduled_effects = []
    for se in d.get("scheduled_effects", []):
        source_name = se.get("source_name", "")
        source_sprite = None
        for s in player_a.team + player_b.team:
            if s.name == source_name:
                source_sprite = s
                break
        battle.scheduled_effects.append({
            "turn": se["turn"], "phase": se["phase"],
            "effects": list(se.get("effects", [])),
            "source": source_sprite,
            "ctx_snapshot": dict(se.get("ctx_snapshot", {})),
        })

    battle.species_db = species_db
    battle.skill_loader = skill_loader

    return battle
