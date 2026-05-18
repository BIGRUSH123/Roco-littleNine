"""Trait trigger executor — called by trait_engine's _fire().

Processes a single TraitTrigger against a context dict, delegating
effect application to backend.vm.effect_applier.apply_effect().
"""

from __future__ import annotations
import random
from typing import Any

from backend.vm.ir_trait import (
    TraitTrigger, TraitEffect,
    TraitStatEffect, TraitAbnormalEffect, TraitMarkEffect,
    TraitWeatherEffect, TraitSpecialEffect,
    MutateEffectOp, RemoveEffectOp,
    ScheduleOp, InheritEffectsOp, TeamCounterOp,
    TransformOp, TraitInteractionOp, LivesOp,
    BattleSkillMutOp,
)
from backend.vm.ir_values import Literal, Query, RefExpr, IRValue
from backend.vm.effect_applier import apply_effect, resolve_value
from backend.vm.cond_path import eval_path_cond


# ── IRValue resolution helper ──

def _resolve_ir_value(v: IRValue | Any, ctx: dict, default=0) -> Any:
    """Resolve an IRValue or raw value to a concrete Python value."""
    if isinstance(v, (Literal, Query, RefExpr)):
        return resolve_value(v, ctx, default)
    return v


# ── Counter trigger check ──

def _check_counter_trigger(current: int, ct: dict) -> bool:
    """Check if a counter meets its trigger threshold."""
    from backend.vm.cond_path import _cmp
    op = ct.get('op', 'gte')
    expected = ct.get('value', 0)
    return _cmp(op, current, expected)


# ── Target resolution ──

def _resolve_effect_target(
    effect: TraitEffect, ctx: dict, default_sprite=None,
):
    """Resolve the target sprite for a typed effect.

    For mark/weather effects (team/global scoped), returns None.
    For random_bench target, picks a random bench member.
    """
    # Team-scoped effects: no sprite target needed
    if isinstance(effect, (TraitMarkEffect, TraitWeatherEffect)):
        return None

    # Operations that don't need a sprite target from this resolver
    if isinstance(effect, (TeamCounterOp, ScheduleOp)):
        return None

    target_key = getattr(effect, 'target', 'self')

    if target_key == 'random_bench':
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if battle:
            player = battle.get_player(team)
            bench = [
                s for i, s in enumerate(player.team)
                if i != player.active_index and not s.is_fainted
            ]
            if bench:
                return random.choice(bench)
        return default_sprite or ctx.get('self')

    return ctx.get(target_key, default_sprite or ctx.get('self'))


def _is_team_scoped(effect: TraitEffect) -> bool:
    """Check if an effect is team/global scoped (no sprite target needed)."""
    return isinstance(effect, (TraitMarkEffect, TraitWeatherEffect, TeamCounterOp))


# ── Use modifier application ──

def _apply_use_modifiers_typed(
    mods: dict[str, dict], ctx: dict,
) -> list[str]:
    """Apply use_modifiers from a typed trigger.

    The inner dict values may be raw or IRValue instances.
    """
    use = ctx.get('use')
    if not use or not mods:
        return []

    _INIT: dict[str, int | float | bool] = {
        'power_mult': 1.0, 'damage_mult': 1.0,
        'damage_reduction': 0.0, 'multi_hit': 0,
        'ignore_mods': False, 'priority_mod': 0,
    }

    for key, spec in mods.items():
        op = spec.get('op', 'add')
        val_raw = spec.get('value', 0)
        val = _resolve_ir_value(val_raw, ctx)
        target = spec.get('target', 'modifiers')

        if target == 'battleskill':
            bs = ctx.get('skill')
            if bs and key == 'priority_mod':
                bs.priority_mod_temp = getattr(bs, 'priority_mod_temp', 0) + val
        else:
            current = use.modifiers.get(key, _INIT.get(key, 0))
            if op == 'set':
                use.modifiers[key] = val
            elif op == 'mult':
                use.modifiers[key] = current * val
            else:
                use.modifiers[key] = current + val

    return []


# ── Battleskill mutation application ──

def _apply_battleskill_mut_typed(
    muts: tuple[BattleSkillMutOp, ...], ctx: dict,
) -> list[str]:
    """Apply typed BattleSkillMutOp mutations."""
    if not muts:
        return []

    sprite = ctx.get('self')
    if not sprite:
        return []

    for mut in muts:
        filt = mut.filter
        field = mut.field
        op = mut.op
        val = _resolve_ir_value(mut.value, ctx)
        target = mut.target

        if target == 'current':
            bs = ctx.get('skill')
            if bs is not None:
                if field == 'element':
                    bs._element_override = val
                elif op == 'set':
                    setattr(bs, field, val)
                elif op == 'mult':
                    current_val = getattr(bs, field, 0)
                    setattr(bs, field, current_val * val)
                else:
                    current_val = getattr(bs, field, 0)
                    setattr(bs, field, current_val + val)
            continue

        for i, bs in enumerate(sprite.skills):
            if not _match_skill_filter(bs, i, filt):
                continue

            if field == 'element':
                bs._element_override = val
            elif op == 'set':
                setattr(bs, field, val)
            elif op == 'mult':
                current_val = getattr(bs, field, 0)
                setattr(bs, field, current_val * val)
            else:
                current_val = getattr(bs, field, 0)
                setattr(bs, field, current_val + val)

    return []


def _match_skill_filter(bs, idx: int, filt: dict) -> bool:
    """Match a BattleSkill against a filter dict."""
    if not filt:
        return True
    if 'element' in filt and bs.element != filt['element']:
        return False
    if 'slot' in filt and idx not in filt['slot']:
        return False
    if 'slot_in' in filt and idx not in filt['slot_in']:
        return False
    if 'slot_not_in' in filt and idx in filt['slot_not_in']:
        return False
    if 'is_attack' in filt and bs.is_attack != filt['is_attack']:
        return False
    if 'is_defense' in filt and bs.is_defense != filt['is_defense']:
        return False
    if 'is_status' in filt:
        bs_is_status = getattr(bs.base, 'is_status', False)
        if bs_is_status != filt['is_status']:
            return False
    ec = bs.energy_cost
    if 'energy_cost_lt' in filt and ec >= filt['energy_cost_lt']:
        return False
    if 'energy_cost_gt' in filt and ec <= filt['energy_cost_gt']:
        return False
    if 'energy_cost_gte' in filt and ec < filt['energy_cost_gte']:
        return False
    if 'energy_cost_eq' in filt and ec != filt['energy_cost_eq']:
        return False
    return True


# ── Flags application ──

def _apply_flags_typed(flags: dict, ctx: dict) -> list[str]:
    """Set sprite flags or counters from a typed trigger."""
    sprite = ctx.get('self')
    if not sprite:
        return []

    for flag, val in flags.items():
        if flag.startswith('counters.'):
            counter_key = flag[9:]
            sprite.counters[counter_key] = val
        else:
            setattr(sprite, flag, val)
    return []


# ── Team counters application ──

def _apply_team_counters_typed(counters: dict, ctx: dict) -> list[str]:
    """Write team counters from a typed trigger."""
    battle = ctx.get('battle')
    team = ctx.get('team', 'A')
    if not battle:
        return []
    for key, delta in counters.items():
        battle.inc_team_counter(team, key, delta)
    return []


# ── Pending effects handler ──

def _handle_pending_effects_typed(
    pending: tuple[TraitEffect, ...], ctx: dict,
) -> list[str]:
    """Add pending effects to battle.pending_effects for next entry."""
    from backend.sim.sprite import StatusEffect

    battle = ctx.get('battle')
    team = ctx.get('team', 'A')
    sprite = ctx.get('self')
    if not battle or not sprite:
        return []

    battle.pending_effects.setdefault(team, [])
    for eff in pending:
        if isinstance(eff, TraitStatEffect):
            steps = _resolve_ir_value(eff.steps, ctx, 0)
            battle.pending_effects[team].append(StatusEffect(
                name=eff.stat or '',
                category='stat',
                stat_key=eff.stat,
                steps=steps,
                scope=eff.scope,
                source=eff.source or '',
            ))
        elif isinstance(eff, TraitAbnormalEffect):
            stacks = _resolve_ir_value(eff.stacks, ctx, 1)
            battle.pending_effects[team].append(StatusEffect(
                name=eff.name,
                category='abnormal',
                stacks=stacks,
                scope=eff.scope,
                source=eff.source or '',
            ))
        elif isinstance(eff, TraitSpecialEffect):
            battle.pending_effects[team].append(StatusEffect(
                name=eff.name,
                category='state',
                scope=getattr(eff, 'scope', 'battlefield') if hasattr(eff, 'scope') else 'battlefield',
                source=eff.source if hasattr(eff, 'source') else '',
            ))

    return [f'{sprite.name}: 离场效果→下一入场']


# ── Replace / conditional_replace mode helpers ──

def _replace_effects_typed(
    effects: tuple[TraitEffect, ...], ctx: dict,
) -> list[str]:
    """mode=replace: clear old effects from same sources, then apply."""
    sprite = ctx.get('self')
    if not sprite:
        return []

    # Collect sources from all effects
    sources: set[str] = set()
    for e in effects:
        src = _get_effect_source(e)
        if src:
            sources.add(src)

    # Remove old effects with matching source
    for e in list(sprite.effects):
        if getattr(e, 'source', '') in sources:
            sprite.effects.remove(e)

    events: list[str] = []
    for eff in effects:
        target_sprite = _resolve_effect_target(eff, ctx, sprite)
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if target_sprite is not None or _is_team_scoped(eff):
            events += _apply_single_typed_effect(eff, target_sprite, battle, team, ctx)
    return events


def _conditional_replace_effects_typed(
    effects: tuple[TraitEffect, ...], trigger: TraitTrigger, ctx: dict,
) -> list[str]:
    """mode=conditional_replace: check clear_condition, then clear+apply."""
    sprite = ctx.get('self')
    if not sprite:
        return []

    # Check clear condition
    if trigger.clear_condition and not eval_path_cond(trigger.clear_condition, ctx):
        return []

    # Collect sources and clear
    sources: set[str] = set()
    for e in effects:
        src = _get_effect_source(e)
        if src:
            sources.add(src)

    for e in list(sprite.effects):
        if getattr(e, 'source', '') in sources:
            sprite.effects.remove(e)

    events: list[str] = []
    for eff in effects:
        target_sprite = _resolve_effect_target(eff, ctx, sprite)
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if target_sprite is not None or _is_team_scoped(eff):
            events += _apply_single_typed_effect(eff, target_sprite, battle, team, ctx)
    return events


def _get_effect_source(effect: TraitEffect) -> str:
    """Extract source string from a typed effect."""
    if isinstance(effect, (TraitStatEffect, TraitAbnormalEffect)):
        return effect.source or ''
    if isinstance(effect, TraitSpecialEffect):
        return getattr(effect, 'source', '') if hasattr(effect, 'source') else ''
    if isinstance(effect, RemoveEffectOp):
        return effect.source
    return ''


# ── Single typed effect application ──

def _apply_single_typed_effect(
    effect: TraitEffect,
    target_sprite,
    battle,
    team: str,
    ctx: dict,
) -> list[str]:
    """Apply a single typed effect, dispatching to the right handler.

    Standard effects (stat/abnormal/mark/weather/special) are delegated to
    effect_applier.apply_effect(). Operation effects are handled here.
    """
    # ── Standard effects → delegate to effect_applier ──
    if isinstance(effect, (
        TraitStatEffect, TraitAbnormalEffect,
        TraitMarkEffect, TraitWeatherEffect, TraitSpecialEffect,
    )):
        return apply_effect(effect, target_sprite, battle, team, ctx)

    # ── RemoveEffectOp ──
    if isinstance(effect, RemoveEffectOp):
        source = effect.source
        if source and target_sprite:
            for e in list(target_sprite.effects):
                if getattr(e, 'source', '') == source:
                    target_sprite.effects.remove(e)
        return []

    # ── MutateEffectOp ──
    if isinstance(effect, MutateEffectOp):
        return _apply_mutate_op(effect, ctx)

    # ── ScheduleOp ──
    if isinstance(effect, ScheduleOp):
        return _apply_schedule_op(effect, ctx)

    # ── InheritEffectsOp ──
    if isinstance(effect, InheritEffectsOp):
        return _apply_inherit_op(effect, ctx)

    # ── TeamCounterOp ──
    if isinstance(effect, TeamCounterOp):
        return _apply_team_counter_op(effect, ctx)

    # ── TransformOp ──
    if isinstance(effect, TransformOp):
        return _apply_transform_op(effect, ctx)

    # ── TraitInteractionOp ──
    if isinstance(effect, TraitInteractionOp):
        return _apply_trait_interaction_op(effect, ctx)

    # ── LivesOp ──
    if isinstance(effect, LivesOp):
        return _apply_lives_op(effect, ctx)

    return []


# ── Operation effect sub-handlers ──

def _apply_mutate_op(effect: MutateEffectOp, ctx: dict) -> list[str]:
    """Apply MutateEffectOp: modify steps/stacks of existing effects."""
    filter_dict = effect.filter
    target_key = effect.target
    delta_steps = effect.delta_steps
    delta_stacks = effect.delta_stacks

    target_sprite = ctx.get(target_key)
    if not target_sprite:
        return []

    effects_list = getattr(target_sprite, 'effects', [])
    mutated = 0
    to_remove = []
    for e in effects_list:
        ok = True
        for fkey, fval in filter_dict.items():
            if fkey in ('type', 'category'):
                if getattr(e, 'category', '') != fval:
                    ok = False
            elif fkey == 'name':
                if getattr(e, 'name', '') != fval:
                    ok = False
            elif fkey == 'is_stat':
                if bool(getattr(e, 'is_stat', False)) != bool(fval):
                    ok = False
            elif fkey == 'stat_key':
                if getattr(e, 'stat_key', '') != fval:
                    ok = False
            elif fkey == 'steps<0':
                if bool(fval) and getattr(e, 'steps', 0) >= 0:
                    ok = False
            elif fkey == 'steps>0':
                if bool(fval) and getattr(e, 'steps', 0) <= 0:
                    ok = False
            else:
                if getattr(e, fkey, None) != fval:
                    ok = False
        if ok:
            if delta_steps:
                e.steps = getattr(e, 'steps', 0) + delta_steps
            if delta_stacks:
                e.stacks = getattr(e, 'stacks', 0) + delta_stacks
            mutated += 1
            if delta_steps and getattr(e, 'steps', 0) == 0:
                to_remove.append(e)

    for e in to_remove:
        if e in target_sprite.effects:
            target_sprite.effects.remove(e)

    if mutated:
        return [f'{target_sprite.name} {mutated}个效果变更']
    return []


def _apply_schedule_op(effect: ScheduleOp, ctx: dict) -> list[str]:
    """Apply ScheduleOp: register delayed effects."""
    battle = ctx.get('battle')
    if not battle:
        return []
    target_turn = battle.turn + effect.turns
    scheduled = {
        'turn': target_turn,
        'phase': effect.phase,
        'effects': effect.effects,
        'source': ctx.get('self'),
        'ctx_snapshot': {
            'team': ctx.get('team', 'A'),
            'target': getattr(effect, 'target', 'self'),
        },
    }
    battle.scheduled_effects.append(scheduled)
    sprite = ctx.get('self')
    label = getattr(sprite, 'name', '?') if sprite else '?'
    return [f'{label}: 延时效果注册({effect.turns}回合后)']


def _apply_inherit_op(effect: InheritEffectsOp, ctx: dict) -> list[str]:
    """Apply InheritEffectsOp: transfer effects from one sprite to another."""
    battle = ctx.get('battle')
    team = ctx.get('team', 'A')
    if not battle:
        return []

    source_key = effect.source_sprite
    inherit_target_key = effect.target
    scope = effect.scope
    via_pending = effect.via_pending

    source_sprite = ctx.get(source_key)
    target_sprite = ctx.get(inherit_target_key)

    if not source_sprite:
        return []
    if not via_pending and not target_sprite:
        return []

    inherited = [e for e in source_sprite.effects if getattr(e, 'scope', '') == scope]
    if not inherited:
        return []

    if via_pending:
        battle.pending_effects.setdefault(team, [])
        battle.pending_effects[team].extend(inherited)
        return [f'{source_sprite.name}→next({team}) 继承{len(inherited)}个效果']
    else:
        for e in inherited:
            target_sprite.add_effect(e)
        return [f'{source_sprite.name}→{target_sprite.name} 继承{len(inherited)}个效果']


def _apply_team_counter_op(effect: TeamCounterOp, ctx: dict) -> list[str]:
    """Apply TeamCounterOp: increment/decrement a team counter."""
    battle = ctx.get('battle')
    team = ctx.get('team', 'A')
    if not battle:
        return []
    key = effect.key
    delta = effect.delta
    target_team = effect.target_team
    t = ('B' if team == 'A' else 'A') if target_team == 'opp' else team
    battle.inc_team_counter(t, key, delta)
    return []


def _apply_transform_op(effect: TransformOp, ctx: dict) -> list[str]:
    """Apply TransformOp: change species + skills of the sprite."""
    from backend.common.models import SpeciesStats

    sprite = ctx.get('self')
    battle = ctx.get('battle')
    if not sprite or not battle:
        return []

    species_name = effect.species
    if not species_name:
        return []

    new_species = battle.lookup_species(species_name)
    if new_species is None:
        s = sprite.species
        new_species = SpeciesStats(
            name=species_name, form='',
            hp=s.hp, atk=s.atk, sp_atk=s.sp_atk,
            def_=s.def_, sp_def=s.sp_def, speed=s.speed,
            attributes=s.attributes, ability=s.ability,
        )

    skill_names = effect.skills or []
    new_skills = battle.build_skills(skill_names) if skill_names else []

    if effect.reset_hp:
        sprite.current_hp = sprite.max_hp
    if effect.reset_energy:
        sprite.energy = getattr(sprite, 'max_energy', 10)

    events = sprite.transform(new_species, new_skills)
    return events


def _apply_trait_interaction_op(effect: TraitInteractionOp, ctx: dict) -> list[str]:
    """Apply TraitInteractionOp: suppress/remove/copy trait."""
    action = effect.action

    if action == 'suppress':
        target_key = effect.target
        target = ctx.get(target_key)
        if not target:
            return []
        target._trait_suppressed = True
        target._trait_handler = None
        return [f'{target.name} 特性被压制']

    if action == 'remove':
        target_key = effect.target
        target = ctx.get(target_key)
        if not target:
            return []
        target._trait_suppressed = True
        target._trait_handler = None
        new_ability = effect.new_ability
        if new_ability:
            target.species.ability = new_ability
            target._trait_suppressed = False
            target._trait_handler = None
            return [f'{target.name} 特性变为 {new_ability}']
        return [f'{target.name} 特性被移除']

    if action == 'copy':
        source_key = effect.copy_from or 'target'
        source = ctx.get(source_key)
        sprite = ctx.get('self')
        if not source or not sprite:
            return []
        if sprite is source:
            return []
        source_ability = source.species.ability
        if not source_ability:
            return []
        sprite.species.ability = source_ability
        sprite._trait_handler = None
        sprite._trait_suppressed = False
        return [f'{sprite.name} 复制特性 → {source_ability}']

    return []


def _apply_lives_op(effect: LivesOp, ctx: dict) -> list[str]:
    """Apply LivesOp: modify team lives."""
    battle = ctx.get('battle')
    team = ctx.get('team', 'A')
    sprite = ctx.get('self')
    if not battle:
        return []

    delta = effect.delta
    target_team = effect.target_team
    t = ('B' if team == 'A' else 'A') if target_team == 'opp' else team
    p = battle.get_player(t)
    if p is not None:
        if delta < 0 and p.lives <= 0:
            return []
        p.lives += delta
        label = f'奉献{delta}' if delta > 0 else f'魔力{delta}'
        return [f'{sprite.name} {label}'] if sprite else []
    return []


# ═══════════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════════

def process_trigger(trigger: TraitTrigger, ctx: dict) -> list[str]:
    """Execute a single TraitTrigger. Returns list of event description strings.

    ctx must contain: 'self' (sprite), 'battle', 'team'
    Optional: 'target', 'attacker', 'opponent'

    Pipeline: condition → delay → counter → track → use_modifiers →
              battleskill_mut → effects → pending → flags → team_counters
    """
    events: list[str] = []

    # 1. Condition check
    if trigger.condition:
        if not eval_path_cond(trigger.condition, ctx):
            return events

    # 2. Delay handling
    if trigger.delay and trigger.delay > 0:
        battle = ctx.get('battle')
        if battle:
            battle.scheduled_effects.append({
                'turn': battle.turn + trigger.delay,
                'phase': trigger.delay_phase,
                'trigger': trigger,
                'ctx_snapshot': {
                    k: v for k, v in ctx.items()
                    if k in ('team', 'self', 'target', 'attacker', 'battle')
                },
            })
        return events

    # 3. Counter accumulation + threshold gate
    counter_key = trigger.counter
    counter_met = True
    if counter_key:
        sprite = ctx.get('self')
        if sprite:
            cop = trigger.counter_op
            if cop == 'inc':
                sprite.inc_counter(counter_key)
            elif cop == 'dec':
                sprite.inc_counter(counter_key, -1)
            elif cop == 'set':
                cval = _resolve_ir_value(trigger.counter_value, ctx, 0) or 0
                sprite.counters[counter_key] = cval
            ctrigger = trigger.counter_trigger
            if ctrigger:
                cur = sprite.get_counter(counter_key)
                counter_met = _check_counter_trigger(cur, ctrigger)
            if not counter_met:
                return events

    # 4. Track / change detection gate
    track = trigger.track
    if track:
        sprite = ctx.get('self')
        if sprite:
            tkey = track.get('key', '_track')
            expr_raw = track.get('expr', '=0')
            new_val = _resolve_track_expr(expr_raw, ctx) or 0
            prev_val = sprite.get_counter(tkey)
            ctx['track_delta'] = new_val - prev_val if prev_val is not None else 0
            # Gate on change: only proceed if value changed (or first tracking)
            if prev_val is not None and new_val == prev_val:
                return events
            sprite.counters[tkey] = new_val

    # 5. Use modifiers
    if trigger.use_modifiers:
        events += _apply_use_modifiers_typed(trigger.use_modifiers, ctx)

    # 6. Battleskill mutations
    if trigger.battleskill_mut:
        events += _apply_battleskill_mut_typed(trigger.battleskill_mut, ctx)

    # 7. Effects
    effects = trigger.effects
    mode = trigger.effects_mode

    if effects and mode == 'conditional_replace':
        events += _conditional_replace_effects_typed(effects, trigger, ctx)
    elif effects and mode == 'replace':
        events += _replace_effects_typed(effects, ctx)
    elif effects:
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        sprite = ctx.get('self')
        for eff in effects:
            target_sprite = _resolve_effect_target(eff, ctx, sprite)
            if target_sprite is not None or _is_team_scoped(eff):
                events += _apply_single_typed_effect(
                    eff, target_sprite, battle, team, ctx,
                )

    # 8. Pending effects
    if trigger.pending_effects:
        events += _handle_pending_effects_typed(trigger.pending_effects, ctx)

    # 9. Flags
    if trigger.flags:
        _apply_flags_typed(trigger.flags, ctx)

    # 10. Team counters
    if trigger.team_counters:
        _apply_team_counters_typed(trigger.team_counters, ctx)

    # 11. Counter reset (after effects, if threshold was met)
    if counter_key and trigger.counter_reset and counter_met:
        sprite = ctx.get('self')
        if sprite:
            sprite.counters[counter_key] = 0

    return events


# ── Track expression resolver ──

def _resolve_track_expr(expr_raw: str, ctx: dict) -> int | float:
    """Resolve a track expression like '=@self.energy' or '=@player_fainted_count * 3'."""
    import re as _re_module
    if not isinstance(expr_raw, str) or not expr_raw.startswith('='):
        try:
            return float(expr_raw) if expr_raw else 0
        except (ValueError, TypeError):
            return 0

    expr = expr_raw[1:]  # strip '='

    # If arithmetic expression
    if _re_module.search(r'[\+\-\*\/\(]', expr):
        return _eval_track_arithmetic(expr, ctx)

    return _resolve_track_single(expr, ctx)


def _resolve_track_single(expr: str, ctx: dict) -> int | float:
    """Resolve a single @ path expression."""
    if not expr.startswith('@'):
        try:
            return float(expr)
        except ValueError:
            return 0

    path = expr[1:]  # strip '@'
    val = _resolve_track_path(path, ctx)
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return val
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0


def _eval_track_arithmetic(expr: str, ctx: dict) -> int | float:
    """Evaluate a ref arithmetic expression for track."""
    import re as _re_module

    def replace_ref(m):
        ref_expr = m.group(0)
        val = _resolve_track_single(ref_expr, ctx)
        return str(val) if val is not None else '0'

    resolved = _re_module.sub(
        r'@[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*(?:\[[^\]]*\])?)*',
        replace_ref, expr,
    )

    try:
        return eval(resolved, {'__builtins__': {}}, {
            'int': int, 'float': float, 'round': round, 'max': max, 'min': min,
        })
    except Exception:
        return 0


def _resolve_track_path(path: str, ctx: dict):
    """Resolve a track path expression to a value. Handles effects[...], counters[...],
    skills[filter].count, team_counters[...], and plain attribute chains."""
    import re as _re_module

    # Handle effects[...]
    if '.effects[' in path:
        m = _re_module.match(r'(\w+)\.effects\[([^\]]+)\]\.(\w+)', path)
        if m:
            target_key, filter_str, prop = m.group(1), m.group(2), m.group(3)
            sprite = ctx.get(target_key)
            if sprite is None:
                return 0
            filters = []
            for part in filter_str.split(','):
                fm = _re_module.match(r'(\w+)([<>=!]+)(.+)', part.strip())
                if fm:
                    filters.append((fm.group(1), fm.group(2), fm.group(3)))
            effects_list = getattr(sprite, 'effects', [])
            matched = []
            for e in effects_list:
                ok = True
                for fkey, fop, fval in filters:
                    if fkey == 'name':
                        ev = getattr(e, 'name', '')
                    elif fkey == 'type':
                        ev = getattr(e, 'category', '')
                    else:
                        ev = getattr(e, fkey, None)
                    # Simple equality check for track
                    if str(ev) != str(fval):
                        ok = False
                        break
                if ok:
                    matched.append(e)
            if prop == 'exists':
                return 1 if matched else 0
            if prop == 'count':
                return len(matched)
            if prop == 'stacks':
                return sum(getattr(e, 'stacks', 0) for e in matched)
            if prop == 'steps':
                return sum(getattr(e, 'steps', 0) for e in matched)
            return 0

    # Handle counters[...]
    if '.counters[' in path:
        m = _re_module.match(r'(\w+)\.counters\[([^\]]+)\]', path)
        if m:
            target_key, counter_key = m.group(1), m.group(2).strip()
            sprite = ctx.get(target_key)
            if sprite is None:
                return 0
            counters = getattr(sprite, 'counters', {})
            return counters.get(counter_key, 0)
        return 0

    # Handle skills[filter].count
    if '.skills[' in path:
        m = _re_module.match(r'(\w+)\.skills\[([^\]]+)\]\.(\w+)', path)
        if m:
            target_key, filter_str, prop = m.group(1), m.group(2), m.group(3)
            sprite = ctx.get(target_key)
            if sprite is None:
                return 0
            filters = []
            for part in filter_str.split(','):
                fm = _re_module.match(r'(\w+)([<>=!]+)(.+)', part.strip())
                if fm:
                    filters.append((fm.group(1), fm.group(2), fm.group(3)))
            skills = getattr(sprite, 'skills', [])
            matched = []
            for bs in skills:
                ok = True
                for fkey, fop, fval in filters:
                    if fkey == 'element':
                        ev = getattr(bs, 'element', '')
                    elif fkey in ('is_attack', 'is_defense', 'is_status'):
                        ev = getattr(bs.base, fkey, False) if hasattr(bs, 'base') else False
                    elif fkey == 'name':
                        ev = getattr(bs, 'name', '')
                    elif fkey == 'energy_cost':
                        ev = getattr(bs, 'energy_cost', 0)
                    else:
                        ev = getattr(bs, fkey, None)
                    if str(ev) != str(fval):
                        ok = False
                        break
                if ok:
                    matched.append(bs)
            if prop == 'count':
                return len(matched)
            if prop == 'exists':
                return 1 if matched else 0
            return 0
        return 0

    # Handle team_counters[...]
    if 'team_counters[' in path:
        opp_match = _re_module.match(r'opponent\.team_counters\[([^\]]+)\]', path)
        if opp_match:
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            opp_team = 'B' if team == 'A' else 'A'
            key = opp_match.group(1).strip()
            return battle.get_team_counter(opp_team, key) if battle else 0
        ply_match = _re_module.match(r'player\.team_counters\[([^\]]+)\]', path)
        if ply_match:
            battle = ctx.get('battle')
            player_obj = ctx.get('player')
            if battle and player_obj:
                return battle.get_team_counter(player_obj.team_letter, ply_match.group(1).strip())
            return 0
        m = _re_module.match(r'team_counters\[([^\]]+)\]', path)
        if m:
            key = m.group(1).strip()
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            if battle is None:
                return 0
            return battle.get_team_counter(team, key)
        return 0

    # Plain attribute chain
    parts = path.split('.')
    obj = ctx.get(parts[0])
    if obj is None:
        return 0
    for attr in parts[1:]:
        if obj is None:
            return 0
        obj = getattr(obj, attr, None)
    return obj if obj is not None else 0
