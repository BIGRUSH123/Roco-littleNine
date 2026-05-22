"""Shared effect application module — used by both skill VM and trait engine.

Extracts stat/abnormal/mark/weather/special effect application logic from
backend.sim.traits.trait_engine.DataDrivenTrait._apply_effect(),
replacing raw dict dispatch with typed IR value matching.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.vm.ir_trait import (
    TraitAbnormalEffect,
    TraitMarkEffect,
    TraitSpecialEffect,
    TraitStatEffect,
    TraitWeatherEffect,
)
from backend.vm.ir_values import IRValue, Literal, Query, RefExpr

if TYPE_CHECKING:
    pass


# ── IRValue resolution ──

def resolve_value(v: IRValue | None, ctx: dict | None = None, default=0) -> int | float | str | bool:
    """Resolve an IRValue to a concrete Python value.

    - Literal → .value
    - Query → look up .field from ctx dict (caller must provide)
    - RefExpr → resolve path expression from ctx
    - None → default
    """
    if v is None:
        return default
    if isinstance(v, Literal):
        return v.value
    if isinstance(v, Query):
        if ctx is not None:
            sprite = ctx.get("self") or ctx.get("target")
            if sprite is not None:
                val = getattr(sprite, v.field, v.default)
                if val is None:
                    val = v.default
            else:
                val = ctx.get(v.field, v.default)
            if val is None:
                val = v.default or 0
            if isinstance(val, (int, float)):
                return float(val) * v.scale + v.offset
            return val
        return v.default if v.default is not None else 0
    if isinstance(v, RefExpr):
        if ctx is not None:
            root = ctx.get(v.root)
            if root is not None:
                val = _walk_path(root, v.path)
                if val is not None and isinstance(val, (int, float)):
                    return float(val) * v.multiplier + v.offset
                return val if val is not None else 0
        return 0
    return v


def _walk_path(obj, path: list[str]):
    """Walk a dotted attribute path on an object."""
    current = obj
    for attr in path:
        if current is None:
            return None
        current = getattr(current, attr, None)
    return current


# ── Stat label mapping ──

_LABEL_MAP: dict[str, str] = {
    "atk": "物攻", "sp_atk": "魔攻", "def": "物防", "sp_def": "魔防",
    "speed": "速度", "power": "威力", "priority": "先手",
    "energy_cost": "能耗", "combo": "连击", "life_drain": "吸血",
}

# Step unit: non-pct stats use direct step values; pct stats use 10% per step
_STEP_UNIT: dict[str, int] = {
    "power": 10, "priority": 1, "energy_cost": 1,
    "combo": 1, "life_drain": 10, "speed": 10,
}

def _format_stat_label(stat_key: str, steps: int) -> str:
    """Format the display name for a stat effect, e.g. '物攻+20%' or '威力+30'.

    Matches the original trait_engine display logic:
    - priority/energy_cost/combo: display raw steps (no unit, no %)
    - power: steps * 10 (no %)
    - speed & six-stats: steps * 10% (with %)
    """
    label = _LABEL_MAP.get(stat_key, stat_key)
    sign = "+" if steps > 0 else ""
    unit = _STEP_UNIT.get(stat_key, 10)
    if stat_key in ("priority", "energy_cost", "combo"):
        return f"{label}{sign}{steps}"
    elif stat_key == "power":
        return f"{label}{sign}{steps * unit}"
    else:
        return f"{label}{sign}{steps * unit}%"


# ── Main dispatch ──

def apply_effect(
    effect,
    target_sprite,
    battle,
    team: str,
    ctx: dict | None = None,
) -> list[str]:
    """Apply a single typed effect to target_sprite.

    Returns a list of event description strings (Chinese log lines).

    Parameters
    ----------
    effect : TraitStatEffect | TraitAbnormalEffect | TraitMarkEffect
             | TraitWeatherEffect | TraitSpecialEffect
        The typed effect to apply.
    target_sprite : Sprite or None
        The primary target sprite. May be None for team-scoped effects (mark/weather).
    battle : Battle or None
        The battle context. Required for mark, weather, and some special effects.
    team : str
        "A" or "B" — the team letter of the effect source.
    ctx : dict or None
        Optional context for resolving Query/RefExpr IRValues.
        Expected keys: 'self', 'target', 'battle', etc.
    """
    if ctx is None:
        ctx = {}

    if target_sprite is None and battle is None:
        return []

    # ── Stat effect ──
    if isinstance(effect, TraitStatEffect):
        return _apply_stat_effect(effect, target_sprite, ctx)

    # ── Abnormal effect ──
    if isinstance(effect, TraitAbnormalEffect):
        return _apply_abnormal_effect(effect, target_sprite, ctx)

    # ── Mark effect ──
    if isinstance(effect, TraitMarkEffect):
        return _apply_mark_effect(effect, battle, team)

    # ── Weather effect ──
    if isinstance(effect, TraitWeatherEffect):
        return _apply_weather_effect(effect, battle)

    # ── Special effect ──
    if isinstance(effect, TraitSpecialEffect):
        return _apply_special_effect(effect, target_sprite, battle, team, ctx)

    return []


# ── Effect sub-handlers ──

def _apply_stat_effect(
    effect: TraitStatEffect, sprite, ctx: dict,
) -> list[str]:
    """Apply a stat change to the target sprite."""
    if sprite is None:
        return []

    steps = resolve_value(effect.steps, ctx, 0)
    if not isinstance(steps, (int, float)):
        steps = 0
    steps = int(steps)

    stat_key = effect.stat
    scope = effect.scope
    source = effect.source or None

    display = _format_stat_label(stat_key, steps)

    from backend.sim.sprite import StatusEffect
    se = StatusEffect(
        name=display, category="stat", stat_key=stat_key,
        steps=steps, scope=scope, source=source,
    )
    sprite.add_effect(se)
    return [f"{sprite.name} {display}"]


def _apply_abnormal_effect(
    effect: TraitAbnormalEffect, sprite, ctx: dict,
) -> list[str]:
    """Apply an abnormal status to the target sprite."""
    if sprite is None:
        return []

    name = effect.name
    stacks = resolve_value(effect.stacks, ctx, 1)
    if not isinstance(stacks, (int, float)):
        stacks = 1
    stacks = int(stacks)

    # 萌化 is a special abnormal that triggers morphological regression
    if name == '萌化':
        battle = ctx.get('battle') if ctx else None
        if battle:
            return sprite.apply_moe(stacks, battle)
        return [f"{sprite.name} 萌化失败(缺少battle上下文)"]

    scope = effect.scope
    source = effect.source or None

    from backend.sim.sprite import StatusEffect
    se = StatusEffect(
        name=name, category="abnormal", stacks=stacks,
        scope=scope, source=source,
    )
    sprite.add_effect(se)
    total = sprite.get_stacks(name)
    return [f"{sprite.name} {name}+{stacks}(共{total}层)"]


def _apply_mark_effect(
    effect: TraitMarkEffect, battle, team: str,
) -> list[str]:
    """Apply a mark to a team."""
    if battle is None:
        return []

    name = effect.name
    stacks = effect.stacks or 1
    mark_target = effect.mark_target

    opp_team = "B" if team == "A" else "A"
    actual = team if mark_target == "own_team" else opp_team

    category = battle.globals.classify_mark(name)
    battle.globals.apply_mark(actual, name, category, stacks)
    return [f"{actual}方 {name}+{stacks}"]


def _apply_weather_effect(
    effect: TraitWeatherEffect, battle,
) -> list[str]:
    """Set the weather."""
    if battle is None:
        return []

    weather = effect.weather
    turns = effect.turns or 8

    battle.globals.set_weather(weather, turns)
    return [f"天气→{weather}({turns}回合)"]


def _apply_special_effect(
    effect: TraitSpecialEffect, sprite, battle, team: str, ctx: dict,
) -> list[str]:
    """Handle special effects: heal, gain_energy, steal_energy, etc."""
    name = effect.name
    value = resolve_value(effect.value, ctx, 0)
    amount = resolve_value(effect.amount, ctx, 0)

    # ── Heal ──
    if name == "heal":
        if sprite is None:
            return []
        pct = value or (amount / sprite.max_hp if amount and sprite.max_hp else 0)
        amt = round(sprite.max_hp * pct) if pct else amount
        healed = sprite.heal(int(amt)) if amt else 0
        return [f"{sprite.name} 回复+{healed}HP"] if healed else []

    if name == "direct_heal":
        if sprite is None:
            return []
        healed = sprite.heal(int(amount or 0))
        return [f"{sprite.name} 回复+{healed}HP"] if healed else []

    # ── Energy ──
    if name == "gain_energy":
        if sprite is None:
            return []
        gained = sprite.gain_energy(int(amount or 0))
        return [f"{sprite.name} 回复+{gained}E"] if gained else []

    if name == "energy_set":
        if sprite is None:
            return []
        sprite.energy = int(amount)
        return [f"{sprite.name} 能量={amount}"]

    if name == "steal_energy":
        if sprite is None:
            return []
        target = ctx.get("target")
        if target:
            amt = int(amount or 1)
            stolen = target.lose_energy(amt)
            sprite.gain_energy(stolen)
            return [f"{sprite.name} 偷取{stolen}E"]
        return []

    if name == "steal_energy_all":
        if battle is None:
            return []
        opp = battle.get_opponent(team)
        events = []
        for s in opp.team:
            if not s.is_fainted:
                lost = s.lose_energy(int(amount or 1))
                if lost:
                    events.append(f"{sprite.name} 偷取{s.name} {lost}E")
        return events

    if name == "lose_energy":
        if sprite is None:
            return []
        lost = sprite.lose_energy(int(amount or 1))
        return [f"{sprite.name} -{lost}E"] if lost else []

    # ── Lives ──
    if name == "lives_delta":
        if battle is None:
            return []
        target_team = getattr(effect, 'target_team', 'own')
        t = ("B" if team == "A" else "A") if target_team == "opp" else team
        p = battle.get_player(t)
        if p is not None:
            delta = int(amount or 0)
            if delta < 0 and p.lives <= 0:
                return []
            p.lives += delta
            label = f"奉献{delta}" if delta > 0 else f"魔力{delta}"
            return [f"{sprite.name} {label}"]
        return []

    if name == "lives_add":
        if battle is None:
            return []
        player = battle.get_player(team)
        if player is not None:
            player.lives += int(amount or 1)
            return [f"{sprite.name} 奉献+{amount or 1}"]
        return []

    # ── Damage ──
    if name == "take_damage":
        dmg = value or amount
        target_key = getattr(effect, 'target', 'self')
        if target_key == "self":
            dmg_target = sprite
        else:
            dmg_target = ctx.get(target_key) if ctx else None
        if dmg_target and dmg:
            actual = dmg_target.take_damage(int(dmg))
            return [f"{dmg_target.name} -{actual}HP"]
        return []

    # ── Mark operations ──
    if name == "dispel_mark":
        return _apply_dispel_mark(effect, battle, team, ctx)
    if name == "steal_mark":
        return _apply_steal_mark(effect, battle, team, ctx)
    if name == "convert_mark":
        return _apply_convert_mark(effect, battle, team, ctx)

    # ── Inherit effects ──
    if name == "inherit_effects":
        return _apply_inherit_effects(effect, battle, team, ctx)

    # ── Team counter ──
    if name == "team_counter_add":
        return _apply_team_counter_add(effect, battle, team)

    # ── Unknown ──
    return []


# ── Mark operation helpers ──

def _apply_dispel_mark(effect, battle, team, ctx) -> list[str]:
    if battle is None:
        return []
    mark_team_key = getattr(effect, 'target_team', 'own')
    t = ("B" if team == "A" else "A") if mark_team_key == "opp" else team
    count = resolve_value(effect.amount, ctx, 1) if effect.amount is not None else 1
    count = int(count) if isinstance(count, (int, float)) else 1
    pos, neg = battle.globals.get_marks(t)
    all_marks = pos + neg
    for m in all_marks:
        if m.stacks > 0:
            removed = min(m.stacks, count)
            m.stacks -= removed
            label = getattr(ctx.get("self"), "name", "?") if ctx else "?"
            return [f"{label} 驱散{t}方{m.name}×{removed}"]
    return []


def _apply_steal_mark(effect, battle, team, ctx) -> list[str]:
    if battle is None:
        return []
    opp_team = "B" if team == "A" else "A"
    count = resolve_value(effect.amount, ctx, 1) if effect.amount is not None else 1
    count = int(count) if isinstance(count, (int, float)) else 1
    pos, neg = battle.globals.get_marks(opp_team)
    all_marks = pos + neg
    for m in all_marks:
        if m.stacks > 0:
            removed = min(m.stacks, count)
            m.stacks -= removed
            category = battle.globals.classify_mark(m.name)
            battle.globals.apply_mark(team, m.name, category, removed)
            label = getattr(ctx.get("self"), "name", "?") if ctx else "?"
            return [f"{label} 偷取{m.name}×{removed}"]
    return []


def _apply_convert_mark(effect, battle, team, ctx) -> list[str]:
    if battle is None:
        return []
    target = (ctx.get("target") or ctx.get("self")) if ctx else None
    if target is None:
        return []
    source_name = getattr(effect, 'name', '')
    # In TraitSpecialEffect, name is the special effect name ("convert_mark"),
    # so we need to read extra fields. Since convert_mark is currently
    # dispatched by name="convert_mark", we use effect attributes directly.
    mark_name = getattr(effect, 'mark_name', source_name)
    ratio = getattr(effect, 'ratio', 1.0)

    effects_list = [e for e in target.effects
                    if getattr(e, 'category', '') == 'abnormal'
                    and getattr(e, 'name', '') == source_name]
    total_stacks = sum(getattr(e, 'stacks', 0) for e in effects_list)
    if total_stacks <= 0:
        return []

    marks = max(1, int(total_stacks * ratio))
    consumed = int(marks / ratio) if ratio > 0 else total_stacks
    for e in effects_list:
        remove_stacks = min(getattr(e, 'stacks', 0), consumed)
        e.stacks -= remove_stacks
        consumed -= remove_stacks
        if consumed <= 0:
            break

    mark_team_key = getattr(effect, 'target_team', 'opp')
    mark_team = ("B" if team == "A" else "A") if mark_team_key == "opp" else team
    category = battle.globals.classify_mark(mark_name)
    battle.globals.apply_mark(mark_team, mark_name, category, marks)
    return [f"{target.name} {source_name}→{mark_name}×{marks}"]


def _apply_inherit_effects(effect, battle, team, ctx) -> list[str]:
    if battle is None:
        return []
    source_key = getattr(effect, 'target', 'self')
    inherit_target_key = getattr(effect, 'inherit_target', 'enemy_new') if hasattr(effect, 'inherit_target') else 'enemy_new'
    scope = getattr(effect, 'scope', 'battlefield') if hasattr(effect, 'scope') else 'battlefield'
    via_pending = getattr(effect, 'via_pending', False) if hasattr(effect, 'via_pending') else False

    source_sprite = ctx.get(source_key) if ctx else None
    target_sprite = ctx.get(inherit_target_key) if ctx else None

    if source_sprite is None:
        return []
    if not via_pending and target_sprite is None:
        return []

    inherited = [e for e in source_sprite.effects if getattr(e, 'scope', '') == scope]
    if not inherited:
        return []

    if via_pending:
        battle.pending_effects.setdefault(team, [])
        battle.pending_effects[team].extend(inherited)
        return [f"{source_sprite.name}→next({team}) 继承{len(inherited)}个效果"]
    else:
        for e in inherited:
            target_sprite.add_effect(e)
        return [f"{source_sprite.name}→{target_sprite.name} 继承{len(inherited)}个效果"]


def _apply_team_counter_add(effect, battle, team) -> list[str]:
    if battle is None:
        return []
    key = getattr(effect, 'key', '')
    delta = resolve_value(effect.amount, None, 1) if effect.amount is not None else 1
    delta = int(delta) if isinstance(delta, (int, float)) else 1
    target_team = getattr(effect, 'target_team', 'own')
    t = ("B" if team == "A" else "A") if target_team == "opp" else team
    battle.inc_team_counter(t, key, delta)
    return []
