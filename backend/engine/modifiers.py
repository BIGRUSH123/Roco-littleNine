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

from backend.vm.journal import Damage, Journal, ModifierInjection

if TYPE_CHECKING:
    from backend.vm.ctx import Ctx


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
        "combo_set": 0,
        "combo_base": max(1, ctx.combo_self),
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
            elif mode == "set":
                mods["combo_set"] = int(value)
        elif stat == "power":
            if mode == "add":
                mods["power_add"] += value
            elif mode == "multiply":
                mods["power_mult"] *= value
            elif mode == "set" and value != 0:
                mods["power_base"] = value

    # Convert power_add to equivalent power_mult multiplier
    if mods.get("power_add", 0) > 0 and ctx.power_self > 0:
        effective_power = ctx.power_self + mods["power_add"]
        mods["power_mult"] *= effective_power / ctx.power_self

    # Compute same-skill damage_reduction delta (ctx already has cross-skill value)
    base_dr = ctx.damage_reduction_opp
    total_dr = mods.get("damage_reduction", base_dr)
    if total_dr > base_dr:
        mods["damage_reduction_delta"] = total_dr - base_dr

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
    combo_set = mods.get("combo_set", 0)
    combo_base = mods.get("combo_base", 1)
    damage_reduction = mods.get("damage_reduction", 0.0)

    # Only adjust for same-skill modifier deltas
    amount = dmg.amount
    amount = round(amount * power_mult * damage_mult)

    # combo: set overrides base, add adds to it
    if combo_set > 0:
        effective_combo = max(1, combo_set + combo_add)
    else:
        effective_combo = max(1, combo_base + combo_add)

    if effective_combo != combo_base and combo_base > 0:
        amount = round(amount * effective_combo / combo_base)

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


def eval_skill_where(skill_where: dict | None, skill: dict) -> bool:
    """Evaluate a skill_where condition against a single skill's properties.

    skill_where format: {"q": "energy_cost", "op": "gt", "value": 3}
    Returns True if the skill matches the condition (or no condition).
    """
    if not skill_where:
        return True
    q = skill_where.get("q", "")
    op = skill_where.get("op", "eq")
    expected = skill_where.get("value")
    actual = skill.get(q)
    if actual is None:
        return False
    if op == "gt":
        return actual > expected
    elif op == "gte":
        return actual >= expected
    elif op == "lt":
        return actual < expected
    elif op == "lte":
        return actual <= expected
    elif op == "eq":
        return actual == expected
    elif op == "neq":
        return actual != expected
    return False


def select_skills_by_element(skills: list[dict], per_element: int) -> list[dict]:
    """Select skills when element='each', taking at most per_element per element group.

    Skills are grouped by element, then the first per_element from each group
    are selected (in original order within each group).
    """
    if per_element is None or per_element <= 0:
        return list(skills)
    groups: dict[str, list[dict]] = {}
    for s in skills:
        el = s.get("element", "普通")
        groups.setdefault(el, []).append(s)
    selected = []
    for el, group in groups.items():
        selected.extend(group[:per_element])
    return selected


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
