"""Modifier collector — compute effective modifier values from Journal.

After VM execution, the engine scans the journal for ModifierInjections,
computes effective values for each modifier category, and adjusts Damage
mutations accordingly. This bridges the gap between same-skill modifier
effects and the damage formula.

The VM is pure: op_hit only sees the pre-execution Ctx snapshot. Effects
that produce power_mult/damage_mult/etc. are ModifierInjections in the
journal — the engine must collect them and apply them to Damage.

Cross-skill modifiers (damage_reduction, stat_stages) are already in Ctx
via the snapshot → replayer → _modifiers loop.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from scripts.vm.journal import Journal, Mutation, Damage, ModifierInjection

if TYPE_CHECKING:
    from scripts.vm.ctx import Ctx


def collect_modifiers(journal: Journal, ctx: Ctx) -> dict:
    """Collect ModifierInjections from journal and compute effective values.

    Returns a dict of modifier category → effective value. Uses ctx for
    base values and the journal for same-skill adjustments.
    """
    mods: dict[str, float] = {
        "power_mult": 1.0,
        "damage_mult": 1.0,
        "damage_reduction": ctx.damage_reduction_opp,
        "combo_add": 0,
        "power_add": 0,
    }

    for m in journal:
        if not isinstance(m, ModifierInjection):
            continue
        stat = m.stat
        value = m.value
        mode = m.mode

        if stat == "power_mult":
            mods["power_mult"] *= value
        elif stat == "damage_mult":
            mods["damage_mult"] *= value
        elif stat == "damage_reduction":
            if mode == "add":
                mods["damage_reduction"] = min(1.0, mods["damage_reduction"] + value)
            elif mode == "set":
                mods["damage_reduction"] = value
            elif mode == "multiply":
                mods["damage_reduction"] = 1.0 - (1.0 - mods["damage_reduction"]) * (1.0 - value)
        elif stat == "combo":
            if mode == "add":
                mods["combo_add"] += int(value)
        elif stat == "power":
            if mode == "add":
                mods["power_add"] += value
            elif mode == "multiply":
                mods["power_mult"] *= value
            elif mode == "set" and value != 0:
                mods["power_base"] = value

    return mods


def adjust_damage(dmg: Damage, mods: dict) -> Damage:
    """Apply collected modifiers to a Damage mutation.

    power_mult, damage_mult, and combo_add are applied multiplicatively.
    damage_reduction is skipped here because op_hit already applied the
    Ctx snapshot value — only same-skill ModifierInjections of
    damage_reduction need to be accounted for.
    """
    power_mult = mods.get("power_mult", 1.0)
    damage_mult = mods.get("damage_mult", 1.0)
    combo_add = mods.get("combo_add", 0)
    damage_reduction = mods.get("damage_reduction", 0.0)

    # Only adjust for same-skill modifier deltas
    amount = dmg.amount
    amount = round(amount * power_mult * damage_mult)

    # combo_add: additional hits multiply damage
    if combo_add > 0:
        amount = round(amount * (1 + combo_add))

    # Only apply extra damage_reduction beyond what op_hit already applied
    # (op_hit uses ctx snapshot damage_reduction; same-skill mods add extra)
    extra_dr = mods.get("damage_reduction_delta", 0.0)
    if extra_dr > 0:
        amount = round(amount * (1.0 - extra_dr))

    return Damage(
        target=dmg.target,
        amount=max(1, amount),
        element=dmg.element,
        type=dmg.type,
    )


def apply_modifiers_to_journal(journal: Journal, ctx: Ctx) -> Journal:
    """Scan journal, collect modifiers, and adjust all Damage mutations.

    Returns a new Journal with adjusted Damage amounts. Non-Damage
    mutations pass through unchanged.
    """
    mods = collect_modifiers(journal, ctx)
    result: Journal = []
    for m in journal:
        if isinstance(m, Damage):
            result.append(adjust_damage(m, mods))
        else:
            result.append(m)
    return result
