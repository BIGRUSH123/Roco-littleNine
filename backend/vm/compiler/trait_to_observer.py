"""Trait → Observer converter (Phase C3).

Converts old DataDrivenTrait JSON triggers into Observer objects that use
Skill IR conditions and opcodes, enabling the unified Ctx-based execution path.

Usage:
    converter = TraitToObserver()
    observers = converter.convert(trait_json, trait_name)
    for obs in observers:
        registry.register(obs)
"""

from __future__ import annotations

from backend.engine.observer import Observer
from backend.vm.ir_trait import FnCond

# ── Hook → Observer trigger point mapping ──

_TRIGGER_MAP: dict[str, str] = {
    # Lifecycle
    "entry": "post_entry",
    "leave": "post_leave",
    "enemy_leave": "post_enemy_leave",
    # Turn phases
    "turn_start": "pre_calc",
    "turn_end": "turn_end",
    # Skill pipeline
    "modifier": "pre_modifier",
    "defend": "pre_defend",
    "skill_use": "post_skill",
    # Combat events
    "counter": "post_counter",
    "counter_success": "post_counter",
    "damage": "post_damage",
    "take_damage": "post_damage",
    "ko": "post_ko",
    "ko_enemy": "post_ko",
    "faint": "post_ko",
    # Abnormal / effect events
    "abnormal_tick": "post_abnormal_tick",
    "abnormal_change": "post_abnormal_change",
    "abnormal_apply": "post_abnormal_apply",
    "inflict": "post_abnormal_apply",
    "gain_effect": "post_positive_change",
    # Resource events
    "energy_change": "post_energy_change",
    "positive_change": "post_positive_change",
    # Switch
    "switch": "post_switch",
}


class TraitToObserver:
    """Convert legacy trait JSON triggers to Skill-IR-based Observers."""

    def convert(self, trait_json: dict, trait_name: str = "") -> list[Observer]:
        """Convert a full trait JSON definition to Observer list.

        trait_json may have:
          - "triggers": [{"on": "...", "condition": {...}, "effects": [...]}, ...]
          - "aura": {"name": "...", "effects": [...]}

        Returns list of Observer ready for registry.register().
        """
        observers: list[Observer] = []
        triggers = trait_json.get("triggers", [])

        # Expand aura into entry+leave triggers
        aura = trait_json.get("aura")
        if aura:
            triggers = list(triggers)
            triggers.extend(self._expand_aura(aura, trait_json, trait_name))

        for trigger in triggers:
            obs = self.convert_trigger(trigger, trait_name)
            if obs:
                observers.append(obs)

        return observers

    def convert_trigger(self, trigger: dict, trait_name: str = "") -> Observer | None:
        """Convert a single trigger dict to an Observer.

        Returns None for engine-level triggers (battleskill_mut / use_modifiers
        only, no effects/pending_effects) that are handled by the old modifier pipeline.
        """
        on_hook = trigger.get("on", "")
        trigger_point = _TRIGGER_MAP.get(on_hook)
        if not trigger_point:
            return None

        effects = list(trigger.get("effects", []))
        pending = trigger.get("pending_effects", [])
        if pending:
            effects.extend(pending)

        has_battleskill = bool(trigger.get("battleskill_mut"))
        has_use_modifiers = bool(trigger.get("use_modifiers"))

        # Engine-only triggers: skip (handled by old modifier pipeline)
        if not effects and (has_battleskill or has_use_modifiers):
            return None

        cond = self._convert_condition(trigger.get("condition"))
        then = self._convert_effects(effects, trigger)
        scope = self._map_scope(trigger)

        return Observer(
            cond=cond,
            then=then,
            scope=scope,
            name=trait_name,
            source=trait_name,
        )

    # ── Condition conversion ──

    def _convert_condition(self, condition: dict | None) -> dict:
        """Convert a trait condition dict to Skill IR condition dict."""
        if not condition:
            return {"cond": "trait_path", "path": "self.energy", "op": "ge", "value": 0}  # always-true

        kind = condition.get("kind", "path")

        if kind == "and":
            return {
                "cond": "and",
                "conditions": [self._convert_condition(c) for c in condition.get("conditions", [])],
            }
        if kind == "or":
            return {
                "cond": "or",
                "conditions": [self._convert_condition(c) for c in condition.get("conditions", [])],
            }
        if kind == "not":
            return {
                "cond": "not",
                "condition": self._convert_condition(condition.get("condition", {})),
            }
        if kind == "fn":
            return FnCond(name=condition.get("name", ""))

        # path-based condition
        path = condition.get("path", "")
        op = condition.get("op", "eq")
        value = condition.get("value")

        # Handle effects[name=X].exists → use "have" condition
        if ".effects[" in path and ".exists" in path:
            import re
            m = re.match(r'(self|target)\.effects\[name=([^\]]+)\]\.exists', path)
            if m:
                of = "sprite_self" if m.group(1) == "self" else "sprite_opp"
                return {
                    "cond": "have",
                    "what": "abnormal",
                    "of": of,
                    "name": m.group(2),
                }

        return {
            "cond": "trait_path",
            "path": path,
            "op": op,
            "value": value,
        }

    # ── Effect conversion ──

    def _convert_effects(self, effects: list[dict], trigger: dict) -> list[dict]:
        """Convert TraitEffect dicts to SkillIROp dicts."""
        result: list[dict] = []
        for e in effects:
            converted = self._convert_effect(e, trigger)
            if converted is not None:
                if isinstance(converted, list):
                    result.extend(converted)
                else:
                    result.append(converted)
        return result

    def _convert_effect(self, effect: dict, trigger: dict) -> dict | list[dict] | None:
        """Convert a single TraitEffect to SkillIROp dict(s)."""
        kind = effect.get("kind", "stat")

        if kind == "stat":
            return self._convert_stat_effect(effect)
        if kind == "abnormal":
            return self._convert_abnormal_effect(effect)
        if kind == "mark":
            return self._convert_mark_effect(effect)
        if kind == "weather":
            return self._convert_weather_effect(effect)
        if kind == "special":
            return self._convert_special_effect(effect)
        if kind == "remove_effect":
            return self._convert_remove_effect(effect)
        if kind == "team_counter":
            return self._convert_team_counter_effect(effect)
        if kind == "schedule":
            return self._convert_schedule_effect(effect)
        if kind == "inherit_effects":
            return self._convert_inherit_effects(effect)
        if kind == "lives":
            return self._convert_lives_effect(effect)
        if kind == "transform":
            return self._convert_transform_effect(effect)
        if kind == "trait_interaction":
            return self._convert_trait_interaction_effect(effect)
        if kind == "state":
            return self._convert_state_effect(effect)
        if kind == "mutate_effect":
            return self._convert_mutate_effect(effect)

        return None  # unknown kind

    def _convert_stat_effect(self, e: dict) -> dict:
        target = self._map_target(e.get("target", "self"))
        stat = e.get("stat", "")
        mode = e.get("mode", "set")
        scope = e.get("scope", "battlefield")

        result: dict = {
            "op": "mod",
            "target": target,
            "stat": stat,
            "mode": mode,
            "scope": scope,
        }

        if "steps" in e:
            result["steps"] = e["steps"]
        if "value" in e:
            result["value"] = e["value"]
        if "source" in e:
            result["source"] = e["source"]
        if "name" in e:
            result["name"] = e["name"]
        if "per_hit" in e:
            result["per_hit"] = e["per_hit"]
        if "element" in e:
            result["element"] = e["element"]
        if "per_element" in e:
            result["per_element"] = e["per_element"]
        if "on_next" in e:
            result["on_next"] = e["on_next"]

        return result

    def _convert_abnormal_effect(self, e: dict) -> dict:
        target = self._map_target(e.get("target", "opp"))
        return {
            "op": "abnormal",
            "target": target,
            "name": e.get("name", ""),
            "stacks": e.get("stacks", 1),
            "scope": e.get("scope", "battlefield"),
        }

    def _convert_mark_effect(self, e: dict) -> dict:
        target = self._map_target(e.get("target", "opp"))
        return {
            "op": "mark",
            "target": target,
            "name": e.get("name", ""),
            "stacks": e.get("stacks", 1),
        }

    def _convert_weather_effect(self, e: dict) -> dict:
        return {
            "op": "weather",
            "weather": e.get("weather", ""),
            "turns": e.get("turns", 8),
        }

    def _convert_special_effect(self, e: dict) -> dict | list[dict] | None:
        name = e.get("name", "")
        target = self._map_target(e.get("target", "self"))

        if name == "heal":
            value = e.get("value", 0)
            return {"op": "mod", "target": target, "stat": "hp", "value": value, "mode": "set"}
        if name == "direct_heal":
            amount = e.get("amount", 0)
            return {"op": "mod", "target": target, "stat": "hp", "value": amount, "mode": "set"}
        if name == "gain_energy":
            amount = e.get("amount", 1)
            return {"op": "mod", "target": target, "stat": "energy", "value": amount, "mode": "set"}
        if name == "lose_energy":
            amount = e.get("amount", 1)
            if isinstance(amount, str):
                return {"op": "mod", "target": target, "stat": "energy", "value": amount, "mode": "set", "negative": True}
            return {"op": "mod", "target": target, "stat": "energy", "value": -amount, "mode": "set"}
        if name == "energy_set":
            amount = e.get("amount", 0)
            return {"op": "mod", "target": target, "stat": "energy", "value": amount, "mode": "set"}
        if name == "steal_energy":
            # "steal" op is sprite-scoped, use mod on both
            amt = e.get("amount", 1)
            return [
                {"op": "mod", "target": target, "stat": "energy", "value": -amt, "mode": "set"},
                {"op": "mod", "target": "sprite_self", "stat": "energy", "value": amt, "mode": "set"},
            ]
        if name == "take_damage":
            damage_target = e.get("damage_target", "")
            actual_target = self._map_target(damage_target) if damage_target else target
            amount = e.get("amount", 0) or e.get("value", 0)
            if isinstance(amount, str):
                return {"op": "mod", "target": actual_target, "stat": "hp", "value": amount, "mode": "set", "negative": True}
            return {"op": "mod", "target": actual_target, "stat": "hp", "value": -amount, "mode": "set"}
        if name == "dispel_mark":
            dispel_target = self._map_target(e.get("target_team", "own"))
            return {"op": "mark", "target": dispel_target, "name": e.get("mark_name", ""), "action": "dispel", "stacks": e.get("count", 1)}
        if name == "steal_mark":
            return {"op": "mark", "target": "sprite_self", "name": e.get("mark_name", ""), "action": "steal", "stacks": e.get("count", 1)}
        if name == "convert_mark":
            return {"op": "mark", "target": "sprite_self", "name": e.get("mark_name", ""), "action": "convert", "ratio": e.get("ratio", 1.0), "source_abnormal": e.get("mark_name", "")}
        if name == "inherit_effects":
            return {"op": "inherit_effects", "source": "self", "inherit_target": e.get("target", "enemy_new"), "scope": e.get("scope", "battlefield"), "via_pending": e.get("via_pending", False)}
        if name == "team_counter_add":
            return {"op": "team_counter_write", "target": e.get("target_team", "own"), "key": e.get("key", ""), "delta": e.get("amount", 1)}
        if name == "lives_delta":
            return {"op": "lives_change", "target_team": e.get("target_team", "own"), "delta": e.get("amount", 0)}
        if name == "lives_add":
            return {"op": "lives_change", "target_team": e.get("target_team", "own"), "delta": e.get("amount", 1)}

        return None

    def _convert_remove_effect(self, e: dict) -> dict:
        source = e.get("source", "")
        target = self._map_target(e.get("target", "self"))
        return {"op": "dispel", "target": target, "what": "positive", "source": source}

    def _convert_team_counter_effect(self, e: dict) -> dict:
        return {
            "op": "team_counter_write",
            "target": e.get("target_team", "own"),
            "key": e.get("key", ""),
            "delta": e.get("delta", 1),
        }

    def _convert_schedule_effect(self, e: dict) -> dict:
        return {
            "op": "schedule",
            "delay_turns": e.get("turns", 1),
            "phase": e.get("phase", "start"),
            "effects": self._convert_effects(e.get("effects", []), {}),
        }

    def _convert_inherit_effects(self, e: dict) -> dict:
        return {
            "op": "inherit_effects",
            "source": e.get("source_sprite", "self"),
            "inherit_target": e.get("target", "enemy_new"),
            "scope": e.get("scope", "battlefield"),
            "via_pending": e.get("via_pending", False),
        }

    def _convert_lives_effect(self, e: dict) -> dict:
        return {
            "op": "lives_change",
            "target_team": e.get("target_team", "own"),
            "delta": e.get("delta", 0),
        }

    def _convert_transform_effect(self, e: dict) -> dict:
        return {
            "op": "transform",
            "species": e.get("species", ""),
            "skills": tuple(e.get("skills", [])) if e.get("skills") else None,
            "reset_hp": e.get("reset_hp", False),
            "reset_energy": e.get("reset_energy", False),
        }

    def _convert_trait_interaction_effect(self, e: dict) -> dict:
        return {
            "op": "trait_interaction",
            "action": e.get("action", "suppress"),
            "target": self._map_target(e.get("target", "sprite_opp")),
            "copy_from": e.get("copy_from"),
            "new_ability": e.get("new_ability"),
        }

    def _convert_state_effect(self, e: dict) -> dict:
        """Convert a 'state' kind effect (pending state application) to abnormal op."""
        target = self._map_target(e.get("target", "self"))
        return {
            "op": "abnormal",
            "target": target,
            "name": e.get("name", ""),
            "stacks": e.get("stacks", 1),
            "scope": e.get("scope", "battlefield"),
            "source": e.get("source", ""),
        }

    def _convert_mutate_effect(self, e: dict) -> dict:
        """Convert a 'mutate_effect' kind to a mod op with mutate flag.

        Used by traits like 营养液泡 that modify existing stat effects in-place.
        """
        target = self._map_target(e.get("target", "self"))
        result: dict = {
            "op": "mod",
            "target": target,
            "mutate": True,
        }
        filt = e.get("filter", {})
        if filt.get("is_stat"):
            result["stat"] = "atk"  # placeholder, filtered at apply time
        if "steps>0" in filt or filt.get("steps>0"):
            result["mode"] = "add"
        if "delta_steps" in e:
            result["steps"] = e["delta_steps"]
        if "source" in e:
            result["source"] = e["source"]
        if "limit_to_effect" in e:
            result["limit_to_effect"] = e["limit_to_effect"]
        return result

    # ── Helpers ──

    def _expand_aura(self, aura: dict, parent: dict, trait_name: str) -> list[dict]:
        """Expand aura into entry + leave trigger pair."""
        effects = aura.get("effects", [])
        aura_name = aura.get("name", trait_name)
        aura_target = aura.get("target", "opponent_active")
        for e in effects:
            e.setdefault("source", aura_name)
            e.setdefault("target", aura_target)

        entry_trigger = {
            "on": "entry",
            "effects": effects,
        }
        if parent.get("condition"):
            entry_trigger["condition"] = parent["condition"]

        leave_effects = []
        for e in effects:
            leave_effects.append({
                "kind": "remove_effect",
                "source": e.get("source", aura_name),
                "target": e.get("target", aura_target),
            })
        leave_trigger = {"on": "leave", "effects": leave_effects}

        return [entry_trigger, leave_trigger]

    @staticmethod
    def _map_target(target: str) -> str:
        """Map trait target names to Skill IR target names."""
        mapping = {
            "self": "sprite_self",
            "opponent_active": "sprite_opp",
            "opponent": "sprite_opp",
            "opp": "sprite_opp",
            "target": "sprite_opp",
            "own_team": "sprite_self",
            "opp_team": "sprite_opp",
            "enemy_new": "sprite_opp",
        }
        return mapping.get(target, target)

    @staticmethod
    def _map_scope(trigger: dict) -> str:
        """Map trait scope to Observer scope."""
        scope = trigger.get("scope", "battlefield")
        return scope
