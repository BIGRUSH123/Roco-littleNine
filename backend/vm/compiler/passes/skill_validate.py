"""Pass 3: SkillValidatePass — whitelist validation for all IR ops."""
from __future__ import annotations

from backend.vm.compiler.context import CompileError, CompilerContext
from backend.vm.ir_skill import (
    AbnormalOp,
    EnergizeOp,
    FlagSetOp,
    HealOp,
    HitOp,
    ModOp,
    MultModOp,
    PowerModOp,
    ResetOp,
    ReviveOp,
    SkillIROp,
    StatStageOp,
    WhenBlock,
)

# RISC attr valid values
VALID_STAGE_STATS = frozenset({"atk", "def", "sp_atk", "sp_def", "speed"})
VALID_POWER_ATTRS = frozenset({
    "power", "energy_cost", "combo", "priority",
    "energy_cost_mult", "combo_mult", "energy_cost_delta_mult",
})
VALID_MULT_ATTRS = frozenset({
    "power_mult", "damage_mult", "damage_reduction", "life_drain",
    "atk", "def", "sp_atk", "sp_def", "speed",  # base stat multipliers
})

# ── Whitelists ──

VALID_TARGETS = frozenset({
    "sprite_self", "sprite_opp",
    "team_own", "own_team",
    "team_opp", "opp_team",
    "team_both",
    "team_own_benched",
    "team_burst",
    "skill_off_0", "skill_opp_current",
    "battle",
})

VALID_STATS = frozenset({
    "hp", "hp_ratio", "hp_missing_ratio", "hp_max",
    "energy", "energy_cost", "energy_cost_reduction", "energy_cost_mult",
    "atk", "def", "sp_atk", "sp_def", "speed",
    "power", "power_mult",
    "damage_reduction", "damage_reduced",
    "damage_mult",
    "heal_reverse",
    "combo", "combo_current", "combo_mult",
    "abnormal_count", "abnormal_stacks",
    "positive_count",
    "mark_count",
    "times_entered", "times_left",
    "elements_used_count",
    "first_action",
    "charged", "is_charging", "pre_charged",
    "last_tick_damage",
    "adjacent_power_sum",
    "skills_energy_sum",
    "zero_cost_skill_count",
    "skill_count",
    "devotion",
    "fainted",
    "burst_triggered_count",
    "counter_value",
    "prev_skill_type",
    "self_koed",
    "target_fainted",
    "life_drain",
    "ignore_mods",
    "ignore_resistance",
    "life_as_energy",
    "survive",
    "extra_action",
    "cooldown",
    "priority",
})

VALID_SCOPES = frozenset({
    "persistent", "battlefield", "permanent",
})

VALID_SKILL_TYPES = frozenset({
    "物攻", "魔攻", "动态攻击", "防御", "状态",
})

VALID_ELEMENTS = frozenset({
    "光", "冰", "地", "幻", "幽", "恶", "普通",
    "机械", "格斗", "毒", "水", "火", "电", "翼",
    "草", "萌", "虫", "龙",
})


class SkillValidatePass:
    """Validates each IR op against whitelists.

    Checks:
    - target is a valid target
    - stat is a valid stat (for ModOp, ResetOp)
    - scope is a valid scope
    - skill_type / element are valid (for informational warnings)
    """

    def process(self, ctx: CompilerContext) -> None:
        for i, op in enumerate(ctx.ir):
            try:
                self._validate_op(op, i)
            except Exception as e:
                ctx.errors.append(CompileError(
                    op_index=i,
                    message=str(e),
                ))

    def _validate_op(self, op: SkillIROp, idx: int) -> None:
        if isinstance(op, WhenBlock):
            self._validate_when_block(op, idx)
            return

        # Validate target field where applicable
        if hasattr(op, "target"):
            self._check(op.target in VALID_TARGETS,
                        f"Invalid target '{op.target}'", idx, "target")
        elif hasattr(op, "from_") and op.from_:
            self._check(op.from_ in VALID_TARGETS,
                        f"Invalid from_ '{op.from_}'", idx, "from_")

        # Validate stat field for ModOp and ResetOp
        if isinstance(op, (ModOp, ResetOp)) and op.stat:  # empty stat is allowed for damage marker
            self._check(op.stat in VALID_STATS,
                        f"Invalid stat '{op.stat}'", idx, "stat")

        # Validate scope field
        if hasattr(op, "scope") and isinstance(op.scope, str):
            s = op.scope
            if s:
                self._check(s in VALID_SCOPES,
                            f"Invalid scope '{s}'", idx, "scope")

        # Validate specific op types
        if isinstance(op, HitOp):
            self._check(op.type in VALID_SKILL_TYPES,
                        f"Invalid skill_type '{op.type}'", idx, "type")

        if isinstance(op, AbnormalOp) and op.name:
            self._check(len(op.name) > 0,
                        "AbnormalOp name cannot be empty", idx, "name")

        # RISC op validation
        if isinstance(op, StatStageOp) and op.stat:
            self._check(op.stat in VALID_STAGE_STATS,
                        f"Invalid stat_stage stat '{op.stat}'", idx, "stat")
        if isinstance(op, PowerModOp) and op.attr:
            self._check(op.attr in VALID_POWER_ATTRS,
                        f"Invalid power_mod attr '{op.attr}'", idx, "attr")
        if isinstance(op, MultModOp) and op.attr:
            self._check(op.attr in VALID_MULT_ATTRS,
                        f"Invalid mult_mod attr '{op.attr}'", idx, "attr")
        if isinstance(op, HealOp) and op.ratio is not None:
            self._check(0.0 <= op.ratio <= 1.0,
                        f"heal ratio must be 0-1, got {op.ratio}", idx, "ratio")
        if isinstance(op, EnergizeOp):
            pass  # delta validated by IRValue resolution
        if isinstance(op, ReviveOp):
            pass  # hp_ratio validated by IRValue resolution
        if isinstance(op, FlagSetOp) and op.flag:
            self._check(len(op.flag) > 0,
                        "flag_set flag cannot be empty", idx, "flag")

    def _validate_when_block(self, wb: WhenBlock, idx: int) -> None:
        for _j, child in enumerate(wb.then):
            self._validate_op(child, idx)
        for _j, child in enumerate(wb.else_):
            self._validate_op(child, idx)
        for branch in wb.elif_:
            for _j, child in enumerate(branch.then):
                self._validate_op(child, idx)

    def _check(self, condition: bool, message: str, idx: int, field: str) -> None:
        if not condition:
            raise ValueError(message)
