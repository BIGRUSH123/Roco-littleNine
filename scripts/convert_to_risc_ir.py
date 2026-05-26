"""Convert old IR opcodes to RISC IR in data/skills/ and data/traits/.

Usage:
  python scripts/convert_to_risc_ir.py --dry-run   # Show what would change
  python scripts/convert_to_risc_ir.py              # Apply changes
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Classification sets ──
STAT_TO_STAGE = frozenset({"atk", "def", "sp_atk", "sp_def", "speed"})
STAT_TO_POWER = frozenset({"power", "energy_cost", "combo", "priority",
                           "energy_cost_mult", "combo_mult", "energy_cost_delta_mult"})
STAT_TO_MULT = frozenset({"power_mult", "damage_mult", "damage_reduction", "life_drain"})
STAT_TO_FLAG = frozenset({
    "immune", "freeze_immune", "survive", "charged", "pre_charged",
    "drive", "swift", "extra_action", "extra_turn_end", "heal_reverse",
    "life_as_energy", "ignore_mods", "ignore_resistance", "cooldown",
    "no_self_damage", "tick_reduce", "abnormal_tick_invert",
    "unlimited_abnormal", "charge_any_skill", "usable_while_charging",
})

# Traits that are already in new format (triggers + kind) — skip
SKIP_TRAITS = frozenset()


def convert_effect(effect: dict) -> dict:
    """Convert a single effect dict from old IR to RISC IR. Returns the modified dict."""
    op = effect.get("op")

    if op == "mod":
        _convert_mod(effect)
    elif op == "count":
        _convert_count(effect)
    elif op == "schedule":
        _convert_schedule(effect)
    elif op == "inherit_effects":
        effect["op"] = "inherit"
    elif op == "when":
        effect["op"] = "branch"

    return effect


def _convert_mod(effect: dict):
    """Convert mod opcode based on stat category."""
    stat = effect.pop("stat", "")
    mode = effect.pop("mode", None)
    steps = effect.pop("steps", None)
    value = effect.pop("value", None)

    if stat in STAT_TO_STAGE:
        if steps is not None:
            # Steps-based → stat_stage (integer stage changes, 1 step = 10%)
            effect["op"] = "stat_stage"
            effect["stat"] = stat
            if isinstance(steps, dict):
                effect["steps"] = steps  # query dict — pass through
            else:
                effect["steps"] = int(steps)
            effect.pop("skill_where", None)
        else:
            # Value-based → mult_mod (base stat multiplier, ModifierInjection path)
            effect["op"] = "mult_mod"
            effect["attr"] = stat
            if value is not None:
                effect["value"] = value
            if mode:
                effect["mode"] = mode
            effect.pop("skill_where", None)

    elif stat in STAT_TO_POWER:
        effect["op"] = "power_mod"
        effect["attr"] = stat
        if steps is not None:
            effect["delta"] = steps
        elif value is not None:
            effect["delta"] = value
        # mode is implicit (delta always additive)

    elif stat in STAT_TO_MULT:
        effect["op"] = "mult_mod"
        effect["attr"] = stat
        if value is not None:
            effect["value"] = value
        elif steps is not None:
            effect["value"] = float(steps)
        # mode is preserved for mult_mod
        if mode:
            effect["mode"] = mode
        effect.pop("skill_where", None)

    elif stat in STAT_TO_FLAG:
        effect["op"] = "flag_set"
        effect["flag"] = stat
        if value is not None:
            effect["value"] = value
        elif steps is not None:
            effect["value"] = bool(steps)
        else:
            effect["value"] = True
        effect.pop("skill_where", None)
        effect.pop("per_hit", None)

    elif stat == "hp":
        effect["op"] = "heal"
        if value is not None:
            if isinstance(value, dict):
                # Dynamic query — keep as value
                effect["value"] = value
            elif isinstance(value, (int, float)) and -1.0 <= value <= 1.0 and value != 0:
                effect["ratio"] = value
            else:
                effect["value"] = value
        elif steps is not None:
            effect["value"] = steps
        effect.pop("skill_where", None)
        effect.pop("per_hit", None)

    elif stat == "energy":
        effect["op"] = "energize"
        if value is not None:
            effect["delta"] = value
        elif steps is not None:
            effect["delta"] = steps
        effect.pop("skill_where", None)
        effect.pop("per_hit", None)

    elif stat == "revive":
        effect["op"] = "revive"
        effect["hp_ratio"] = value if value is not None else 1.0
        effect.pop("skill_where", None)
        effect.pop("per_hit", None)

    elif stat == "devotion":
        # Keep as mod for now — devotion has special handling
        effect["op"] = "mod"
        effect["stat"] = stat
        if value is not None:
            effect["value"] = value
        if steps is not None:
            effect["steps"] = steps
        if mode:
            effect["mode"] = mode
        effect.pop("skill_where", None)
        effect.pop("per_hit", None)

    else:
        # Unknown stat — keep as mod
        effect["op"] = "mod"
        effect["stat"] = stat
        if value is not None:
            effect["value"] = value
        if steps is not None:
            effect["steps"] = steps
        if mode:
            effect["mode"] = mode


def _convert_count(effect: dict):
    """Convert count opcode to observer."""
    effect["op"] = "observer"
    # when → cond
    when = effect.pop("when", None)
    if when is not None:
        effect["cond"] = when
    # threshold → counter
    threshold = effect.pop("threshold", None)
    if threshold is not None:
        effect["counter"] = {
            "name": effect.pop("name", ""),
            "threshold": threshold,
            "reset": effect.pop("reset_on_fire", True),
        }
    else:
        effect.pop("name", None)
        effect.pop("reset_on_fire", None)


def _convert_schedule(effect: dict):
    """Convert schedule opcode to defer."""
    effect["op"] = "defer"
    effect["turns"] = effect.pop("delay_turns", 0)
    phase = effect.pop("phase", "start")
    effect["at"] = "turn_start" if phase == "start" else "turn_end"
    effect["then"] = effect.pop("effects", [])


# ── Recursive walker ──

def _walk_list(items: list) -> int:
    """Walk a list of effects, converting in-place. Returns count of conversions."""
    count = 0
    for item in items:
        if isinstance(item, dict):
            count += _walk_dict(item)
    return count


def _walk_dict(obj: dict) -> int:
    """Walk a dict, converting effect objects in-place. Returns count of conversions."""
    count = 0

    # Convert this dict if it has an 'op' key
    op = obj.get("op")
    if op in ("mod", "count", "schedule", "inherit_effects", "when"):
        convert_effect(obj)
        count += 1

    # Recurse into nested containers
    for key in ("effects", "then", "else", "else_if", "effect"):
        val = obj.get(key)
        if isinstance(val, list):
            count += _walk_list(val)
        elif isinstance(val, dict):
            count += _walk_dict(val)

    # The 'when' key in condition wrappers (not opcodes) — recurse into it
    when = obj.get("when")
    if isinstance(when, dict):
        count += _walk_dict(when)

    # conditions array (and/or)
    conditions = obj.get("conditions")
    if isinstance(conditions, list):
        count += _walk_list(conditions)

    return count


# ── Triggers → RISC IR converter ──

_TRIGGER_ON_TO_COND = {
    "entry":       {"cond": "sprite_entered", "of": "sprite_self"},
    "ko_enemy":    {"cond": "on_ko"},
    "skill_use":   {"cond": "skill_use"},
    "leave":       {"cond": "sprite_left", "of": "sprite_self"},
    "faint":       {"cond": "on_self_ko"},
    "turn_end":    {"cond": "turn_end"},
    "hit":         {"cond": "on_damage_taken"},
    "turn_start":  {},
    "round_start": {},
    "opp_entry":   {"cond": "sprite_entered", "of": "sprite_opp"},
    "opp_leave":   {"cond": "sprite_left", "of": "sprite_opp"},
    "enemy_leave": {"cond": "sprite_left", "of": "sprite_opp"},
    "cast":        {"cond": "skill_use"},
    "before_act":  {},
    "after_act":   {"cond": "sprite_acted", "of": "sprite_self"},
    "counter_success": {"cond": "counter_succeeded"},
    "take_damage": {"cond": "on_damage_taken"},
    "damage":      {"cond": "on_damage_taken"},
    "defend":      {},  # fires when targeted — cond evaluated by modifier system
    "modifier":    {},  # fires pre-modifier — cond evaluated by modifier system
    "inflict":     {},  # fires when applying abnormal — engine hook
    "abnormal_tick": {"cond": "on_abnormal_tick"},
    "energy_change": {"cond": "on_energy_changed"},
    "gain_effect": {},  # fires when gaining effect
}

def _convert_path_condition(path_cond: dict) -> dict:
    """Convert a path condition dict to Skill IR cond format."""
    path = path_cond.get("path", "")
    op = path_cond.get("op", "eq")
    value = path_cond.get("value")

    # team_elements contains X
    if path == "team_elements" and op == "contains":
        return {"cond": "team_has_element", "element": value}

    # skill.element eq X
    if path == "skill.element" and op == "eq":
        return {"cond": "skill_use", "element": value}

    # battle.globals.weather eq X
    if path in ("battle.globals.weather", "battle.weather") and op == "eq":
        return {"cond": "weather_is", "weather": value}

    # self.effects[name=X].exists
    if ".effects[name=" in path:
        import re
        m = re.match(r'self\.effects\[name=([^\]]+)\]\.exists', path)
        if m:
            return {"cond": "have", "what": "abnormal", "of": "sprite_self", "name": m.group(1)}
        m = re.match(r'target\.effects\[name=([^\]]+)\]\.exists', path)
        if m:
            return {"cond": "have", "what": "abnormal", "of": "sprite_opp", "name": m.group(1)}

    # Generic: compare op
    q_path = path.replace("self.", "").replace("target.", "").replace("battle.globals.", "")
    of_map = {
        "hp_ratio": "sprite_self", "energy": "sprite_self",
        "hp": "sprite_self", "speed": "sprite_self",
    }
    of = of_map.get(q_path, "sprite_self")
    return {"cond": "compare", "q": q_path, "of": of, "op": op, "value": value}

def _kind_to_effect(eff: dict) -> dict:
    """Convert a triggers-format effect (kind/stat/steps) to RISC IR."""
    kind = eff.get("kind", "stat")
    result = {"target": eff.get("target", "sprite_self")}
    if "scope" in eff:
        result["scope"] = eff["scope"]
    if "source" in eff:
        result["source"] = eff["source"]
    if "per_hit" in eff:
        result["per_hit"] = eff["per_hit"]

    if kind == "stat":
        stat = eff.get("stat", "atk")
        if "steps" in eff:
            result["op"] = "stat_stage"
            result["stat"] = stat
            result["steps"] = eff["steps"]
        elif "value" in eff:
            result["op"] = "mult_mod"
            result["attr"] = stat
            result["value"] = eff["value"]
            if "mode" in eff:
                result["mode"] = eff["mode"]
    elif kind == "abnormal":
        result["op"] = "abnormal"
        result["name"] = eff.get("name", "")
        result["stacks"] = eff.get("stacks", eff.get("steps", 1))
    elif kind == "mark":
        result["op"] = "mark"
        result["name"] = eff.get("name", "")
        result["stacks"] = eff.get("stacks", eff.get("steps", 1))
    elif kind == "weather":
        result["op"] = "weather"
        result["weather"] = eff.get("weather", "")
    elif kind == "dispel":
        result["op"] = "dispel"
        result["what"] = eff.get("what", "positive")
        if "name" in eff:
            result["name"] = eff["name"]
    elif kind == "steal":
        result["op"] = "steal"
        result["what"] = eff.get("what", "positive")
    elif kind == "heal":
        result["op"] = "heal"
        if "ratio" in eff:
            result["ratio"] = eff["ratio"]
        elif "value" in eff:
            result["value"] = eff["value"]
    elif kind == "energize":
        result["op"] = "energize"
        result["delta"] = eff.get("delta", eff.get("value", 0))
    elif kind == "revive":
        result["op"] = "revive"
        result["hp_ratio"] = eff.get("hp_ratio", eff.get("value", 1.0))
    elif kind == "flag":
        result["op"] = "flag_set"
        result["flag"] = eff.get("flag", eff.get("stat", ""))
        result["value"] = eff.get("value", True)
    elif kind == "power_mod":
        result["op"] = "power_mod"
        result["attr"] = eff.get("attr", eff.get("stat", ""))
        result["delta"] = eff.get("delta", eff.get("value", 0))
    elif kind == "force_switch":
        result["op"] = "escape"
        result["target"] = eff.get("target", "sprite_opp")
    elif kind == "transform":
        result["op"] = "transform"
        result["species"] = eff.get("species", "")
    elif kind == "special":
        result.update(_convert_special_effect(eff))
    elif kind == "mutate_effect":
        result["op"] = "mutate_effect"
        for k in ("filter", "target", "delta_steps", "limit_to_effect"):
            if k in eff:
                result[k] = eff[k]
    else:
        result["op"] = kind
    return result

def _convert_special_effect(eff: dict) -> dict:
    """Convert a triggers-format 'special' effect to specific RISC op."""
    name = eff.get("name", "")
    if name == "lives_add":
        return {"op": "lives", "delta": eff.get("amount", 1), "target_team": "own"}
    if name == "gain_energy":
        return {"op": "energize", "delta": eff.get("amount", 0)}
    if name == "direct_heal":
        amount = eff.get("amount", 0)
        if isinstance(amount, str) and amount.startswith("=@"):
            return {"op": "heal", "value": amount}
        return {"op": "heal", "value": amount}
    if name == "inherit_effects":
        result = {"op": "inherit", "target": eff.get("inherit_target", "enemy_new"), "scope": eff.get("scope", "battlefield")}
        if "source_sprite" in eff:
            result["source"] = eff["source_sprite"]
        return result
    # fallback: pass through
    return {"op": "mod", "stat": name, "value": eff.get("amount", 0)}

def _convert_triggers(traits_data: dict) -> int:
    """Convert a trait JSON's triggers array to RISC IR effects array. Returns count."""
    triggers = traits_data.get("triggers")
    if not triggers or not isinstance(triggers, list):
        return 0

    effects = []
    count = 0

    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        on = trigger.get("on", "")
        if not on:
            continue

        # Build condition
        base_cond = _TRIGGER_ON_TO_COND.get(on)
        if base_cond is None:
            continue

        trigger_condition = trigger.get("condition")
        clear_condition = trigger.get("clear_condition")

        # Build combined condition (base + trigger condition)
        if trigger_condition and isinstance(trigger_condition, dict):
            trigger_cond = _convert_path_condition(trigger_condition)
            if base_cond is not None and "cond" in base_cond:
                cond = {"cond": "and", "conditions": [base_cond, trigger_cond]}
            else:
                cond = trigger_cond
        else:
            cond = base_cond if base_cond else {}

        # Build effects list
        eff_list = []
        for eff in trigger.get("effects", []):
            if not isinstance(eff, dict):
                continue
            if eff.get("kind") == "damage":
                continue  # engine handles damage implicitly
            eff_list.append(_kind_to_effect(eff))
            count += 1

        if not eff_list:
            continue

        effects_mode = trigger.get("effects_mode", "")
        scope = "persistent"
        if effects_mode == "replace":
            scope = "battlefield"  # battlefield scope + source = replace behavior
        elif effects_mode == "conditional_replace":
            scope = "battlefield"

        # Build observer op
        observer = {
            "op": "observer",
            "then": eff_list,
            "scope": scope,
        }
        if cond and "cond" in cond:
            observer["cond"] = cond
        if on in ("entry", "leave"):
            observer["listen"] = "post_entry" if on == "entry" else "post_leave"

        effects.append(observer)

        # Handle clear_condition: create a second observer with NOT condition
        if clear_condition and isinstance(clear_condition, dict):
            clear_cond = _convert_path_condition(clear_condition)
            clear_obs = {
                "op": "observer",
                "cond": {"cond": "not", "condition": clear_cond},
                "then": [{"op": "dispel", "target": "sprite_self"}],
                "scope": "persistent",
            }
            effects.append(clear_obs)
            count += 1

    if effects:
        traits_data["effects"] = effects
        del traits_data["triggers"]
    return count


def convert_file(filepath: Path, dry_run: bool = False) -> int:
    """Convert a single JSON file. Returns number of conversions made."""
    with open(filepath, "r", encoding="utf-8") as f:
        original = f.read()
        data = json.loads(original)

    # Step 0: Convert triggers format → effects format (if present)
    count = 0
    if "triggers" in data and isinstance(data["triggers"], list) and data["triggers"]:
        count += _convert_triggers(data)

    # Walk the JSON structure
    if "effects" in data and isinstance(data["effects"], list):
        count += _walk_list(data["effects"])
    if "triggers" in data and isinstance(data["triggers"], list):
        count += _walk_list(data["triggers"])
    if "passive" in data and isinstance(data["passive"], list):
        count += _walk_list(data["passive"])

    if count == 0:
        return 0

    new_json = json.dumps(data, ensure_ascii=False, indent=2)
    if not new_json.endswith("\n"):
        new_json += "\n"

    if dry_run:
        rel = filepath.relative_to(PROJECT_ROOT)
        print(f"  [{count} changes] {rel}")
        return count

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_json)
    return count


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("=== DRY RUN (no files will be modified) ===\n")

    total_files = 0
    total_changes = 0

    dirs = [
        PROJECT_ROOT / "data" / "skills",
        PROJECT_ROOT / "data" / "traits",
    ]

    for d in dirs:
        if not d.exists():
            print(f"WARNING: directory not found: {d}")
            continue

        print(f"--- {d.relative_to(PROJECT_ROOT)} ---")
        dir_files = 0
        dir_changes = 0

        for fpath in sorted(d.glob("*.json")):
            if fpath.name == "_ids.json":
                continue
            if fpath.stem in SKIP_TRAITS:
                continue

            c = convert_file(fpath, dry_run=dry_run)
            if c > 0:
                dir_files += 1
                dir_changes += c

        print(f"  Files changed: {dir_files}, Opcodes converted: {dir_changes}\n")
        total_files += dir_files
        total_changes += dir_changes

    if dry_run:
        print("=== DRY RUN complete ===")
        print(f"Would modify {total_files} files, {total_changes} opcode conversions")
    else:
        print(f"=== Done: {total_files} files modified, {total_changes} opcodes converted ===")


if __name__ == "__main__":
    main()
