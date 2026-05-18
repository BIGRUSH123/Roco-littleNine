"""SkillLoader — load, validate, normalize, and pre-process skill JSONs.

Produces a SkillRecord (plain dict-compatible object) with:
    - pre-sorted effects (feeds/needs topological order)
    - normalized opcodes (legacy kind → op/when)
    - injected implicit hit effect for attack skills
"""

import json
import os
from typing import Any

from vm.sort import sort_effects

# Required fields every skill must have
_REQUIRED = frozenset({"name", "element", "skill_type", "energy_cost"})

# Attack types that need an implicit hit effect
_ATTACK_TYPES = frozenset({"物攻", "魔攻", "动态攻击"})


class SkillRecord:
    """A pre-processed skill ready for VM execution.

    Lightweight — just dict fields + processed effects. No references to
    prototype objects.
    """
    __slots__ = (
        "id", "name", "element", "skill_type", "power", "energy_cost",
        "priority", "combo", "counter", "effects", "description",
        "transmission", "exclusive_to",
    )

    def __init__(self, data: dict, effects: list[dict]):
        self.id: int = data.get("id", 0)
        self.name: str = data["name"]
        self.element: str = data.get("element", "")
        self.skill_type: str = data.get("skill_type", "")
        self.power: int = data.get("power", 0)
        self.energy_cost: int = data.get("energy_cost", 0)
        self.priority: int = data.get("priority", 0)
        self.combo: int = data.get("combo", 1)
        self.counter: str = data.get("counter", "")
        self.effects: list[dict] = effects
        self.description: str = data.get("description", "")
        self.transmission: int = data.get("transmission", 0)
        self.exclusive_to: str = data.get("exclusive_to", "")

    def __repr__(self):
        return f"SkillRecord({self.name}, {self.skill_type}, effects={len(self.effects)})"


class SkillLoader:
    """Load and pre-process skill JSONs for VM execution."""

    def __init__(self, skills_dir: str | None = None):
        self._dir = skills_dir
        self._cache: dict[str, SkillRecord] = {}

    # ── Public API ──

    def load(self, data: dict) -> SkillRecord:
        """Load a single skill from a JSON dict."""
        self._validate(data)
        effects = list(data.get("effects", []))
        effects = self._normalize(effects)
        effects = sort_effects(effects)
        effects = self._inject_hit(data, effects)
        return SkillRecord(data, effects)

    def load_file(self, path: str) -> SkillRecord:
        """Load a single skill from a JSON file path."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return self.load(data)

    def load_all(self, skills_dir: str | None = None) -> dict[str, SkillRecord]:
        """Load all skills from a directory. Returns {name: SkillRecord}."""
        directory = skills_dir or self._dir
        if not directory:
            raise ValueError("skills_dir not specified")
        records: dict[str, SkillRecord] = {}
        for fname in os.listdir(directory):
            if fname.startswith("_") or not fname.endswith(".json"):
                continue
            path = os.path.join(directory, fname)
            record = self.load_file(path)
            records[record.name] = record
        self._cache.update(records)
        return records

    # ── Internal ──

    @staticmethod
    def _validate(data: dict) -> None:
        missing = _REQUIRED - set(data.keys())
        if missing:
            raise ValueError(f"Skill '{data.get('name', '?')}' missing: {missing}")

    @staticmethod
    def _normalize(effects: list[dict]) -> list[dict]:
        """Normalize legacy kind-based effects to op/when format.

        Old format: {"kind": "conditional", "when": {"kind": "counter_succeeded"}, "then": [...]}
        New format: {"when": {"cond": "counter_succeeded"}, "then": [...]}
        """
        _KIND_TO_COND = {
            "counter_succeeded": "counter_succeeded",
            "charged": "charged",
            "burst": "burst",
            "is_charging": "is_charging",
            "on_ko": "on_ko",
            "weather_is": "weather_is",
        }
        _KIND_TO_OP = {
            "force_return": "return",
            "charge": "charge",
            "escape": "escape",
            "steal_mark": "steal",
        }

        result = []
        for eff in effects:
            eff = dict(eff)  # shallow copy
            kind = eff.pop("kind", None)

            if kind == "conditional":
                # Convert kind-conditional to when-block
                when = eff.get("when", {})
                if "kind" in when:
                    when = {"cond": _KIND_TO_COND.get(when["kind"], when["kind"])}
                    eff["when"] = when
                result.append(eff)
            elif kind in _KIND_TO_OP:
                eff["op"] = _KIND_TO_OP[kind]
                if "target" not in eff:
                    eff["target"] = "sprite_opp"
                result.append(eff)
            elif kind:
                # Unknown legacy kind — preserve but mark
                eff["op"] = kind
                result.append(eff)
            else:
                result.append(eff)

        return result

    @staticmethod
    def _inject_hit(data: dict, effects: list[dict]) -> list[dict]:
        """Inject an implicit hit effect for attack-type skills.

        Per design doc: SkillLoader injects a synthetic hit effect so the VM
        doesn't need to know about "implicit damage". The hit effect uses
        dynamic power queries if the skill has power modifiers.
        """
        skill_type = data.get("skill_type", "")
        power = data.get("power", 0)

        if skill_type not in _ATTACK_TYPES or power <= 0:
            return list(effects)

        # Determine if there's a feeds:power effect that computes dynamic power.
        # If so, use a dynamic query; otherwise use literal power.
        has_feeds_power = any(e.get("feeds") == "power" for e in effects)

        if has_feeds_power:
            # Dynamic power — query the resolve-time power value
            hit = {
                "op": "hit",
                "power": {"q": "power_base", "of": "skill_off_0"},
                "type": skill_type,
                "feeds": "mult",
            }
        else:
            hit = {
                "op": "hit",
                "power": power,
                "type": skill_type,
                "feeds": "mult",
            }

        result = list(effects) + [hit]
        return sort_effects(result)  # re-sort with the new hit in place
