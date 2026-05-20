"""Pass 3: TraitValidatePass — validate compiled TraitTrigger IR."""
from __future__ import annotations

from backend.vm.compiler.context import CompileError, CompilerContext


class TraitValidatePass:
    """Validate TraitTrigger IR fields against known whitelists and rules."""

    # All valid hook names observed in the existing trait JSON data
    VALID_HOOKS: set[str] = {
        "entry", "leave",
        "turn_start", "turn_end",
        "modifier", "damage", "defend", "skill_use",
        "counter_success", "counter_fail",
        "ko_enemy", "faint", "be_killed",
        "energy_change", "gain_effect", "inflict",
        "enemy_leave", "abnormal_tick", "take_damage",
        "start", "end", "swap_in", "swap_out",
        "kill",
        "before_take_damage", "before_action",
        "aura",  # pre-expansion only
    }

    VALID_EFFECTS_MODES: set[str] = {
        "accumulate", "replace", "conditional_replace",
    }

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        for i, trigger in enumerate(ctx.ir):
            self._validate_trigger(trigger, i, ctx)
        return ctx

    def _validate_trigger(self, trigger, idx: int, ctx: CompilerContext) -> None:
        on = getattr(trigger, "on", "")

        # 1. Validate hook name
        if not on:
            ctx.errors.append(CompileError(
                op_index=idx,
                message="Trigger has empty 'on' field",
                field="on",
            ))
        elif on not in self.VALID_HOOKS:
            ctx.warnings.append(
                f"Trigger #{idx}: unknown hook '{on}', not in VALID_HOOKS"
            )

        # 2. Validate effects_mode
        mode = getattr(trigger, "effects_mode", "accumulate")
        if mode not in self.VALID_EFFECTS_MODES:
            ctx.errors.append(CompileError(
                op_index=idx,
                message=f"Invalid effects_mode '{mode}'; must be one of {self.VALID_EFFECTS_MODES}",
                field="effects_mode",
            ))

        # 3. conditional_replace must have clear_condition
        if mode == "conditional_replace":
            clear = getattr(trigger, "clear_condition", None)
            if clear is None:
                ctx.errors.append(CompileError(
                    op_index=idx,
                    message="effects_mode='conditional_replace' requires clear_condition",
                    field="clear_condition",
                ))

        # 4. Warn if no effects AND no battleskill_mut AND no use_modifiers AND no flags
        has_effects = bool(getattr(trigger, "effects", ()))
        has_battleskill_mut = bool(getattr(trigger, "battleskill_mut", ()))
        has_use_modifiers = bool(trigger.use_modifiers)
        has_flags = bool(trigger.flags)
        has_pending = bool(getattr(trigger, "pending_effects", ()))
        has_team_counters = bool(trigger.team_counters)

        if not any([has_effects, has_battleskill_mut, has_use_modifiers, has_flags, has_pending, has_team_counters]):
            ctx.warnings.append(
                f"Trigger #{idx} (on='{on}'): no effects, battleskill_mut, use_modifiers, flags, or pending_effects"
            )

        # 5. Validate delay if present
        delay = getattr(trigger, "delay", 0)
        if delay < 0:
            ctx.errors.append(CompileError(
                op_index=idx,
                message=f"Invalid delay value: {delay}",
                field="delay",
            ))
