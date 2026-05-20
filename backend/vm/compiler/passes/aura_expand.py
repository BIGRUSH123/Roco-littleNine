"""Pass 2: AuraExpandPass — expand aura traits into entry+leave trigger pairs."""
from __future__ import annotations

from backend.vm.compiler.context import CompilerContext
from backend.vm.ir_trait import (
    MutateEffectOp,
    RemoveEffectOp,
    TraitAbnormalEffect,
    TraitEffect,
    TraitMarkEffect,
    TraitSpecialEffect,
    TraitStatEffect,
    TraitTrigger,
    TraitWeatherEffect,
)
from backend.vm.ir_values import Literal


class AuraExpandPass:
    """Expand aura definitions into entry + leave trigger pairs.

    An aura trigger applies effects on entry and removes them on leave.
    The raw JSON format uses an "aura" key within the trigger dict.
    The engine expands these at load time; the compiler does it at compile time.

    This pass scans ctx.raw["triggers"] for entries with an "aura" key
    and expands them into entry+leave TraitTrigger pairs. TraitParsePass
    also handles aura expansion inline, so this pass acts as a safety net
    for any aura entries that weren't expanded during parsing.
    """

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        raw_triggers = ctx.raw.get("triggers", [])

        # Check if any raw triggers have aura entries
        has_aura = any("aura" in t for t in raw_triggers)

        if not has_aura:
            # No aura triggers to expand — pass through
            return ctx

        # If the IR is populated by TraitParsePass, it already handled aura expansion.
        # If IR is empty (raw data hasn't been parsed yet), expand from raw data.
        if ctx.ir:
            # IR already populated by TraitParsePass (which handles aura inline)
            # Verify no unexpanded aura triggers remain
            for trigger in ctx.ir:
                if getattr(trigger, "on", "") == "aura":
                    ctx.warnings.append(
                        "Unexpanded aura trigger found; TraitParsePass should handle inline expansion"
                    )
            return ctx

        # IR is empty — expand aura triggers directly from raw data
        new_ir: list[TraitTrigger] = []
        for i, trigger_dict in enumerate(raw_triggers):
            if "aura" in trigger_dict:
                expanded = self._expand_aura(trigger_dict, i)
                new_ir.extend(expanded)
            else:
                # Non-aura trigger: create a minimal placeholder
                # (TraitParsePass will populate proper IR when it runs)
                pass  # Skip non-aura triggers; they'll be parsed by TraitParsePass

        if new_ir:
            ctx.ir = new_ir
        return ctx

    def _expand_aura(self, raw: dict, idx: int = 0) -> list[TraitTrigger]:
        """Expand a trigger with an 'aura' key into entry + leave trigger pair.

        aura format:
          {"aura": {"name": "冰封", "effects": [...], "target": "opponent_active"}}
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

        # Entry trigger: apply aura effects
        entry_effects: list[TraitEffect] = []
        for e in effects_raw:
            e_copy = dict(e)
            e_copy.setdefault("source", aura_name)
            e_copy.setdefault("target", aura_target)
            entry_effects.append(self._raw_to_effect(e_copy))

        # Leave trigger: remove aura effects
        leave_effects: list[TraitEffect] = []
        for e in effects_raw:
            leave_effects.append(RemoveEffectOp(
                source=e.get("source", aura_name),
                target=e.get("target", aura_target),
            ))

        entry_trigger = TraitTrigger(
            on="entry",
            condition=None,
            effects=tuple(entry_effects),
            effects_mode=parent_mode,
        )

        leave_trigger = TraitTrigger(
            on="leave",
            condition=None,
            effects=tuple(leave_effects),
            effects_mode="accumulate",
        )

        return [entry_trigger, leave_trigger]

    def _raw_to_effect(self, d: dict) -> TraitEffect:
        """Convert a raw effect dict to a TraitEffect for aura expansion."""
        kind = d.get("kind", "stat")

        if kind == "stat":
            steps_raw = d.get("steps", 0)
            if isinstance(steps_raw, (int, float)):
                steps = Literal(steps_raw)
            else:
                steps = Literal(steps_raw)
            return TraitStatEffect(
                kind="stat",
                target=d.get("target", "self"),
                stat=d.get("stat", ""),
                steps=steps,
                scope=d.get("scope", "battlefield"),
                source=d.get("source", ""),
            )

        if kind == "abnormal":
            stacks_raw = d.get("stacks", 1)
            stacks = Literal(stacks_raw) if isinstance(stacks_raw, (int, float)) else Literal(stacks_raw)
            return TraitAbnormalEffect(
                kind="abnormal",
                target=d.get("target", "opp"),
                name=d.get("name", ""),
                stacks=stacks,
                scope=d.get("scope", "battlefield"),
                source=d.get("source", ""),
            )

        if kind == "mark":
            stacks_raw = d.get("stacks", 1)
            stacks = int(stacks_raw) if isinstance(stacks_raw, (int, float)) else 1
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
                target=d.get("target", "self"),
                target_team=d.get("target_team", "own"),
            )

        if kind == "mutate_effect":
            return MutateEffectOp(
                target=d.get("target", ""),
                filter=d.get("filter", {}),
                delta_steps=d.get("delta_steps", 0),
                delta_stacks=d.get("delta_stacks", 0),
            )

        return TraitSpecialEffect(kind="special", name="unknown")
