"""aura / element_convert / morph / grant_choice / counter opcodes.

这些 opcode 把「持续型/授予型」机制表达成 IR，落到统一的机制声明 mutation：

    aura            按命名计数源持续重算的属性修饰（和弦共振/守护之心/先知）
    element_convert 在场时把命中技能的属性转换为另一属性（展翅）
    morph           巧变授予：使用后变为某类别的技能（换碟/魔术帽）
    grant_choice    给命中技能/聚能行动追加可选分支（异类/长久保存制法）
    counter         精灵级计数器读改（草木苏醒时 等累积-重置模式）

机制本身由 backend/engine/mechanisms.py 与 backend/engine/morph.py 提供，
本层只负责把 IR 转成 mutation，保持数据面与引擎解耦。
"""

from ..ctx import Ctx
from ..journal import CounterWrite, MechanismGrant, Mutation
from ..resolve import resolve


def _get(effect, key, default=None):
    if isinstance(effect, dict):
        return effect.get(key, default)
    return getattr(effect, key, default)


def _grant(effect, mechanism: str, payload: dict) -> list[Mutation]:
    return [MechanismGrant(
        target=_get(effect, "target", "sprite_self"),
        mechanism=mechanism,
        payload=payload,
        affects=_get(effect, "affects", "self"),
        scope=_get(effect, "scope", "battlefield"),
        source=_get(effect, "source") or "",
    )]


def op_aura(ctx: Ctx, effect) -> list[Mutation]:
    """注册属性光环：stat += per_unit × count(计数源)。"""
    return _grant(effect, "aura", {
        "stat": _get(effect, "stat", ""),
        "count": _get(effect, "count", ""),
        "per_unit": int(_get(effect, "per_unit", 1) or 1),
        "count_params": _get(effect, "count_params"),
    })


def op_element_convert(ctx: Ctx, effect) -> list[Mutation]:
    return _grant(effect, "element_convert", {
        "from": _get(effect, "from", "") or _get(effect, "from_element", ""),
        "to": _get(effect, "to", "") or _get(effect, "to_element", ""),
        "skill_filter": _get(effect, "skill_filter"),
        "skill_where": _get(effect, "skill_where"),
    })


def op_morph(ctx: Ctx, effect) -> list[Mutation]:
    return _grant(effect, "morph", {
        "category": _get(effect, "category", "same_element"),
        "skill_filter": _get(effect, "skill_filter"),
        "skill_where": _get(effect, "skill_where"),
    })


def op_grant_choice(ctx: Ctx, effect) -> list[Mutation]:
    return _grant(effect, "grant_choice", {
        "choices": tuple(_get(effect, "choices", ()) or ()),
        "action": _get(effect, "action", ""),
        "name": _get(effect, "name", ""),
        "skill_filter": _get(effect, "skill_filter"),
        "skill_where": _get(effect, "skill_where"),
        "element": _get(effect, "element"),
    })


def op_counter(ctx: Ctx, effect) -> list[Mutation]:
    """精灵级计数器：mode="set" 覆盖，mode="add" 累加。"""
    mode = _get(effect, "mode", "add")
    raw = _get(effect, "value") if mode == "set" else _get(effect, "delta")
    if raw is None:
        raw = _get(effect, "delta", 0) if mode == "set" else _get(effect, "value", 0)
    return [CounterWrite(
        target=_get(effect, "target", "sprite_self"),
        key=_get(effect, "key", ""),
        delta=int(resolve(ctx, raw) or 0),
        mode=mode,
    )]
