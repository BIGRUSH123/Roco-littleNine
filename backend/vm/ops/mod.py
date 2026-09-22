"""RISC modifier opcodes — one handler per opcode, no stat dispatch.

stat_stage → StatChange；power_mod/mult_mod/flag_set → ModifierInjection；
heal → Heal/Damage；energize → EnergyChange；revive/devotion → engine-side 注入。
stat_random / stat_convert 是同族的两条专用指令（随机分配 / 正负翻转）。
"""

from ..ctx import Ctx
from ..journal import (
    Damage,
    EnergyChange,
    Heal,
    ModifierInjection,
    Mutation,
    StatChange,
    StatConvert,
    StatRandom,
)
from ..resolve import resolve

_HP_MAX_MAP = {
    "sprite_self": "hp_self_max",
    "sprite_opp": "hp_opp_max",
}

#: stat_random 的默认分配维度（五维，与 3014/3018 的属性范围一致）
_RANDOM_STATS: tuple[str, ...] = ("atk", "def", "sp_atk", "sp_def", "speed")


def op_stat_random(ctx: Ctx, op) -> list[Mutation]:
    """RISC: stat_random → StatRandom（分配在 replayer 落地，需随机源）。"""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        layers = op.get("layers", op.get("steps", 0))
        direction = op.get("direction", "positive")
        stats = op.get("stats") or ()
        scope = op.get("scope", "battlefield")
        source = op.get("source")
    else:
        target = op.target
        # value（Query/RefExpr）优先于静态 layers（动态层数，如 计数器×3）
        layers = op.value if op.value is not None else op.layers
        direction = op.direction
        stats = op.stats or ()
        scope = op.scope
        source = op.source
    layers = int(resolve(ctx, layers))
    if direction not in ("positive", "negative"):
        direction = "positive"
    # 负值层数 = 反方向（数据面写 layers:-16 也能落到减益）
    if layers < 0:
        layers = -layers
        direction = "negative" if direction == "positive" else "positive"
    return [StatRandom(
        target=target, layers=layers, direction=direction,
        stats=tuple(stats) or _RANDOM_STATS,
        scope=scope, source=source or "",
    )]


def op_stat_convert(ctx: Ctx, op) -> list[Mutation]:
    """RISC: stat_convert → StatConvert（正负翻转，层数不变）。"""
    if type(op) is dict:
        target = op.get("target", "sprite_opp")
        from_ = op.get("from", "positive")
        to = op.get("to", "")
        name = op.get("name") or ""
        source = op.get("source")
    else:
        target = op.target
        from_ = op.from_
        to = op.to
        name = op.name or ""
        source = op.source
    if from_ not in ("positive", "negative"):
        from_ = "positive"
    if to not in ("positive", "negative"):
        to = "negative" if from_ == "positive" else "positive"
    return [StatConvert(
        target=target, from_=from_, to=to, name=name, source=source or "",
    )]



def op_stat_stage(ctx: Ctx, op) -> list[Mutation]:
    """RISC: stat_stage → StatChange."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        stat = op.get("stat", "")
        scope = op.get("scope", "battlefield")
        source = op.get("source")
        steps = op.get("steps", 0)
        value = op.get("value")
    else:
        target = op.target
        stat = op.stat
        scope = op.scope
        source = op.source
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
    # 连击技能的效果一律按「每次释放」各结算一次（连击 = 释放次数）
    if ctx.combo_self > 1:
        result = result * ctx.combo_self
    return result


def op_power_mod(ctx: Ctx, op) -> list[Mutation]:
    """RISC: power_mod → ModifierInjection."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        attr = op.get("attr", "")
        scope = op.get("scope", "battlefield")
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
    # 自技能参数修正（target: skill_off_0 的威力/能耗/连击/优先级…）整次使用只结算一次：
    # 连击数是释放次数，这些参数描述的是「单次释放」（引雷「迸发：本次技能威力+20」
    # = 2 段各 55 威力，不是 55+75）；其余效果（如冰捆缚给对方全技能能耗+1）按次结算
    self_param = (target == "skill_off_0" and stat in (
        "power", "power_mult", "energy_cost", "priority",
        "combo", "combo_set", "combo_mult", "use_count_bonus",
    ))
    if not self_param and ctx.combo_self > 1:
        result = result * ctx.combo_self
    return result


def op_mult_mod(ctx: Ctx, op) -> list[Mutation]:
    """RISC: mult_mod → ModifierInjection."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        attr = op.get("attr", "")
        scope = op.get("scope", "battlefield")
        mode = op.get("mode", "set")
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
    self_param = (target == "skill_off_0" and attr in (
        "power", "power_mult", "energy_cost", "priority",
        "combo", "combo_set", "combo_mult", "use_count_bonus",
    ))
    if not self_param and ctx.combo_self > 1:
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
    # 治疗也按每次释放各结算一次（聚盐「2连击，每次连击自己回复8%生命」= 16%）
    combo = max(1, ctx.combo_self)
    if amount > 0:
        return [Heal(target=target, amount=amount)] * combo
    elif amount < 0:
        return [Damage(
            target=target, amount=abs(amount),
            element=ctx.element_self, type=ctx.skill_type_self,
        )] * combo
    return []


def op_energize(ctx: Ctx, op) -> list[Mutation]:
    """RISC: energize → EnergyChange."""
    if type(op) is dict:
        target = op.get("target", "sprite_self")
        delta_raw = op.get("delta")
        overflow = bool(op.get("overflow", False))
    else:
        target = op.target
        delta_raw = op.delta
        overflow = bool(getattr(op, "overflow", False))
    delta = resolve(ctx, delta_raw) if delta_raw is not None else 0
    delta = int(delta)
    # overflow=true：回复可突破 max_energy 上限（盗魂铃）；扣除不受影响
    if delta != 0:
        return [EnergyChange(target=target, delta=delta,
                             overflow=bool(overflow and delta > 0))]
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
