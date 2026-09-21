"""EffectFactory — deserialize JSON effect dicts to EffectObject instances.

Pure function: from_dict(dict, source) -> EffectObject | None.
Returns None for opcodes that don't need objectification (raw VM ops like hit, mark, etc.).

也接受**已编译的 typed op**（dataclass）：字段名与 JSON 键同名（`from_` ↔ `from`），
因此 `inherit` op 的 `effects[]` 无论来自技能 JSON（已被 SkillParsePass 编译）
还是特性 JSON（仍是 dict）都能走同一条落地路径。
"""

from __future__ import annotations

import dataclasses
import re

from .effect import AbnormalEffect, EffectObject, MarkEffect, ModifierEffect, ObserverEffect

#: typed op 类名 → 数据面 op 名。通用规则是 CamelCase → snake_case，
#: 下面这些是「类名 ≠ op 名」的例外（与 skill_parse 的 `_parse_<op>` 方法名对齐）。
_OP_ALIASES: dict[str, str] = {
    "InheritEffects": "inherit",
    "TeamCounterWrite": "team_counter",
    "LivesChange": "lives",
    "Schedule": "defer",
    "ReplayChoiceOp": "replay_branch",
    "BurstGrantOp": "burst_grant",
}


def _normalize_listen(listen) -> frozenset:
    """Accept string, list, or None; always return frozenset."""
    if listen is None:
        return frozenset()
    if isinstance(listen, str):
        return frozenset({listen})
    if isinstance(listen, (list, tuple, set, frozenset)):
        return frozenset(listen)
    return frozenset()


def _op_name(d) -> str:
    """typed op → 数据面 op 名（dict 走 op 键，typed op 由类名反推）。"""
    class_name = type(d).__name__
    if class_name in _OP_ALIASES:
        return _OP_ALIASES[class_name]
    if class_name.endswith("Op"):
        class_name = class_name[:-2]
    return re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()


def _as_effect_dict(d):
    """dict 原样返回；typed op（frozen slots dataclass）转成等价的字段 dict。

    typed op 没有 `op` 字段，由类名反推（见 `_op_name`），使技能 JSON 里已被
    SkillParsePass 编译过的 `effects[]`（如 inherit 的携带效果）与特性 JSON 里
    仍是 dict 的写法都能走同一条落地路径。
    """
    if isinstance(d, dict):
        return d
    if dataclasses.is_dataclass(d) and not isinstance(d, type):
        out: dict = {"op": _op_name(d)}
        for f in dataclasses.fields(d):
            key = "from" if f.name == "from_" else f.name
            out[key] = getattr(d, f.name)
        return out
    return {}


def from_dict(d, *, source: str = "") -> EffectObject | None:
    """Convert a JSON effect dict (or a compiled typed op) to an EffectObject.

    Returns None for opcodes handled directly by the VM (hit, mark, etc.)
    that don't need identity wrapping.
    """
    d = _as_effect_dict(d)
    op = d.get("op", "")
    scope = d.get("scope", "battlefield")
    ttl = d.get("ttl", 0)
    name = d.get("name", "")

    # ── ObserverEffect ──
    if op == "observer":
        return ObserverEffect(
            name=name or source,
            source=source,
            scope=d.get("scope", "persistent"),
            ttl=ttl,
            cond=d.get("cond", {}),
            then=d.get("then", []),
            listen=_normalize_listen(d.get("listen")),
            threshold=d.get("threshold", 1),
            reset_on_fire=d.get("reset_on_fire", True),
        )

    # ── ModifierEffect (power_mod / mult_mod / stat_stage) ──
    if op in ("power_mod", "mult_mod", "stat_stage"):
        attr = d.get("attr", d.get("stat", ""))
        value = d.get("value", d.get("delta", d.get("steps", 0)))
        # Immunity attrs: preserve raw name from JSON (empty = blanket immunity)
        effective_name = name if attr.startswith("immune_") else (name or f"{source}-{attr}")
        return ModifierEffect(
            name=effective_name,
            source=source,
            scope=scope,
            ttl=ttl,
            target=d.get("target", "sprite_self"),
            attr=attr,
            value=value,
            mode=d.get("mode", "add"),
            skill_where=d.get("skill_where"),
        )

    # ── AbnormalEffect ──
    if op == "abnormal":
        from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
        template = ABNORMAL_TEMPLATES.get(name, ABNORMAL_TEMPLATES.get(d.get("template", "")))
        if template is not None:
            return AbnormalEffect(
                name=name or template.name,
                source=source,
                scope=d.get("scope", template.scope),
                ttl=d.get("ttl", template.ttl),
                stacks=d.get("stacks", 0),
                tick_damage_pct=d.get("tick_damage_pct", template.tick_damage_pct),
                tick_element=d.get("tick_element", template.tick_element),
                decay_on_tick=d.get("decay_on_tick", template.decay_on_tick),
                max_stacks=d.get("max_stacks", template.max_stacks),
                tick_per_stack=d.get("tick_per_stack", template.tick_per_stack),
                # 情报遮蔽状态（木桶/月陨星）的解除时机随模板带上
                release_on_action=getattr(template, "release_on_action", False),
                release_on_damage=getattr(template, "release_on_damage", False),
            )
        return AbnormalEffect(
            name=name or source,
            source=source,
            scope=scope,
            ttl=ttl,
            stacks=d.get("stacks", 0),
        )

    # ── MarkEffect ──
    if op == "mark":
        from backend.engine.mark_config import MARK_TEMPLATES
        template = MARK_TEMPLATES.get(name)
        if template is not None:
            return MarkEffect(
                name=name or template.name,
                source=source,
                scope=d.get("scope", template.scope),
                ttl=d.get("ttl", template.ttl),
                stacks=d.get("stacks", 0),
                category=d.get("category", template.category),
                power_bonus=d.get("power_bonus", template.power_bonus),
                damage_mult=d.get("damage_mult", template.damage_mult),
                speed_penalty=d.get("speed_penalty", template.speed_penalty),
                energy_mod=d.get("energy_mod", template.energy_mod),
                turn_end_energy=d.get("turn_end_energy", template.turn_end_energy),
                turn_end_damage_pct=d.get("turn_end_damage_pct", template.turn_end_damage_pct),
                switch_damage_pct=d.get("switch_damage_pct", template.switch_damage_pct),
                switch_energy_loss=d.get("switch_energy_loss", template.switch_energy_loss),
                starfall_damage=d.get("starfall_damage", template.starfall_damage),
                leave_random_debuffs=d.get("leave_random_debuffs", template.leave_random_debuffs),
                buff_bonus_layers=d.get("buff_bonus_layers", template.buff_bonus_layers),
                condition=d.get("condition", template.condition),
            )
        return MarkEffect(
            name=name or source,
            source=source,
            scope=scope,
            ttl=ttl,
            stacks=d.get("stacks", 0),
            category=d.get("category", "negative"),
        )

    # ── Raw VM ops (hit, mark, abnormal, heal, etc.) — no wrapper needed ──
    return None
