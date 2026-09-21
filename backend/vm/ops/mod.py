"""RISC modifier opcodes — one handler per opcode, no stat dispatch.

stat_stage → StatChange；power_mod/mult_mod/flag_set → ModifierInjection；
heal → Heal/Damage；energize → EnergyChange；revive/devotion → engine-side 注入。
"""

from ..ctx import Ctx
from ..journal import (
    Damage,
    EnergyChange,
    Heal,
    ModifierInjection,
    Mutation,
    StatChange,
)
from ..resolve import resolve

_HP_MAX_MAP = {
    "sprite_self": "hp_self_max",
    "sprite_opp": "hp_opp_max",
}



def op_stat_stage(ctx: Ctx, op) -> list[Mutation]:
    """RISC: stat_stage → StatChange."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        stat = op.get("stat", "")
        scope = op.get("scope", "battlefield")
        source = op.get("source")
        per_hit = op.get("per_hit", False)
        steps = op.get("steps", 0)
        value = op.get("value")
    else:
        target = op.target
        stat = op.stat
        scope = op.scope
        source = op.source
        per_hit = op.per_hit
        steps = op.steps
        value = op.value
    if isinstance(stat, str) and stat.startswith("="):
        stat = str(resolve(ctx, stat))
    if value is not None:
        steps = int(resolve(ctx, value))
    elif steps and not isinstance(steps, int):
        steps = int(resolve(ctx, steps))
    result = [StatChange(
        target=target, stat=stat, steps=int(steps),
        scope=scope, source=source,
    )]
    if per_hit and ctx.combo_self > 1:
        result = result * ctx.combo_self
    return result


def op_power_mod(ctx: Ctx, op) -> list[Mutation]:
    """RISC: power_mod → ModifierInjection."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        attr = op.get("attr", "")
        scope = op.get("scope", "battlefield")
        per_hit = op.get("per_hit", False)
        mode = op.get("mode", "add")
        value_raw = op.get("value")
        delta_raw = op.get("delta")
        skill_filter = op.get("skill_filter")
        skill_where = op.get("skill_where")
        element = op.get("element")
        source = op.get("source")
        name = op.get("name")
        ttl = op.get("ttl", 0)
    else:
        target = op.target
        attr = op.attr
        scope = op.scope
        per_hit = op.per_hit
        mode = op.mode
        value_raw = op.value
        delta_raw = op.delta
        skill_filter = op.skill_filter
        skill_where = op.skill_where
        element = op.element
        source = op.source
        name = op.name
        ttl = op.ttl
    if value_raw is not None:
        value = resolve(ctx, value_raw)
    else:
        value = resolve(ctx, delta_raw) if delta_raw is not None else 0
    # mode:"set" 的连击数走 combo_set（绝对语义）；写进 "combo" 会被当成 +N
    stat = "combo_set" if (attr == "combo" and mode == "set") else attr
    result = [ModifierInjection(
        target=target, stat=stat, value=float(value), mode=mode,
        scope=scope,
        skill_filter=skill_filter,
        skill_where=skill_where,
        element=element,
        source=source,
        name=name,
        ttl=ttl,
        on_next=(op.get("on_next", False) if type(op) is dict
                 else getattr(op, "on_next", False)),
    )]
    if per_hit and ctx.combo_self > 1:
        result = result * ctx.combo_self
    return result


def op_mult_mod(ctx: Ctx, op) -> list[Mutation]:
    """RISC: mult_mod → ModifierInjection."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        attr = op.get("attr", "")
        scope = op.get("scope", "battlefield")
        mode = op.get("mode", "set")
        per_hit = op.get("per_hit", False)
        value_raw = op.get("value")
        skill_filter = op.get("skill_filter")
        skill_where = op.get("skill_where")
        element = op.get("element")
        source = op.get("source")
        name = op.get("name")
        on_next = op.get("on_next", False)
        if_type = op.get("if_type")
        ttl = op.get("ttl", 0)
    else:
        target = op.target
        attr = op.attr
        scope = op.scope
        mode = op.mode
        per_hit = op.per_hit
        value_raw = op.value
        skill_filter = op.skill_filter
        skill_where = op.skill_where
        element = op.element
        source = op.source
        name = op.name
        on_next = op.on_next
        if_type = op.if_type
        ttl = getattr(op, "ttl", 0)
    value = resolve(ctx, value_raw) if value_raw is not None else 1.0
    result = [ModifierInjection(
        target=target, stat=attr, value=float(value),
        mode=mode, scope=scope,
        skill_filter=skill_filter,
        skill_where=skill_where,
        element=element,
        source=source,
        name=name,
        on_next=on_next,
        if_type=if_type,
        ttl=ttl,
    )]
    if per_hit and ctx.combo_self > 1:
        result = result * ctx.combo_self
    return result


def op_flag_set(ctx: Ctx, op) -> list[Mutation]:
    """RISC: flag_set → ModifierInjection."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        flag = op.get("flag", "")
        scope = op.get("scope", "battlefield")
        value_raw = op.get("value")
        name = op.get("name")
        source = op.get("source")
        ttl = op.get("ttl", 0)
    else:
        target = op.target
        flag = op.flag
        scope = op.scope
        value_raw = op.value
        name = op.name
        source = op.source
        ttl = getattr(op, "ttl", 0)
    value = resolve(ctx, value_raw) if value_raw is not None else True
    skill_filter = (op.get("skill_filter") if type(op) is dict
                    else getattr(op, "skill_filter", None))
    skill_where = (op.get("skill_where") if type(op) is dict
                   else getattr(op, "skill_where", None))
    return [ModifierInjection(
        target=target, stat=flag, value=value,
        mode="set", scope=scope,
        name=name,
        source=source,
        ttl=ttl,
        skill_filter=skill_filter,
        skill_where=skill_where,
    )]


def op_heal(ctx: Ctx, op) -> list[Mutation]:
    """RISC: heal → Heal (or Damage if negative)."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        ratio = op.get("ratio")
        value_raw = op.get("value")
    else:
        target = op.target
        ratio = op.ratio
        value_raw = op.value
    if ratio is not None:
        hp_max_field = _HP_MAX_MAP.get(target, "hp_self_max")
        hp_max = getattr(ctx, hp_max_field, 100)
        amount = max(1, round(ratio * hp_max))
    elif value_raw is not None:
        raw = resolve(ctx, value_raw)
        if isinstance(raw, float) and 0 < raw <= 1:
            hp_max_field = _HP_MAX_MAP.get(target, "hp_self_max")
            hp_max = getattr(ctx, hp_max_field, 100)
            amount = max(1, round(float(raw) * hp_max))
        else:
            amount = int(raw)
    else:
        amount = 0
    if amount > 0:
        return [Heal(target=target, amount=amount)]
    elif amount < 0:
        return [Damage(
            target=target, amount=abs(amount),
            element=ctx.element_self, type=ctx.skill_type_self,
        )]
    return []


def op_energize(ctx: Ctx, op) -> list[Mutation]:
    """RISC: energize → EnergyChange."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        delta_raw = op.get("delta")
    else:
        target = op.target
        delta_raw = op.delta
    delta = resolve(ctx, delta_raw) if delta_raw is not None else 0
    delta = int(delta)
    if delta != 0:
        return [EnergyChange(target=target, delta=delta)]
    return []


def op_revive(ctx: Ctx, op) -> list[Mutation]:
    """RISC: revive → ModifierInjection (engine handles revive logic)."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        hp_ratio_raw = op.get("hp_ratio")
    else:
        target = op.target
        hp_ratio_raw = op.hp_ratio
    hp_ratio = resolve(ctx, hp_ratio_raw) if hp_ratio_raw is not None else 1.0
    return [ModifierInjection(
        target=target, stat="revive", value=float(hp_ratio),
        mode="set",
    )]


def op_devotion(ctx: Ctx, op) -> list[Mutation]:
    """RISC: devotion → ModifierInjection (stat="devotion", with name/then/ttl)."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        value_raw = op.get("value", 1)
        mode = op.get("mode", "add")
        scope = op.get("scope", "persistent")
        name = op.get("name")
        then = op.get("then")
        ttl = op.get("ttl", 0)
        source = op.get("source")
    else:
        target = op.target
        value_raw = op.value
        mode = op.mode
        scope = op.scope
        name = op.name
        then = op.then
        ttl = op.ttl
        source = op.source
    value = float(resolve(ctx, value_raw)) if value_raw is not None else 1.0
    return [ModifierInjection(
        target=target, stat="devotion", value=value,
        scope=scope, mode=mode, name=name, then=then, ttl=ttl, source=source,
    )]
