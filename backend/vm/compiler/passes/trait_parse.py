"""Pass 1: TraitParsePass — parse trait JSON triggers into TraitTrigger IR."""
from __future__ import annotations

import re
from typing import Any

from backend.vm.compiler.context import CompileError, CompilerContext
from backend.vm.ir_trait import (
    ActionModifierOp,
    AndCond,
    BattleSkillMutOp,
    CompiledTrait,
    FnCond,
    InheritEffectsOp,
    LivesOp,
    MutateEffectOp,
    NotCond,
    OrCond,
    PathCond,
    RemoveEffectOp,
    ScheduleOp,
    TeamCounterOp,
    TraitAbnormalEffect,
    TraitCondition,
    TraitEffect,
    TraitInteractionOp,
    TraitMarkEffect,
    TraitSpecialEffect,
    TraitStatEffect,
    TraitTrigger,
    TraitWeatherEffect,
    TransformOp,
)
from backend.vm.ir_values import IRValue, Literal, RefExpr


class TraitParsePass:
    """Parse trait JSON triggers into TraitTrigger IR."""

    # ── Public API ──

    # ── Passive → triggers cond mapping ──
    _PASSIVE_COND_MAP: dict[str, str] = {
        "skill_use": "skill_use",
        "sprite_entered": "entry",
        "opp_switched": "enemy_leave",
        "self_switched": "leave",
        "on_abnormal_tick": "abnormal_tick",
        "on_ko": "ko_enemy",
        "on_self_ko": "faint",
        "on_abnormal_changed": "gain_effect",
        "on_damage_taken": "take_damage",
        "on_energy_changed": "energy_change",
        "turn_end": "turn_end",
        "turn_start": "turn_start",
    }

    _PASSIVE_TARGET_MAP: dict[str, str] = {
        "sprite_self": "self",
        "sprite_opp": "target",
        "team_opp": "opp_team",
        "team_self": "own_team",
    }

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        data = ctx.raw

        # Normalize passive format → triggers format if needed
        if "passive" in data and "triggers" not in data:
            data = dict(data)  # Don't mutate original
            data["triggers"] = self._normalize_passive_to_triggers(data)
            ctx.raw = data

        triggers_raw = data.get("triggers", [])
        if not triggers_raw:
            name = data.get("name", "")
            if not name:
                # Skip silently — likely _ids.json or similar index file
                return ctx
            # Allow empty triggers (Phase C4: engine-hook traits with no JSON triggers)
            # Produce an empty compiled trait
            ctx.compiled = CompiledTrait(
                name=name,
                triggers=(),
                id=data.get("id", 0),
                description=data.get("description", ""),
            )
            return ctx

        for i, trigger_dict in enumerate(triggers_raw):
            # Aura triggers are expanded inline (entry+leave pair)
            if "aura" in trigger_dict:
                expanded = self._expand_aura_trigger(trigger_dict, i)
                ctx.ir.extend(expanded)
                continue

            try:
                trigger = self._parse_trigger(trigger_dict, i)
                ctx.ir.append(trigger)
            except Exception as e:
                ctx.errors.append(CompileError(
                    op_index=i,
                    message=f"Parse trigger #{i}: {e}",
                    field="triggers",
                ))
        return ctx

    # ── Trigger parsing ──

    def _parse_trigger(self, d: dict, idx: int) -> TraitTrigger:
        on = d.get("on", "")
        condition = self._parse_condition(d.get("condition"))
        effects = self._parse_effects(d.get("effects", []))
        effects_mode = d.get("effects_mode", "accumulate")
        clear_condition = self._parse_condition(d.get("clear_condition"))
        delay = d.get("delay", 0)
        delay_phase = d.get("delay_phase", "start")
        counter = d.get("counter")
        counter_op = d.get("counter_op", "inc")
        counter_value = self._parse_ir_value(d.get("counter_value"))
        counter_trigger = d.get("counter_trigger")
        counter_reset = d.get("counter_reset", False)
        track = d.get("track")
        use_modifiers = d.get("use_modifiers")
        battleskill_mut = self._parse_battleskill_mut(d.get("battleskill_mut", []))
        action_modifier = self._parse_action_modifier(d.get("action_modifier"))
        pending_effects = self._parse_effects(d.get("pending_effects", []))
        flags = d.get("flags")
        team_counters = d.get("team_counters")

        return TraitTrigger(
            on=on,
            condition=condition,
            effects=effects,
            effects_mode=effects_mode,
            clear_condition=clear_condition,
            delay=delay,
            delay_phase=delay_phase,
            counter=counter,
            counter_op=counter_op,
            counter_value=counter_value,
            counter_trigger=counter_trigger,
            counter_reset=counter_reset,
            track=track,
            use_modifiers=use_modifiers,
            battleskill_mut=battleskill_mut,
            action_modifier=action_modifier,
            pending_effects=pending_effects,
            flags=flags,
            team_counters=team_counters,
        )

    # ── Condition parsing ──

    def _parse_condition(self, raw: dict | None) -> TraitCondition | None:
        if raw is None or not isinstance(raw, dict):
            return None

        kind = raw.get("kind")

        if kind == "and":
            subs = tuple(
                self._parse_condition(c) for c in raw.get("conditions", [])
                if self._parse_condition(c) is not None
            )
            return AndCond(conditions=subs) if subs else None

        if kind == "or":
            subs = tuple(
                self._parse_condition(c) for c in raw.get("conditions", [])
                if self._parse_condition(c) is not None
            )
            return OrCond(conditions=subs) if subs else None

        if kind == "not":
            sub = self._parse_condition(raw.get("condition"))
            return NotCond(condition=sub) if sub else None

        if kind == "fn":
            name = raw.get("name", "")
            return FnCond(name=name) if name else None

        # Default: path condition (kind == "path" or no kind specified)
        path_str = raw.get("path", "")
        if path_str:
            path_parts = path_str.split(".")
            op = raw.get("op", "eq")
            val_raw = raw.get("value")
            value = self._parse_ir_value(val_raw)
            return PathCond(path=path_parts, op=op, value=value)

        # Fallback: if it has a name but no path or kind, treat as FnCond
        name = raw.get("name", "")
        if name:
            return FnCond(name=name)

        return None

    # ── Effect parsing ──

    def _parse_effects(self, raw: list[dict]) -> tuple[TraitEffect, ...]:
        results: list[TraitEffect] = []
        for e in raw:
            parsed = self._parse_effect(e)
            if parsed is not None:
                results.append(parsed)
        return tuple(results)

    def _parse_effect(self, d: dict) -> TraitEffect | None:
        kind = d.get("kind", "stat")

        if kind == "stat":
            return TraitStatEffect(
                kind="stat",
                target=d.get("target", "self"),
                stat=d.get("stat", ""),
                steps=self._parse_ir_value(d.get("steps", 0)),
                scope=d.get("scope", "battlefield"),
                source=d.get("source", ""),
            )

        if kind == "abnormal":
            return TraitAbnormalEffect(
                kind="abnormal",
                target=d.get("target", "opp"),
                name=d.get("name", ""),
                stacks=self._parse_ir_value(d.get("stacks", 1)),
                scope=d.get("scope", "battlefield"),
                source=d.get("source", ""),
            )

        if kind == "mark":
            stacks_raw = d.get("stacks", 1)
            if isinstance(stacks_raw, str):
                stacks_val = self._parse_ref_expr(stacks_raw)
                if isinstance(stacks_val, Literal) and isinstance(stacks_val.value, (int, float)):
                    stacks = int(stacks_val.value)
                else:
                    stacks = 1
            else:
                stacks = int(stacks_raw) if stacks_raw is not None else 1
            return TraitMarkEffect(
                kind="mark",
                name=d.get("name", ""),
                stacks=stacks,
                mark_target=d.get("mark_target", "opp_team"),
            )

        if kind == "weather":
            return TraitWeatherEffect(
                kind="weather",
                weather=d.get("weather", ""),
                turns=d.get("turns", 8),
            )

        if kind == "special":
            return TraitSpecialEffect(
                kind="special",
                name=d.get("name", ""),
                value=self._parse_ir_value(d.get("value")),
                amount=self._parse_ir_value(d.get("amount")),
                target=d.get("target", "self"),
                target_team=d.get("target_team", "own"),
            )

        if kind == "remove_effect":
            return RemoveEffectOp(
                source=d.get("source", ""),
                target=d.get("target", ""),
            )

        if kind == "mutate_effect":
            return MutateEffectOp(
                target=d.get("target", ""),
                filter=d.get("filter", {}),
                delta_steps=d.get("delta_steps", 0),
                delta_stacks=d.get("delta_stacks", 0),
            )

        if kind == "state":
            # pending_effects with kind=state map to TraitSpecialEffect
            return TraitSpecialEffect(
                kind="special",
                name=d.get("name", ""),
                target=d.get("target", "self"),
            )

        if kind == "schedule":
            return ScheduleOp(
                turns=d.get("turns", 1),
                phase=d.get("phase", "start"),
                effects=self._parse_effects(d.get("effects", [])),
            )

        if kind == "inherit_effects":
            return InheritEffectsOp(
                scope=d.get("scope", "battlefield"),
                source_sprite=d.get("source_sprite", "self"),
                target=d.get("target", "enemy_new"),
                via_pending=d.get("via_pending", False),
            )

        if kind == "team_counter":
            return TeamCounterOp(
                key=d.get("key", ""),
                delta=d.get("delta", 1),
                target_team=d.get("target_team", "own"),
            )

        if kind == "transform":
            return TransformOp(
                species=d.get("species", ""),
                skills=d.get("skills"),
                reset_hp=d.get("reset_hp", False),
                reset_energy=d.get("reset_energy", False),
            )

        if kind == "trait_interaction":
            return TraitInteractionOp(
                action=d.get("action", ""),
                target=d.get("target", ""),
                copy_from=d.get("copy_from"),
                new_ability=d.get("new_ability"),
            )

        if kind == "lives":
            return LivesOp(
                delta=d.get("delta", 0),
                target_team=d.get("target_team", "own"),
            )

        return None

    # ── BattleSkillMutOp parsing ──

    def _parse_battleskill_mut(self, raw: list[dict]) -> tuple[BattleSkillMutOp, ...]:
        results: list[BattleSkillMutOp] = []
        for m in raw:
            results.append(BattleSkillMutOp(
                filter=m.get("filter", {}),
                field=m.get("field", ""),
                value=self._parse_ir_value(m.get("value", 0)),
                op=m.get("op", "set"),
                target=m.get("target", "all"),
            ))
        return tuple(results)

    # ── ActionModifierOp parsing ──

    def _parse_action_modifier(self, raw: dict | None) -> ActionModifierOp | None:
        if raw is None:
            return None
        return ActionModifierOp(
            action=raw.get("action", ""),
            slot=raw.get("slot"),
            slots=raw.get("slots"),
            force=raw.get("force"),
        )

    # ── IRValue parsing ──

    def _parse_ir_value(self, raw: Any) -> IRValue:
        """Parse any value into an IRValue.

        - None -> Literal(0)
        - int/float/bool -> Literal(value)
        - string starting with '=' -> try RefExpr, fallback to Literal(str)
        - other string -> Literal(str)
        """
        if raw is None:
            return Literal(0)

        if isinstance(raw, (int, float, bool)):
            return Literal(raw)

        if isinstance(raw, str):
            if raw.startswith("="):
                return self._parse_ref_expr(raw)
            # Try numeric conversion
            try:
                if "." in raw:
                    return Literal(float(raw))
                return Literal(int(raw))
            except (ValueError, TypeError):
                return Literal(raw)

        if isinstance(raw, list):
            return Literal(raw)

        if isinstance(raw, dict):
            return Literal(raw)

        return Literal(raw)

    # ── RefExpr parsing ──

    _REF_PATTERN = re.compile(
        r"^=@([a-zA-Z_]\w*(?:\[[^\]]*\])?(?:\.[a-zA-Z_]\w*(?:\[[^\]]*\])?)*)"
        r"\s*(?:\*\s*(-?[\d.]+))?"
        r"\s*(?:\+\s*(-?[\d.]+))?"
        r"$"
    )

    def _parse_ref_expr(self, raw: str) -> IRValue:
        """Parse a ref expression string like '=@player_fainted_count * 3'.

        Simple single-term: =@root.path * N + M -> RefExpr(root, path, N, M)
        Compound (multiple @ symbols): kept as Literal string for runtime eval.
        """
        if not raw.startswith("=@"):
            return Literal(raw)

        expr = raw[2:]  # strip '=@'

        # Compound expression with multiple @ references or arithmetic operators between refs
        if re.search(r'[@\+\-]\s*@', expr) or (
            re.search(r'@', expr) and re.search(r'[\+\-]\s*(?=\D)', expr)
        ):
            return Literal(raw)

        m = self._REF_PATTERN.match(raw)
        if m:
            full_path = m.group(1)
            multiplier = float(m.group(2)) if m.group(2) else 1.0
            offset = int(float(m.group(3))) if m.group(3) else 0

            root, path = self._split_ref_path(full_path)
            return RefExpr(root=root, path=path, multiplier=multiplier, offset=offset)

        # Fallback: keep as string for runtime resolution
        return Literal(raw)

    @staticmethod
    def _split_ref_path(full_path: str) -> tuple[str, list[str]]:
        """Split a ref path like 'self.skills[element=毒].count' into (root, path)."""
        parts = full_path.split(".")
        root = parts[0]
        # Extract bracket content from root: team_counters[element:武] -> root=team_counters
        bracket_match = re.match(r'(\w+)\[([^\]]+)\]', root)
        if bracket_match:
            root = bracket_match.group(1)
            bracket_content = bracket_match.group(2)
            path = [bracket_content] + parts[1:]
        else:
            path = parts[1:] if len(parts) > 1 else []
        return root, path

    # ── Aura expansion (inline, pre-IR) ──

    def _expand_aura_trigger(self, raw: dict, idx: int) -> list[TraitTrigger]:
        """Expand a trigger with an 'aura' key into entry + leave TraitTrigger pair.

        Matches the engine's DataDrivenTrait._expand_aura logic.
        """
        aura = raw.get("aura", {})
        if not aura or not isinstance(aura, dict):
            return []

        effects_raw = aura.get("effects", [])
        if not effects_raw:
            return []

        aura_name = aura.get("name", "")
        aura_target = aura.get("target", "opponent_active")
        parent_mode = raw.get("effects_mode", "accumulate")

        # Parse parent condition (if any) for the entry trigger
        parent_condition = self._parse_condition(raw.get("condition"))

        # Entry trigger: apply aura effects
        entry_effects = tuple(
            self._parse_effect({**e, "source": e.get("source", aura_name), "target": e.get("target", aura_target)})
            for e in effects_raw
        )

        entry_trigger = TraitTrigger(
            on="entry",
            condition=parent_condition,
            effects=entry_effects,
            effects_mode=parent_mode,
        )

        # Leave trigger: remove aura effects
        leave_effects = tuple(
            RemoveEffectOp(
                source=e.get("source", aura_name),
                target=e.get("target", aura_target),
            )
            for e in effects_raw
        )

        leave_trigger = TraitTrigger(
            on="leave",
            condition=None,
            effects=leave_effects,
            effects_mode="accumulate",
        )

        return [entry_trigger, leave_trigger]

    # ── Passive format normalization ──

    def _normalize_passive_to_triggers(self, data: dict) -> list[dict]:
        """Convert passive-format entries to triggers format.

        The passive format (used by 28 older traits) has:
          {"passive": [{"op": "count"/"mod", "when": {...}, "then": [...]}]}

        This converts to the standard triggers format:
          {"triggers": [{"on": "...", "condition": {...}, "effects": [...]}]}
        """
        passive_list = data.get("passive", [])
        triggers: list[dict] = []

        for entry in passive_list:
            op = entry.get("op", "")

            if op == "count":
                # Event-driven: when.cond → on, then → effects
                when = entry.get("when", {})
                cond_key = when.get("cond", "")
                if not cond_key:
                    continue

                on = self._PASSIVE_COND_MAP.get(cond_key, cond_key)

                trigger: dict[str, Any] = {"on": on}

                # Build condition from when filters
                when_conditions: list[dict] = []
                if "energy_cost" in when:
                    when_conditions.append({
                        "path": "skill.energy_cost", "op": "eq", "value": when["energy_cost"],
                    })
                if "element" in when:
                    when_conditions.append({
                        "path": "skill.element", "op": "eq", "value": when["element"],
                    })
                if "name" in when and cond_key in ("on_abnormal_tick", "on_abnormal_changed"):
                    when_conditions.append({
                        "path": "effect_name", "op": "eq", "value": when["name"],
                    })

                if len(when_conditions) == 1:
                    trigger["condition"] = when_conditions[0]
                elif len(when_conditions) > 1:
                    trigger["condition"] = {"kind": "and", "conditions": when_conditions}

                # Parse then effects
                then_list = entry.get("then", [])
                effects, use_modifiers, battleskill_mut, flags, pending = \
                    self._convert_passive_then(then_list, data.get("name", ""))

                if effects:
                    trigger["effects"] = effects
                if use_modifiers:
                    trigger["use_modifiers"] = use_modifiers
                if battleskill_mut:
                    trigger["battleskill_mut"] = battleskill_mut
                if flags:
                    trigger["flags"] = flags
                if pending:
                    trigger["pending_effects"] = pending

                triggers.append(trigger)

            elif op == "mod":
                # Direct modifier (no when/then) → entry trigger
                effects, use_modifiers, battleskill_mut, flags, pending = \
                    self._convert_passive_then([entry], data.get("name", ""))

                trigger: dict[str, Any] = {"on": "entry"}
                if effects:
                    trigger["effects"] = effects
                if use_modifiers:
                    trigger["use_modifiers"] = use_modifiers
                if battleskill_mut:
                    trigger["battleskill_mut"] = battleskill_mut
                if flags:
                    trigger["flags"] = flags
                if pending:
                    trigger["pending_effects"] = pending

                triggers.append(trigger)

        return triggers

    def _convert_passive_then(
        self, then_list: list[dict], trait_name: str
    ) -> tuple[list[dict], dict, list[dict], dict, list[dict]]:
        """Convert passive 'then' operations to triggers-format effects.

        Returns: (effects, use_modifiers, battleskill_mut, flags, pending_effects)
        """
        effects: list[dict] = []
        use_modifiers: dict[str, dict] = {}
        battleskill_mut: list[dict] = []
        flags: dict[str, Any] = {}
        pending: list[dict] = []

        for item in then_list:
            item_op = item.get("op", "")

            if item_op == "mod":
                self._convert_mod_op(item, effects, use_modifiers, battleskill_mut,
                                     flags, pending, trait_name)
            elif item_op == "abnormal":
                effects.append({
                    "kind": "abnormal",
                    "name": item.get("name", ""),
                    "stacks": item.get("stacks", 1),
                    "target": self._PASSIVE_TARGET_MAP.get(item.get("target", ""), "target"),
                    "source": trait_name,
                })
            elif item_op == "mark":
                effects.append({
                    "kind": "mark",
                    "name": item.get("name", ""),
                    "stacks": item.get("stacks", 1),
                    "mark_target": self._PASSIVE_TARGET_MAP.get(item.get("target", ""), "opp_team"),
                })
            elif item_op == "state":
                pending.append({
                    "kind": "state",
                    "name": item.get("name", ""),
                    "source": trait_name,
                })

        return effects, use_modifiers, battleskill_mut, flags, pending

    def _convert_mod_op(
        self, item: dict, effects: list[dict], use_modifiers: dict[str, dict],
        battleskill_mut: list[dict], flags: dict, pending: list[dict], trait_name: str
    ) -> None:
        """Convert a passive 'mod' op to the appropriate trigger format."""
        stat = item.get("stat", "")
        value_raw = item.get("value", 0)
        mode = item.get("mode", "add")
        target = self._PASSIVE_TARGET_MAP.get(item.get("target", ""), "self")
        scope = item.get("scope", "battlefield")
        per_hit = item.get("per_hit", False)
        ttl = item.get("ttl")

        # Convert query-style values to ref expressions
        value = self._convert_passive_value(value_raw)

        # Skill-specific modifiers → battleskill_mut
        skill_filter = item.get("skill_filter")
        skill_where = item.get("skill_where")
        skill_name = item.get("name")

        if skill_filter or skill_where or skill_name:
            mut_filter: dict[str, Any] = {}
            if skill_filter == "all":
                pass  # No filter means all skills
            elif skill_filter == "bare_attack":
                mut_filter["is_attack"] = True
                mut_filter["is_status"] = False
            if skill_name:
                mut_filter["name"] = skill_name
            if skill_where:
                if isinstance(skill_where, dict):
                    sq = skill_where.get("q", "")
                    if sq == "energy_cost":
                        ec_op = skill_where.get("op", "gt")
                        ec_val = skill_where.get("value", 0)
                        if ec_op == "gt":
                            mut_filter["energy_cost_gt"] = ec_val
                        elif ec_op == "gte":
                            mut_filter["energy_cost_gte"] = ec_val
                        elif ec_op == "lt":
                            mut_filter["energy_cost_lt"] = ec_val
                        elif ec_op == "lte":
                            mut_filter["energy_cost_lte"] = ec_val
                        elif ec_op == "eq":
                            mut_filter["energy_cost_eq"] = ec_val

            if stat == "power_mult":
                use_modifiers["power_mult"] = {
                    "op": "add" if mode == "add" else "set",
                    "value": value_raw if isinstance(value_raw, (int, float)) else value,
                }
            elif stat == "damage_reduction":
                use_modifiers["damage_reduction"] = {
                    "op": "add",
                    "value": value_raw if isinstance(value_raw, (int, float)) else value,
                }
            else:
                battleskill_mut.append({
                    "filter": mut_filter,
                    "field": stat,
                    "op": "add" if mode == "add" else "set",
                    "value": value_raw if isinstance(value_raw, (int, float)) else value,
                })
            return

        # Stat effects
        if stat in ("atk", "def", "sp_atk", "sp_def", "speed", "combo", "energy_cost"):
            steps = value
            if isinstance(value_raw, (int, float)):
                if stat in ("combo", "energy_cost"):
                    steps = int(value_raw)
                else:
                    steps = int(value_raw * 10) if mode == "add" else int(value_raw)
            effects.append({
                "kind": "stat",
                "stat": stat,
                "steps": steps,
                "target": target,
                "scope": scope,
                "source": trait_name,
            })
        elif stat == "power":
            steps = value
            if isinstance(value_raw, (int, float)):
                steps = int(value_raw / 10)  # Power in steps
            effects.append({
                "kind": "stat",
                "stat": "power",
                "steps": steps,
                "target": target,
                "scope": scope,
                "source": trait_name,
            })
        elif stat == "hp":
            effects.append({
                "kind": "special",
                "name": "heal",
                "amount": value,
                "target": target,
            })
        elif stat == "energy":
            val_num = value_raw if isinstance(value_raw, (int, float)) else 0
            if val_num >= 0:
                effects.append({
                    "kind": "special",
                    "name": "gain_energy",
                    "amount": value,
                    "target": target,
                })
            else:
                effects.append({
                    "kind": "special",
                    "name": "lose_energy",
                    "amount": -val_num,
                    "target": target,
                })
        elif stat == "lives":
            effects.append({
                "kind": "special",
                "name": "lives_delta",
                "amount": value,
            })
        elif stat == "immune":
            # Immune to a named effect → pending state
            pending.append({
                "kind": "state",
                "name": f"免疫{value}",
                "source": trait_name,
            })
        else:
            # Unknown stat → special effect
            effects.append({
                "kind": "special",
                "name": stat,
                "value": value,
                "target": target,
            })

    def _convert_passive_value(self, value_raw: Any) -> Any:
        """Convert a passive-format value (dict query or literal) to a triggers-format value.

        Query objects like {"q": "energy", "of": "sprite_self", "scale": 0.1, "offset": 1}
        are converted to ref expression strings like "=@self.energy * 0.1 + 1".
        """
        if not isinstance(value_raw, dict):
            return value_raw

        q = value_raw.get("q", "")
        of = value_raw.get("of", "")
        scale = value_raw.get("scale", 1.0)
        offset = value_raw.get("offset", 0)
        # Handle optional 'name' field (e.g., for abnormal_stacks query)
        q_name = value_raw.get("name", "")

        target_map = {"sprite_self": "self", "sprite_opp": "target"}
        target = target_map.get(of, of)

        # Build a ref expression string
        if q == "abnormal_stacks":
            ref = f"=@{target}.effects[name={q_name}].stacks"
        elif q == "energy":
            ref = f"=@{target}.energy"
        elif q == "element":
            ref = f"=@{value_raw.get('of_skill', 'skill')}.element"
        elif q == "last_tick_damage":
            ref = f"=@{target}.last_tick_damage"
        elif q == "energy_cost":
            ref = "=@skill.energy_cost"
        else:
            ref = f"=@{target}.{q}"

        if scale != 1.0:
            ref += f" * {scale}"
        if offset:
            ref += f" + {offset}"

        return ref

