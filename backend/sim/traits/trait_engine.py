"""backend/sim/traits/trait_engine.py — 通用特性引擎

DataDrivenTrait: 从 JSON 加载特性定义，实现 TraitHandler 接口。
支持条件求值、动态 ref 表达式、modifier 操作、效果管线。

Schema 见 架构修改.md。

方向覆盖:
  1. team_counter 读写 (pre-entry accumulator)
  2. Aura 光环 (auto entry+leave 配对)
  3. 条件性 replace (conditional_replace)
  4. 效果变异 (mutate_effect)
  5. ref 表达式动态 heal/damage
  6. 印记操作 (dispel/steal/convert mark)
  7. 队伍级计算路径
  8. 技能能耗/底材过滤
  9. Sprite counters 读写
  10. bloodline 检查
  11. 效果继承 (inherit_effects)
  12. 技能计数 (skills[element=X].count)
  13. Hook 注册机制 (register_hook)
"""

from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import TraitHandler
from backend.vm.executor_trait import process_trigger

if TYPE_CHECKING:
    from backend.sim.sprite import Sprite, StatusEffect
    from backend.sim.battle import Battle
    from backend.sim.battleskill import BattleSkill, SkillUse
    from backend.vm.ir_trait import TraitTrigger, CompiledTrait


# ═══════════════════════════════════════════════════════════════════
# Hook 注册机制 (Layer 3b, 方向 13)
# ═══════════════════════════════════════════════════════════════════

_HOOK_REGISTRY: dict[str, list] = {}
"""hook_name → [(callback, trait_name), ...]"""


def register_hook(hook_name: str, callback, trait_name: str = '') -> None:
    """注册一个引擎级 hook 回调。

    支持的 hook 名:
      before_apply_mark, max_energy_override, before_consume_starfall,
      turn_end_bench_check, after_transmission, after_gain_energy,
      after_take_damage, on_energy_short, on_fatal_damage
    """
    _HOOK_REGISTRY.setdefault(hook_name, []).append((callback, trait_name))


def unregister_hook(hook_name: str, trait_name: str = '') -> None:
    """移除指定 trait 注册的所有 hook。"""
    if hook_name not in _HOOK_REGISTRY:
        return
    if not trait_name:
        _HOOK_REGISTRY.pop(hook_name, None)
    else:
        _HOOK_REGISTRY[hook_name] = [
            (cb, tn) for cb, tn in _HOOK_REGISTRY[hook_name]
            if tn != trait_name
        ]


def fire_hook(hook_name: str, *args, **kwargs):
    """触发 hook，合并所有 list 结果。非 list 结果返回第一个非 None。"""
    callbacks = _HOOK_REGISTRY.get(hook_name, [])
    results = []
    for cb, _tn in callbacks:
        result = cb(*args, **kwargs)
        if result is not None:
            results.append(result)
    if not results:
        return None
    if all(isinstance(r, list) for r in results):
        merged: list[str] = []
        for r in results:
            merged.extend(r)
        return merged
    return results[0]


def fire_hook_first(hook_name: str, *args, **kwargs):
    """触发 hook，返回第一个非 None 结果。"""
    callbacks = _HOOK_REGISTRY.get(hook_name, [])
    for cb, _tn in callbacks:
        result = cb(*args, **kwargs)
        if result is not None:
            return result
    return None


# ═══════════════════════════════════════════════════════════════════
# 条件函数注册（方向: 条件函数回调）
# ═══════════════════════════════════════════════════════════════════

_CONDITION_FNS: dict[str, callable] = {}


def register_condition_fn(name: str, fn):
    """注册条件函数，供 condition DSL 的 fn 类型调用。"""
    _CONDITION_FNS[name] = fn


# ═══════════════════════════════════════════════════════════════════
# 条件求值器
# ═══════════════════════════════════════════════════════════════════

class ConditionEvaluator:
    """求值条件 dict。

    支持格式:
    - 路径比较: {"path": "self.energy", "op": "gt", "value": 0}
    - 效果路径: {"path": "self.effects[name=灼烧].exists"}
    - 计数器:   {"path": "self.counters[times_entered]"}
    - 队伍聚合: {"path": "player_fainted_count", "op": "gt", "value": 0}
    - 队伍计数: {"path": "team_counters[element:水]"}
    - 技能计数: {"path": "self.skills[element=毒].count"}
    - 组合:     {"and": [...]}, {"or": [...]}, {"not": {...}}
    """

    _CONTEXT_KEYS: set[str] = {
        'self', 'skill', 'use', 'target', 'attacker',
        'victim', 'killer', 'enemy_old', 'enemy_new',
        'battle', 'team', 'is_faint', 'delta', 'new_energy',
        'damage', 'effect', 'effect_name',
        'player', 'opponent',
        'player_fainted_count', 'opponent_fainted_count',
        'player_unique_elements', 'opponent_unique_elements',
        'player_lives', 'opponent_lives',
        'team_elements',
    }

    @staticmethod
    def evaluate(condition: dict | None, ctx: dict[str, Any]) -> bool:
        if not condition:
            return True

        kind = condition.get('kind', 'path')

        if kind == 'and':
            return all(
                ConditionEvaluator.evaluate(c, ctx)
                for c in condition.get('conditions', [])
            )
        if kind == 'or':
            return any(
                ConditionEvaluator.evaluate(c, ctx)
                for c in condition.get('conditions', [])
            )
        if kind == 'not':
            return not ConditionEvaluator.evaluate(
                condition.get('condition', {}), ctx,
            )
        if kind == 'fn':
            fn_name = condition.get('name', '')
            fn = _CONDITION_FNS.get(fn_name)
            if fn:
                return bool(fn(ctx))
            return True

        path: str = condition.get('path', '')
        op: str = condition.get('op', 'eq')
        expected = condition.get('value')

        if isinstance(expected, str) and expected.startswith('='):
            expected = RefResolver.resolve(expected, ctx)

        actual = ConditionEvaluator._resolve_path(path, ctx)
        return ConditionEvaluator._cmp(op, actual, expected)

    @staticmethod
    def _resolve_path(path: str, ctx: dict[str, Any]):
        """解析路径字符串为实际值。

        支持: effects[...], counters[...], skills[filter].count,
              team_counters[...], 普通属性链, 私有属性.
        """
        # 处理 effects[...] 路径
        if '.effects[' in path:
            return ConditionEvaluator._resolve_effects_path(path, ctx)

        # 处理 counters[...] 路径
        if '.counters[' in path:
            return ConditionEvaluator._resolve_counters_path(path, ctx)

        # 处理 skills[filter].count 路径
        if '.skills[' in path:
            return ConditionEvaluator._resolve_skills_path(path, ctx)

        # 处理 opponent.team_counters[...] / player.team_counters[...] / team_counters[...] 路径
        if 'team_counters[' in path:
            opp_match = re.match(r'opponent\.team_counters\[([^\]]+)\]', path)
            if opp_match:
                battle = ctx.get('battle')
                team = ctx.get('team', 'A')
                opp_team = 'B' if team == 'A' else 'A'
                key = opp_match.group(1).strip()
                return battle.get_team_counter(opp_team, key) if battle else 0
            ply_match = re.match(r'player\.team_counters\[([^\]]+)\]', path)
            if ply_match:
                battle = ctx.get('battle')
                player_obj = ctx.get('player')
                if battle and player_obj:
                    return battle.get_team_counter(player_obj.team_letter, ply_match.group(1).strip())
                return 0
            return ConditionEvaluator._resolve_team_counters_path(path, ctx)

        # 普通字段路径（含预计算键）
        parts = path.split('.')
        obj = ctx.get(parts[0])
        if obj is None:
            return None

        for attr in parts[1:]:
            if obj is None:
                return None
            obj = ConditionEvaluator._resolve_attr(obj, attr)
        return obj

    @staticmethod
    def _resolve_attr(obj, attr: str):
        """解析对象属性，优先 getattr，回退到 dict/私有属性。"""
        val = getattr(obj, attr, None)
        if val is not None:
            return val
        if isinstance(obj, dict):
            return obj.get(attr)
        # 支持私有属性 _charging, _escape_pending 等
        if attr.startswith('_'):
            return getattr(obj, attr, None)
        return None

    @staticmethod
    def _resolve_counters_path(path: str, ctx: dict[str, Any]):
        """解析 sprite counters: <target>.counters[<key>]"""
        m = re.match(r'(\w+)\.counters\[([^\]]+)\]', path)
        if not m:
            return None
        target_key = m.group(1)
        counter_key = m.group(2).strip()
        sprite = ctx.get(target_key)
        if sprite is None:
            return None
        counters = getattr(sprite, 'counters', {})
        return counters.get(counter_key, 0)

    @staticmethod
    def _resolve_skills_path(path: str, ctx: dict[str, Any]):
        """解析技能过滤计数: <target>.skills[<filter>].<property>"""
        m = re.match(r'(\w+)\.skills\[([^\]]+)\]\.(\w+)', path)
        if not m:
            return None
        target_key = m.group(1)
        filter_str = m.group(2)
        prop = m.group(3)
        sprite = ctx.get(target_key)
        if sprite is None:
            return None

        filters: list[tuple[str, str, str]] = []
        for part in filter_str.split(','):
            fm = re.match(r'(\w+)([<>=!]+)(.+)', part.strip())
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
                if not ConditionEvaluator._cmp(fop, ev, fval):
                    ok = False
                    break
            if ok:
                matched.append(bs)

        if prop == 'count':
            return len(matched)
        if prop == 'exists':
            return len(matched) > 0
        return None

    @staticmethod
    def _resolve_team_counters_path(path: str, ctx: dict[str, Any]):
        """解析 team counter: team_counters[<key>]"""
        m = re.match(r'team_counters\[([^\]]+)\]', path)
        if not m:
            return None
        key = m.group(1).strip()
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if battle is None:
            return 0
        return battle.get_team_counter(team, key)

    @staticmethod
    def _resolve_effects_path(path: str, ctx: dict[str, Any]):
        """解析 effects: <target>.effects[<filter>].<property>"""
        m = re.match(r'(\w+)\.effects\[([^\]]+)\]\.(\w+)', path)
        if not m:
            return None

        target_key = m.group(1)
        filter_str = m.group(2)
        prop = m.group(3)

        sprite = ctx.get(target_key)
        if sprite is None:
            return None

        filters: list[tuple[str, str, str]] = []
        for part in filter_str.split(','):
            fm = re.match(r'(\w+)([<>=!]+)(.+)', part.strip())
            if fm:
                filters.append((fm.group(1), fm.group(2), fm.group(3)))

        effects = getattr(sprite, 'effects', [])
        matched = []
        for e in effects:
            ok = True
            for fkey, fop, fval in filters:
                if fkey == 'name':
                    ev = getattr(e, 'name', '')
                elif fkey == 'type':
                    ev = getattr(e, 'category', '')
                else:
                    ev = getattr(e, fkey, None)
                if not ConditionEvaluator._cmp(fop, ev, fval):
                    ok = False
                    break
            if ok:
                matched.append(e)

        if prop == 'exists':
            return len(matched) > 0
        if prop == 'count':
            return len(matched)
        if prop == 'stacks':
            return sum(getattr(e, 'stacks', 0) for e in matched)
        if prop == 'steps':
            return sum(getattr(e, 'steps', 0) for e in matched)
        return None

    @staticmethod
    def _cmp(op: str, actual, expected) -> bool:
        if actual is None:
            return False

        _OP_MAP: dict[str, str] = {
            '=': 'eq', '==': 'eq', '!=': 'neq', '<>': 'neq',
            '>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte',
        }
        op = _OP_MAP.get(op, op)

        if expected is not None and type(actual) is not type(expected):
            if isinstance(expected, bool) and not isinstance(actual, bool):
                actual = bool(actual)
            elif isinstance(expected, (int, float)) and isinstance(actual, str):
                try:
                    actual = float(actual)
                except ValueError:
                    return False
            elif isinstance(expected, str) and isinstance(actual, (int, float)):
                try:
                    expected = float(expected) if isinstance(actual, float) else int(expected)
                except ValueError:
                    return False

        if op == 'eq':
            return actual == expected
        if op == 'neq':
            return actual != expected
        if op == 'gt':
            return actual > expected
        if op == 'gte':
            return actual >= expected
        if op == 'lt':
            return actual < expected
        if op == 'lte':
            return actual <= expected
        if op == 'in':
            return actual in (expected if isinstance(expected, (list, tuple, set)) else [expected])
        if op == 'not_in':
            return actual not in (expected if isinstance(expected, (list, tuple, set)) else [expected])
        if op == 'contains':
            if isinstance(actual, (list, tuple, set)):
                return expected in actual
            return False

        return True


# ═══════════════════════════════════════════════════════════════════
# Ref 表达式解析器
# ═══════════════════════════════════════════════════════════════════

class RefResolver:
    """解析 ref 表达式。

    格式:
      "=@self.energy"              → sprite.energy
      "=@self.effects[name=萌化].stacks * 2"  → 组合求值
      "=@team_counters[element:水] * 3"       → team counter * 3
      "=@player_fainted_count * 30"           → 队伍聚合

    非 ref 字符串（不以 "=" 开头）→ 直接返回原值。
    """

    @staticmethod
    def resolve(value, ctx: dict[str, Any]) -> int | float | str | None:
        if not isinstance(value, str) or not value.startswith('='):
            return value

        expr = value[1:]  # 去掉 '=' 前缀

        # 含运算符或函数调用的表达式 → 算术求值
        if re.search(r'[\+\-\*\/\(]', expr):
            return RefResolver._eval_arithmetic(expr, ctx)

        return RefResolver._resolve_single(expr, ctx)

    @staticmethod
    def _resolve_single(expr: str, ctx: dict[str, Any]):
        if not expr.startswith('@'):
            try:
                return int(expr)
            except ValueError:
                try:
                    return float(expr)
                except ValueError:
                    return expr

        path = expr[1:]  # self.energy 或 self.effects[name=X].stacks
        return ConditionEvaluator._resolve_path(path, ctx)

    @staticmethod
    def _eval_arithmetic(expr: str, ctx: dict[str, Any]) -> int | float:
        def replace_ref(m):
            ref_expr = m.group(0)
            val = RefResolver._resolve_single(ref_expr, ctx)
            return str(val) if val is not None else '0'

        resolved = re.sub(
            r'@[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*(?:\[[^\]]*\])?)*',
            replace_ref, expr,
        )

        try:
            return eval(resolved, {'__builtins__': {}}, {'int': int, 'float': float, 'round': round, 'max': max, 'min': min})
        except Exception:
            return 0


# ═══════════════════════════════════════════════════════════════════
# DataDrivenTrait — 通用特性处理器
# ═══════════════════════════════════════════════════════════════════

class DataDrivenTrait(TraitHandler):
    """从 JSON triggers 列表构造的通用特性处理器。

    实现所有 17 个 TraitHandler hook，委托给 _fire() 执行匹配的 trigger。
    支持 aura 自动配对、conditional_replace 模式。

    on_energy_short / on_fatal_damage 委托给 hook 系统（Layer 3b）。
    """

    def __init__(self, name: str, triggers: list[dict], trait_id: int = 0,
                 compiled: "CompiledTrait | None" = None):
        self.name = name
        self.trait_id = trait_id
        self._triggers: dict[str, list[dict]] = {}
        self._process_triggers(triggers)
        # Typed triggers from CompiledTrait (Task 8/9: typed execution path)
        self._compiled_triggers: dict[str, list["TraitTrigger"]] = {}
        if compiled is not None:
            self._process_compiled_triggers(compiled)

    def _process_triggers(self, triggers: list[dict]) -> None:
        """处理 triggers 列表，展开 aura 定义（方向 2）。

        aura: 入场对目标施加效果，离场按 source 清除。
        """
        for t in triggers:
            aura = t.get('aura')
            if aura:
                self._expand_aura(aura, t)
                continue

            hook = t.get('on', '')
            if hook:
                self._triggers.setdefault(hook, []).append(t)

    def _process_compiled_triggers(self, compiled: "CompiledTrait") -> None:
        """组织 CompiledTrait 中的类型化 TraitTrigger 按 hook 分组。

        CompiledTrait 的 triggers 已经过 AuraExpandPass 展开，
        每个 TraitTrigger.on 即为 hook 名。
        """
        for t in compiled.triggers:
            self._compiled_triggers.setdefault(t.on, []).append(t)

    def _expand_aura(self, aura: dict, parent: dict) -> None:
        """aura → entry + leave 触发器对。

        aura 格式:
          {"aura": {"name": "冰封", "effects": [...],
                    "target": "opponent_active"}}
        """
        effects = aura.get('effects', [])
        aura_name = aura.get('name', self.name)
        aura_target = aura.get('target', 'opponent_active')
        for e in effects:
            e['source'] = e.get('source', aura_name)
            e['target'] = e.get('target', aura_target)

        entry_trigger = {
            'on': 'entry',
            'effects': effects,
            'effects_mode': parent.get('effects_mode', 'accumulate'),
        }
        if parent.get('condition'):
            entry_trigger['condition'] = parent['condition']
        self._triggers.setdefault('entry', []).append(entry_trigger)

        leave_effects = []
        for e in effects:
            leave_effects.append({
                'kind': 'remove_effect',
                'source': e.get('source', aura_name),
                'target': e.get('target', aura_target),
            })
        leave_trigger = {'on': 'leave', 'effects': leave_effects}
        self._triggers.setdefault('leave', []).append(leave_trigger)

    # ── 入场 / 离场 ──

    def on_entry(self, sprite, battle, team):
        player = battle.get_player(team) if battle else None
        opponent = battle.get_opponent(team) if battle else None
        team_elements: list[str] = []
        if player:
            for s in player.team:
                if not s.is_fainted:
                    team_elements.extend(s.species.elements)
        self_total_energy_cost = sum(
            bs.energy_cost for bs in sprite.skills
        ) if sprite else 0
        # 队伍中其他虫系精灵数量
        player_bug_count = sum(
            1 for s in player.team
            if not s.is_fainted and s != sprite and '虫' in s.species.elements
        ) if player else 0
        # 其他队友萌化总层数
        player_moe_stacks = sum(
            s.get_stacks('萌化') for s in player.team
            if not s.is_fainted and s != sprite
        ) if player else 0
        return self._fire('entry', {
            'self': sprite, 'battle': battle, 'team': team,
            'player': player, 'opponent': opponent,
            'team_elements': team_elements,
            'self_total_energy_cost': self_total_energy_cost,
            'player_bug_count': player_bug_count,
            'player_moe_stacks': player_moe_stacks,
        })

    def on_leave(self, sprite, battle, team, is_faint=False):
        player = battle.get_player(team) if battle else None
        opponent = battle.get_opponent(team) if battle else None
        return self._fire('leave', {
            'self': sprite, 'battle': battle, 'team': team,
            'is_faint': is_faint,
            'player': player, 'opponent': opponent,
        })

    # ── 回合边界 ──

    def on_turn_start(self, sprite, battle, team):
        target = battle.get_opponent(team).active if battle else None
        return self._fire('turn_start', {
            'self': sprite, 'target': target, 'battle': battle, 'team': team,
        })

    def on_turn_end(self, sprite, battle, team):
        target = battle.get_opponent(team).active if battle else None
        return self._fire('turn_end', {
            'self': sprite, 'target': target, 'battle': battle, 'team': team,
        })

    # ── 技能管线 ──

    def on_modifier(self, user, use, battle, team):
        target = battle.get_opponent(team).active if battle else None
        _EXTRA_KINDS = {'stat', 'abnormal', 'mark', 'weather'}
        has_extra = any(
            getattr(e, 'kind', '') in _EXTRA_KINDS
            for e in use.battle_skill.effects
        )
        opp_team = 'B' if team == 'A' else 'A'
        _, neg_marks = battle.globals.get_marks(opp_team)
        starfall = next((m for m in neg_marks if m.name == '星陨印记'), None)
        opp_starfall_stacks = starfall.stacks if starfall else 0
        enemy_total_energy_cost = sum(
            bs.energy_cost for bs in target.skills
        ) if target else 0
        enemy_elements = set(
            bs.element for bs in target.skills if bs.element
        ) if target else set()
        enemy_unique_elements = len(enemy_elements)
        self_total_energy_cost = sum(
            bs.energy_cost for bs in user.skills
        ) if user else 0
        return self._fire('modifier', {
            'self': user, 'skill': use.battle_skill, 'use': use,
            'target': target, 'battle': battle, 'team': team,
            'skill_has_extra': has_extra,
            'opp_starfall_stacks': opp_starfall_stacks,
            'enemy_total_energy_cost': enemy_total_energy_cost,
            'self_total_energy_cost': self_total_energy_cost,
            'enemy_unique_elements': enemy_unique_elements,
            'first_action': getattr(user, 'first_action', False),
            'target_bloodline': getattr(target, 'bloodline', '') if target else '',
        })

    def on_damage(self, user, target, use, battle, team):
        type_mult = use.modifiers.get('type_mult', 1.0) if use else 1.0
        return self._fire('damage', {
            'self': user, 'skill': use.battle_skill, 'use': use,
            'target': target, 'battle': battle, 'team': team,
            'type_mult': type_mult,
        })

    def on_defend(self, target, attacker, use, battle, team):
        defender_skill_elements = list({bs.element for bs in target.skills if bs.element})
        return self._fire('defend', {
            'self': target, 'skill': use.battle_skill, 'use': use,
            'target': attacker, 'attacker': attacker,
            'battle': battle, 'team': team,
            'defender_skill_elements': defender_skill_elements,
        })

    def on_skill_use(self, user, skill, battle, team):
        target = battle.get_opponent(team).active if battle else None
        self_total_energy_cost = sum(
            bs.energy_cost for bs in user.skills
        ) if user else 0
        # 敌方中毒印记层数
        opp_team = 'B' if team == 'A' else 'A'
        opp_poison_mark = battle.globals.get_mark_by_name(opp_team, '中毒印记') if battle else None
        opp_poison_mark_stacks = opp_poison_mark.stacks if opp_poison_mark else 0
        return self._fire('skill_use', {
            'self': user, 'skill': skill,
            'target': target, 'battle': battle, 'team': team,
            'self_total_energy_cost': self_total_energy_cost,
            'opp_poison_mark_stacks': opp_poison_mark_stacks,
        })

    def on_take_damage(self, target, attacker, damage, battle, team):
        return self._fire('take_damage', {
            'self': target, 'attacker': attacker, 'damage': damage,
            'battle': battle, 'team': team,
        })

    def on_ko_enemy(self, user, victim, battle, team):
        return self._fire('ko_enemy', {
            'self': user, 'victim': victim,
            'battle': battle, 'team': team,
        })

    def on_counter_success(self, user, countered_skill, battle, team):
        return self._fire('counter_success', {
            'self': user, 'skill': countered_skill,
            'battle': battle, 'team': team,
        })

    def on_faint(self, sprite, killer, battle, team):
        return self._fire('faint', {
            'self': sprite, 'killer': killer,
            'battle': battle, 'team': team,
        })

    # ── 能量 / 效果事件 ──

    def on_energy_change(self, sprite, delta, new_energy, battle, team):
        return self._fire('energy_change', {
            'self': sprite, 'delta': delta, 'new_energy': new_energy,
            'battle': battle, 'team': team,
        })

    def on_gain_effect(self, sprite, effect, battle, team):
        return self._fire('gain_effect', {
            'self': sprite, 'effect': effect,
            'battle': battle, 'team': team,
        })

    def on_inflict(self, user, target, effect_name, battle, team):
        return self._fire('inflict', {
            'self': user, 'target': target, 'effect_name': effect_name,
            'battle': battle, 'team': team,
        })

    def on_enemy_leave(self, sprite, enemy_old, enemy_new, battle, team):
        return self._fire('enemy_leave', {
            'self': sprite, 'enemy_old': enemy_old, 'enemy_new': enemy_new,
            'battle': battle, 'team': team,
        })

    def on_abnormal_tick(self, sprite, effect_name, damage, battle, team):
        return self._fire('abnormal_tick', {
            'self': sprite, 'effect_name': effect_name, 'damage': damage,
            'battle': battle, 'team': team,
        })

    # ── 非 list 返回的 hooks（Layer 3b hook 系统）──

    def on_energy_short(self, sprite, cost, battle, team):
        result = fire_hook_first('on_energy_short', sprite, cost, battle, team)
        return result if result is not None else 0

    def on_fatal_damage(self, sprite, damage, battle, team):
        result = fire_hook_first('on_fatal_damage', sprite, damage, battle, team)
        return result if result is not None else False

    def on_before_take_damage(self, target, attacker, damage, element, battle, team):
        """受到伤害前拦截。先查 hook，再查 JSON trigger。
        返回 None=不修改, 0=免疫, <0=吸收, >0=修正伤害。"""
        result = fire_hook_first('before_take_damage', target, attacker, damage, element, battle, team)
        if result is not None:
            return result
        # JSON trigger: before_take_damage 返回第一个非 None 效果中的 value
        ctx = {
            'self': target, 'attacker': attacker, 'damage': damage,
            'skill_element': element, 'battle': battle, 'team': team,
        }
        triggers = self._triggers.get('before_take_damage', [])
        for trigger in triggers:
            if not ConditionEvaluator.evaluate(trigger.get('condition'), ctx):
                continue
            effects = trigger.get('effects', [])
            for eff in effects:
                if eff.get('kind') == 'special' and eff.get('name') == 'modify_damage':
                    val = eff.get('value', None)
                    if val is not None:
                        if isinstance(val, str):
                            val = RefResolver.resolve(val, ctx)
                        return int(val) if val is not None else None
        return None

    def on_before_action(self, sprite, action, battle, team):
        """行动选择后修改/否决。返回 None=不修改, 否则返回替换的 action。"""
        result = fire_hook_first('before_action', sprite, action, battle, team)
        if result is not None:
            return result
        # JSON trigger: 检查条件，若匹配则应用 action_modifier
        ctx = {'self': sprite, 'battle': battle, 'team': team, 'action': action}
        triggers = self._triggers.get('before_action', [])
        for trigger in triggers:
            if not ConditionEvaluator.evaluate(trigger.get('condition'), ctx):
                continue
            effects = trigger.get('effects', [])
            for eff in effects:
                if eff.get('kind') == 'special':
                    name = eff.get('name', '')
                    if name == 'force_action':
                        return DataDrivenTrait._apply_action_force(eff, action, ctx)
                    elif name == 'forbid_action':
                        if DataDrivenTrait._match_action(eff, action):
                            return DataDrivenTrait._apply_action_force(eff, action, ctx)
        return None

    # ═══════════════════════════════════════════════════════════════
    # 核心分派
    # ═══════════════════════════════════════════════════════════════

    def _fire(self, hook: str, ctx: dict) -> list[str]:
        """执行匹配 hook 的所有 trigger。

        管线: precompute team values → condition → use_modifiers →
              battleskill_mut → effects → conditional_replace →
              replace → accumulate → pending_effects →
              flags → team_counters

        优先执行 typed CompiledTrait triggers (通过 process_trigger()),
        回退到原始 dict triggers 路径。
        """
        events: list[str] = []

        self._precompute_team_values(ctx)

        # ── Typed path: CompiledTrait triggers via executor_trait ──
        for trigger in self._compiled_triggers.get(hook, []):
            events += process_trigger(trigger, ctx)

        # ── Dict path: legacy JSON triggers ──
        events += self._fire_dict_triggers(hook, self._triggers.get(hook, []), ctx)

        return events

    def _fire_dict_triggers(self, hook: str, triggers: list[dict], ctx: dict) -> list[str]:
        """执行原始 dict 格式的 triggers（向后兼容路径）。"""
        events: list[str] = []

        for trigger in triggers:
            condition = trigger.get('condition')
            if not ConditionEvaluator.evaluate(condition, ctx):
                continue

            # ── 延时调度（方向: delayed effects）──
            delay = trigger.get('delay', 0)
            if delay and delay > 0:
                battle = ctx.get('battle')
                if battle:
                    battle.scheduled_effects.append({
                        'turn': battle.turn + delay,
                        'phase': trigger.get('delay_phase', 'start'),
                        'trait_name': self.name,
                        'hook': hook,
                        'trigger': trigger,
                        'ctx_snapshot': {
                            k: v for k, v in ctx.items()
                            if k in ('team', 'self', 'target', 'attacker', 'battle')
                        },
                    })
                continue

            # ── Counter accumulation + threshold gate (方向: 跨触发器累积计数) ──
            counter_key = trigger.get('counter')
            counter_met = True
            if counter_key:
                sprite = ctx.get('self')
                if sprite:
                    cop = trigger.get('counter_op', 'inc')
                    if cop == 'inc':
                        sprite.inc_counter(counter_key)
                    elif cop == 'dec':
                        sprite.inc_counter(counter_key, -1)
                    elif cop == 'set':
                        cval = RefResolver.resolve(trigger.get('counter_value', 0), ctx) or 0
                        sprite.counters[counter_key] = cval
                    ctrigger = trigger.get('counter_trigger')
                    if ctrigger:
                        cur = sprite.get_counter(counter_key)
                        counter_met = ConditionEvaluator._cmp(
                            ctrigger.get('op', 'gte'), cur, ctrigger.get('value', 0))
                    if not counter_met:
                        continue

            # ── Track / change detection gate (方向: 变化检测) ──
            track = trigger.get('track')
            if track:
                sprite = ctx.get('self')
                if sprite:
                    new_val = RefResolver.resolve(track.get('expr', '=0'), ctx) or 0
                    tkey = track.get('key', '_track')
                    prev_val = sprite.get_counter(tkey)
                    ctx['track_delta'] = new_val - prev_val
                    if trigger.get('trigger_on_change') and new_val == prev_val:
                        continue
                    sprite.counters[tkey] = new_val

            events += self._apply_use_modifiers(
                trigger.get('use_modifiers', {}), ctx,
            )

            events += self._apply_battleskill_mut(
                trigger.get('battleskill_mut', []), ctx,
            )

            effects = trigger.get('effects', [])
            mode = trigger.get('effects_mode', 'accumulate')

            if effects and mode == 'conditional_replace':
                events += self._conditional_replace_effects(effects, trigger, ctx)
            elif effects and mode == 'replace':
                events += self._replace_effects(effects, ctx)
            else:
                for eff_dict in effects:
                    events += self._apply_effect(eff_dict, ctx)

            pending = trigger.get('pending_effects', [])
            if pending:
                events += self._handle_pending_effects(pending, ctx)

            flags = trigger.get('flags', {})
            if flags:
                events += self._apply_flags(flags, ctx)

            team_counters = trigger.get('team_counters', {})
            if team_counters:
                events += self._write_team_counters(team_counters, ctx)

            # ── Counter reset (after effects, if threshold was met) ──
            if counter_key and trigger.get('counter_reset') and counter_met:
                sprite = ctx.get('self')
                if sprite:
                    sprite.counters[counter_key] = 0

        return events

    @staticmethod
    def _precompute_team_values(ctx: dict) -> None:
        """预计算队伍级聚合值（方向 7）。

        新增 ctx: player_fainted_count, opponent_fainted_count,
                  player_unique_elements, opponent_unique_elements,
                  player_lives, opponent_lives
        """
        for key, team_obj in [('player', ctx.get('player')), ('opponent', ctx.get('opponent'))]:
            if team_obj is None:
                continue
            ctx[f'{key}_fainted_count'] = sum(
                1 for s in team_obj.team if s.is_fainted
            )
            ctx[f'{key}_unique_elements'] = len(set(
                e for s in team_obj.team if not s.is_fainted
                for e in s.species.elements
            ))
            ctx[f'{key}_lives'] = getattr(team_obj, 'lives', 0)

    @staticmethod
    def _write_team_counters(counters: dict, ctx: dict) -> list[str]:
        """写入队伍计数器（方向 1）。"""
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if not battle:
            return []
        for key, delta in counters.items():
            battle.inc_team_counter(team, key, delta)
        return []

    # ── Modifier 操作 ──

    @staticmethod
    def _apply_use_modifiers(mods: dict, ctx: dict) -> list[str]:
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
            val = RefResolver.resolve(spec.get('value', 0), ctx)
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

    @staticmethod
    def _apply_battleskill_mut(mutations: list[dict], ctx: dict) -> list[str]:
        if not mutations:
            return []

        sprite = ctx.get('self')
        if not sprite:
            return []

        for mut in mutations:
            filt = mut.get('filter', {})
            field = mut.get('field', '')
            op = mut.get('op', 'add')
            val_raw = mut.get('value', 0)
            val = RefResolver.resolve(val_raw, ctx) if isinstance(val_raw, str) else val_raw
            target = mut.get('target', 'all')

            if target == 'current':
                bs = ctx.get('skill')
                if bs is not None:
                    if field == 'element':
                        bs._element_override = val
                    elif op == 'set':
                        setattr(bs, field, val)
                    elif op == 'mult':
                        current = getattr(bs, field, 0)
                        setattr(bs, field, current * val)
                    else:
                        current = getattr(bs, field, 0)
                        setattr(bs, field, current + val)
                continue

            for i, bs in enumerate(sprite.skills):
                if not DataDrivenTrait._match_filter(bs, i, filt):
                    continue

                # 元素转换: 设置 _element_override (element 是只读 property)
                if field == 'element':
                    bs._element_override = val
                elif op == 'set':
                    setattr(bs, field, val)
                elif op == 'mult':
                    current = getattr(bs, field, 0)
                    setattr(bs, field, current * val)
                else:
                    current = getattr(bs, field, 0)
                    setattr(bs, field, current + val)

        return []

    @staticmethod
    def _match_filter(bs, idx: int, filt: dict) -> bool:
        """技能过滤器（方向 8: 新增 energy_cost 范围 + base 属性）。"""
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

    # ── 目标解析（方向: 随机目标选择）──

    @staticmethod
    def _resolve_target(eff_dict: dict, ctx: dict, default_sprite):
        """解析效果目标。支持 random_bench、target_filter。"""
        import random

        target_key = eff_dict.get('target', 'self')

        if target_key == 'random_bench':
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            if battle:
                player = battle.get_player(team)
                bench = [s for i, s in enumerate(player.team)
                        if i != player.active_index and not s.is_fainted]
                tf = eff_dict.get('target_filter', {})
                if tf:
                    bench = [s for s in bench
                            if DataDrivenTrait._match_target_filter(s, tf, ctx)]
                if bench:
                    return random.choice(bench)
            return default_sprite

        return ctx.get(target_key, default_sprite)

    @staticmethod
    def _match_target_filter(sprite, filt: dict, ctx: dict) -> bool:
        """检查精灵是否匹配 target_filter。支持 not (排除)、is_fainted 等。"""
        if 'not' in filt:
            exclude = filt['not']
            if exclude == 'self':
                if sprite is ctx.get('self'):
                    return False
        if 'is_fainted' in filt:
            if sprite.is_fainted != bool(filt['is_fainted']):
                return False
        return True

    # ── 效果应用 ──

    @staticmethod
    def _apply_effect(eff_dict: dict, ctx: dict) -> list[str]:
        """应用单个效果。

        kind: stat, abnormal, mark, weather, special,
              mutate_effect, remove_effect
        """
        from backend.sim.sprite import StatusEffect

        kind = eff_dict.get('kind', 'stat')
        sprite = ctx.get('self')
        if not sprite:
            return []

        target_sprite = DataDrivenTrait._resolve_target(eff_dict, ctx, sprite)

        # remove_effect: aura 离场清除
        if kind == 'remove_effect':
            source = eff_dict.get('source', '')
            if source and target_sprite:
                for e in list(target_sprite.effects):
                    if getattr(e, 'source', '') == source:
                        target_sprite.effects.remove(e)
            return []

        if kind == 'stat':
            steps_raw = eff_dict.get('steps', 0)
            steps = RefResolver.resolve(steps_raw, ctx) if isinstance(steps_raw, str) else steps_raw
            if not isinstance(steps, (int, float)):
                steps = 0
            stat_key = eff_dict.get('stat', '')
            scope = eff_dict.get('scope', 'battlefield')
            source = eff_dict.get('source', '')

            label_map = {
                'atk': '物攻', 'sp_atk': '魔攻', 'def': '物防', 'sp_def': '魔防',
                'speed': '速度', 'power': '威力', 'priority': '先手',
                'energy_cost': '能耗', 'combo': '连击', 'life_drain': '吸血',
            }
            label = label_map.get(stat_key, stat_key)
            sign = '+' if steps > 0 else ''
            unit = _STEP_UNIT.get(stat_key, 10)
            if stat_key in ('priority', 'energy_cost', 'combo'):
                display = f'{label}{sign}{steps}'
            elif stat_key == 'power':
                display = f'{label}{sign}{steps * unit}'
            else:
                display = f'{label}{sign}{steps * unit}%'

            se = StatusEffect(
                name=display, category='stat', stat_key=stat_key,
                steps=steps, scope=scope, source=source or None,
            )
            target_sprite.add_effect(se)
            return [f'{target_sprite.name} {display}']

        if kind == 'abnormal':
            name = eff_dict.get('name', '')
            stacks_raw = eff_dict.get('stacks', 1)
            stacks = RefResolver.resolve(stacks_raw, ctx) if isinstance(stacks_raw, str) else stacks_raw
            # 萌化：走形态退化专用路径
            if name == '萌化':
                battle = ctx.get('battle')
                if battle and target_sprite:
                    old_name = target_sprite.name
                    events = target_sprite.apply_moe(int(stacks), battle)
                    return events
            scope = eff_dict.get('scope', 'battlefield')
            source = eff_dict.get('source', '')
            se = StatusEffect(
                name=name, category='abnormal', stacks=stacks,
                scope=scope, source=source or None,
            )
            target_sprite.add_effect(se)
            total = target_sprite.get_stacks(name)
            return [f'{target_sprite.name} {name}+{stacks}(共{total}层)']

        if kind == 'mark':
            name = eff_dict.get('name', '')
            stacks = eff_dict.get('stacks', 1)
            team = ctx.get('team', 'A')
            mark_target = eff_dict.get('mark_target', 'opp_team')
            battle = ctx.get('battle')
            if battle:
                opp_team = 'B' if team == 'A' else 'A'
                actual = team if mark_target == 'own_team' else opp_team
                category = battle.globals.classify_mark(name)
                battle.globals.apply_mark(actual, name, category, stacks)
                return [f'{actual}方 {name}+{stacks}']

        if kind == 'weather':
            weather = eff_dict.get('weather', '')
            turns = eff_dict.get('turns', 8)
            battle = ctx.get('battle')
            if battle:
                battle.globals.set_weather(weather, turns)
                return [f'天气→{weather}({turns}回合)']

        if kind == 'special':
            return DataDrivenTrait._apply_special_effect(
                eff_dict, target_sprite, ctx,
            )

        if kind == 'mutate_effect':
            return DataDrivenTrait._apply_mutate_effect(eff_dict, ctx)

        return []

    @staticmethod
    def _replace_effects(effects: list[dict], ctx: dict) -> list[str]:
        """mode=replace: 先清除同 source 旧效果，再添加。"""
        sprite = ctx.get('self')
        if not sprite:
            return []

        sources = {e.get('source', '') for e in effects if e.get('source')}
        for e in list(sprite.effects):
            if getattr(e, 'source', '') in sources:
                sprite.effects.remove(e)

        events: list[str] = []
        for eff_dict in effects:
            events += DataDrivenTrait._apply_effect(eff_dict, ctx)
        return events

    @staticmethod
    def _conditional_replace_effects(effects: list[dict], trigger: dict, ctx: dict) -> list[str]:
        """mode=conditional_replace（方向 3）: 检查条件 → 满足才清除+添加。"""
        sprite = ctx.get('self')
        if not sprite:
            return []

        clear_cond = trigger.get('clear_condition')
        if clear_cond and not ConditionEvaluator.evaluate(clear_cond, ctx):
            return []

        sources = {e.get('source', '') for e in effects if e.get('source')}
        for e in list(sprite.effects):
            if getattr(e, 'source', '') in sources:
                sprite.effects.remove(e)

        events: list[str] = []
        for eff_dict in effects:
            events += DataDrivenTrait._apply_effect(eff_dict, ctx)
        return events

    @staticmethod
    def _apply_mutate_effect(eff_dict: dict, ctx: dict) -> list[str]:
        """效果变异（方向 4）: 修改目标已有效果的 steps/stacks。"""
        filter_dict = eff_dict.get('filter', {})
        target_key = eff_dict.get('target', 'target')
        delta_steps = eff_dict.get('delta_steps', 0)
        delta_stacks = eff_dict.get('delta_stacks', 0)

        target_sprite = ctx.get(target_key)
        if not target_sprite:
            return []

        effects = getattr(target_sprite, 'effects', [])
        mutated = 0
        to_remove = []
        for e in effects:
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

    @staticmethod
    def _apply_special_effect(eff_dict: dict, sprite, ctx: dict) -> list[str]:
        """特殊效果（方向 5: ref 表达式动态值）。

        支持: heal, direct_heal, gain_energy, energy_set,
              steal_energy, steal_energy_all, lose_energy,
              lives_delta, lives_add, take_damage,
              dispel_mark, steal_mark, convert_mark,
              inherit_effects, team_counter_add
        """
        name = eff_dict.get('name', '')
        value = eff_dict.get('value', 0)
        amount = eff_dict.get('amount', 0)

        if isinstance(value, str):
            value = RefResolver.resolve(value, ctx) or 0
        if isinstance(amount, str):
            amount = RefResolver.resolve(amount, ctx) or 0

        if name == 'heal':
            pct = value or (amount / sprite.max_hp if amount and sprite.max_hp else 0)
            amt = round(sprite.max_hp * pct) if pct else amount
            healed = sprite.heal(int(amt)) if amt else 0
            return [f'{sprite.name} 回复+{healed}HP'] if healed else []

        if name == 'direct_heal':
            healed = sprite.heal(int(amount or 0))
            return [f'{sprite.name} 回复+{healed}HP'] if healed else []

        if name == 'gain_energy':
            gained = sprite.gain_energy(int(amount or 0))
            return [f'{sprite.name} 回复+{gained}E'] if gained else []

        if name == 'energy_set':
            sprite.energy = int(amount)
            return [f'{sprite.name} 能量={amount}']

        if name == 'steal_energy':
            target = ctx.get('target')
            if target:
                amt = int(amount or 1)
                stolen = target.lose_energy(amt)
                sprite.gain_energy(stolen)
                return [f'{sprite.name} 偷取{stolen}E']
            return []

        if name == 'steal_energy_all':
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            if battle:
                opp = battle.get_opponent(team)
                events = []
                for s in opp.team:
                    if not s.is_fainted:
                        lost = s.lose_energy(int(amount or 1))
                        if lost:
                            events.append(f'{sprite.name} 偷取{s.name} {lost}E')
                return events
            return []

        if name == 'lose_energy':
            lost = sprite.lose_energy(int(amount or 1))
            return [f'{sprite.name} -{lost}E'] if lost else []

        if name == 'lives_delta':
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            if battle:
                target_team = eff_dict.get('target_team', 'own')
                t = ('B' if team == 'A' else 'A') if target_team == 'opp' else team
                p = battle.get_player(t)
                delta = int(amount or 0)
                if delta < 0 and p.lives <= 0:
                    return []
                p.lives += delta
                label = f'奉献{delta}' if delta > 0 else f'魔力{delta}'
                return [f'{sprite.name} {label}']

        if name == 'lives_add':
            battle = ctx.get('battle')
            team = ctx.get('team', 'A')
            if battle:
                player = battle.get_player(team)
                player.lives += int(amount or 1)
                return [f'{sprite.name} 奉献+{amount or 1}']

        if name == 'take_damage':
            dmg = value or amount
            target_key = eff_dict.get('damage_target', 'target')
            target = ctx.get(target_key)
            if target and dmg:
                actual = target.take_damage(int(dmg))
                return [f'{target.name} -{actual}HP']

        # ── 形态变换（方向: 复杂状态转换）──

        if name == 'transform':
            return DataDrivenTrait._apply_transform(eff_dict, sprite, ctx)

        # ── 特性交互（方向: 特性禁用/复制/移除）──

        if name == 'suppress_trait':
            return DataDrivenTrait._apply_suppress_trait(eff_dict, ctx)
        if name == 'remove_trait':
            return DataDrivenTrait._apply_remove_trait(eff_dict, ctx)
        if name == 'copy_trait':
            return DataDrivenTrait._apply_copy_trait(eff_dict, ctx)

        # ── 延时效果（方向: scheduled effects）──

        if name == 'schedule':
            return DataDrivenTrait._apply_schedule(eff_dict, ctx)

        # ── 印记操作（方向 6）──

        if name == 'dispel_mark':
            return DataDrivenTrait._apply_dispel_mark(eff_dict, ctx)

        if name == 'steal_mark':
            return DataDrivenTrait._apply_steal_mark(eff_dict, ctx)

        if name == 'convert_mark':
            return DataDrivenTrait._apply_convert_mark(eff_dict, ctx)

        # ── 效果继承（方向 11）──

        if name == 'inherit_effects':
            return DataDrivenTrait._apply_inherit_effects(eff_dict, ctx)

        # ── 队伍计数器写入（方向 1）──

        if name == 'team_counter_add':
            return DataDrivenTrait._apply_team_counter_add(eff_dict, ctx)

        return []

    # ── 印记操作实现（方向 6）──

    @staticmethod
    def _apply_dispel_mark(eff_dict: dict, ctx: dict) -> list[str]:
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if not battle:
            return []
        mark_team = eff_dict.get('mark_target_team', 'opp')
        t = ('B' if team == 'A' else 'A') if mark_team == 'opp' else team
        count = eff_dict.get('count', 1)
        filt = eff_dict.get('filter', {})
        pos, neg = battle.globals.get_marks(t)
        all_marks = pos + neg
        for m in all_marks:
            if 'name' in filt and m.name != filt['name']:
                continue
            if m.stacks > 0:
                removed = min(m.stacks, count)
                m.stacks -= removed
                label = getattr(ctx.get('self'), 'name', '?')
                return [f'{label} 驱散{t}方{m.name}×{removed}']
        return []

    @staticmethod
    def _apply_steal_mark(eff_dict: dict, ctx: dict) -> list[str]:
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if not battle:
            return []
        opp_team = 'B' if team == 'A' else 'A'
        count = eff_dict.get('count', 1)
        pos, neg = battle.globals.get_marks(opp_team)
        all_marks = pos + neg
        for m in all_marks:
            if m.stacks > 0:
                removed = min(m.stacks, count)
                m.stacks -= removed
                category = battle.globals.classify_mark(m.name)
                battle.globals.apply_mark(team, m.name, category, removed)
                label = getattr(ctx.get('self'), 'name', '?')
                return [f'{label} 偷取{m.name}×{removed}']
        return []

    @staticmethod
    def _apply_convert_mark(eff_dict: dict, ctx: dict) -> list[str]:
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if not battle:
            return []
        target = ctx.get('target') or ctx.get('self')
        if not target:
            return []
        source_name = eff_dict.get('source_effect', '')
        mark_name = eff_dict.get('mark_name', '')
        ratio = eff_dict.get('ratio', 1.0)
        effects = [e for e in target.effects
                   if e.category == 'abnormal' and e.name == source_name]
        total_stacks = sum(e.stacks for e in effects)
        if total_stacks <= 0:
            return []
        marks = max(1, int(total_stacks * ratio))
        consumed = int(marks / ratio) if ratio > 0 else total_stacks
        for e in effects:
            remove = min(e.stacks, consumed)
            e.stacks -= remove
            consumed -= remove
            if consumed <= 0:
                break
        mark_team_key = eff_dict.get('mark_target_team', 'opp')
        mark_team = ('B' if team == 'A' else 'A') if mark_team_key == 'opp' else team
        category = battle.globals.classify_mark(mark_name)
        battle.globals.apply_mark(mark_team, mark_name, category, marks)
        return [f'{target.name} {source_name}→{mark_name}×{marks}']

    # ── 效果继承实现（方向 11）──

    @staticmethod
    def _apply_inherit_effects(eff_dict: dict, ctx: dict) -> list[str]:
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if not battle:
            return []
        source_key = eff_dict.get('source_sprite', 'self')
        target_key = eff_dict.get('inherit_target', 'enemy_new')
        scope = eff_dict.get('scope', 'battlefield')
        source_sprite = ctx.get(source_key)
        via_pending = eff_dict.get('via_pending', False)
        target_sprite = ctx.get(target_key)
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

    # ── 队伍计数器实现（方向 1）──

    @staticmethod
    def _apply_team_counter_add(eff_dict: dict, ctx: dict) -> list[str]:
        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        if not battle:
            return []
        key = eff_dict.get('key', '')
        delta = eff_dict.get('delta', 1)
        target_team = eff_dict.get('counter_team', 'own')
        t = ('B' if team == 'A' else 'A') if target_team == 'opp' else team
        battle.inc_team_counter(t, key, delta)
        return []

    # ── 形态变换实现（方向: 复杂状态转换）──

    @staticmethod
    def _apply_transform(eff_dict: dict, sprite, ctx: dict) -> list[str]:
        """形态变换: 替换 species + skills，保留 HP 比例/能量/效果/计数器。"""
        from backend.common.models import SpeciesStats

        battle = ctx.get('battle')
        if not battle:
            return []

        species_name = eff_dict.get('species', '')
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

        skill_names = eff_dict.get('skills', [])
        new_skills = battle.build_skills(skill_names) if skill_names else []

        if eff_dict.get('reset_hp'):
            sprite.current_hp = sprite.max_hp
        if eff_dict.get('reset_energy'):
            sprite.energy = getattr(sprite, 'max_energy', 10)

        events = sprite.transform(new_species, new_skills)
        return events

    # ── 特性交互实现 ──

    @staticmethod
    def _apply_suppress_trait(eff_dict: dict, ctx: dict) -> list[str]:
        """压制目标精灵的特性直到离场。"""
        target_key = eff_dict.get('target', 'target')
        target = ctx.get(target_key)
        if not target:
            return []
        target._trait_suppressed = True
        # 清除缓存的 trait handler
        target._trait_handler = None
        return [f'{target.name} 特性被压制']

    @staticmethod
    def _apply_remove_trait(eff_dict: dict, ctx: dict) -> list[str]:
        """移除目标精灵的特性（效果等同 suppress 但永久，可被替换）。"""
        target_key = eff_dict.get('target', 'target')
        target = ctx.get(target_key)
        if not target:
            return []
        target._trait_suppressed = True
        target._trait_handler = None
        # 可选修改 species.ability 为空
        new_ability = eff_dict.get('new_ability', '')
        if new_ability:
            target.species.ability = new_ability
            target._trait_suppressed = False
            target._trait_handler = None
            return [f'{target.name} 特性变为 {new_ability}']
        return [f'{target.name} 特性被移除']

    @staticmethod
    def _apply_copy_trait(eff_dict: dict, ctx: dict) -> list[str]:
        """复制目标精灵的特性。"""
        target_key = eff_dict.get('copy_from', 'target')
        source = ctx.get(target_key)
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

    # ── 延时效果实现 ──

    @staticmethod
    def _apply_schedule(eff_dict: dict, ctx: dict) -> list[str]:
        """注册延时效果，在未来回合结算。"""
        battle = ctx.get('battle')
        if not battle:
            return []
        turns = eff_dict.get('turns', 1)
        target_turn = battle.turn + turns
        scheduled = {
            'turn': target_turn,
            'phase': eff_dict.get('phase', 'start'),
            'effects': eff_dict.get('effects', []),
            'source': ctx.get('self'),
            'ctx_snapshot': {
                'team': ctx.get('team', 'A'),
                'target': eff_dict.get('target', 'self'),
            },
        }
        battle.scheduled_effects.append(scheduled)
        sprite = ctx.get('self')
        label = getattr(sprite, 'name', '?') if sprite else '?'
        return [f'{label}: 延时效果注册({turns}回合后)']

    # ── 行动修改实现 ──

    @staticmethod
    def _match_action(eff_dict: dict, action) -> bool:
        """检查 action 是否匹配 forbid 条件。"""
        kind = eff_dict.get('action_kind', '')
        if kind and getattr(action, 'kind', '') != kind:
            return False
        slot = eff_dict.get('slot', -1)
        if slot >= 0 and getattr(action, 'skill_index', -1) != slot:
            return False
        return True

    @staticmethod
    def _apply_action_force(eff_dict: dict, action, ctx: dict):
        """强制替换行动。支持 force_gather / force_skill:N / force_switch:N。"""
        force = eff_dict.get('force', 'gather')
        if force == 'gather':
            from backend.sim.action import Action
            return Action(kind='gather')
        if force.startswith('skill:'):
            slot = int(force.split(':')[1])
            from backend.sim.action import Action
            return Action(kind='skill', skill_index=slot)
        if force.startswith('switch:'):
            idx = int(force.split(':')[1])
            from backend.sim.action import Action
            return Action(kind='switch', switch_index=idx)
        return None

    @staticmethod
    def _apply_action_modifier(eff_dict: dict, available: list, ctx: dict) -> list:
        """修改可选行动列表（用于 JSON 数据驱动特性）。"""
        action = eff_dict.get('action', 'forbid_skill')
        if action == 'forbid_skill':
            slot = eff_dict.get('slot', -1)
            if slot >= 0 and slot < len(available):
                available = [a for i, a in enumerate(available) if i != slot]
        elif action == 'forbid_gather':
            available = [a for a in available if getattr(a, 'kind', '') != 'gather']
        elif action == 'restrict_slots':
            slots = eff_dict.get('slots', [])
            available = [a for i, a in enumerate(available) if i in slots]
        elif action == 'seal_all_but':
            slot = eff_dict.get('slot', 0)
            available = [a for i, a in enumerate(available) if i == slot]
        return available

    @staticmethod
    def _handle_pending_effects(pending: list[dict], ctx: dict) -> list[str]:
        from backend.sim.sprite import StatusEffect

        battle = ctx.get('battle')
        team = ctx.get('team', 'A')
        sprite = ctx.get('self')
        if not battle or not sprite:
            return []

        battle.pending_effects.setdefault(team, [])
        for eff_dict in pending:
            kind = eff_dict.get('kind', 'stat')
            if kind == 'state':
                battle.pending_effects[team].append(StatusEffect(
                    name=eff_dict.get('name', ''),
                    category='state',
                    scope=eff_dict.get('scope', 'battlefield'),
                    source=eff_dict.get('source', ''),
                ))
            elif kind == 'stat':
                battle.pending_effects[team].append(StatusEffect(
                    name=eff_dict.get('name', ''),
                    category='stat',
                    stat_key=eff_dict.get('stat', ''),
                    steps=eff_dict.get('steps', 0),
                    scope=eff_dict.get('scope', 'battlefield'),
                    source=eff_dict.get('source', ''),
                ))
            elif kind == 'abnormal':
                battle.pending_effects[team].append(StatusEffect(
                    name=eff_dict.get('name', ''),
                    category='abnormal',
                    stacks=eff_dict.get('stacks', 1),
                    scope=eff_dict.get('scope', 'battlefield'),
                    source=eff_dict.get('source', ''),
                ))

        return [f'{sprite.name}: 离场效果→下一入场']

    @staticmethod
    def _apply_flags(flags: dict, ctx: dict) -> list[str]:
        """设置 sprite flags 或 counters（方向 9）。

        格式: {"_escape_pending": true, "counters.times_entered": 0}
        """
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


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════

_STEP_UNIT: dict[str, int] = {
    'power': 10, 'priority': 1, 'energy_cost': 1,
    'combo': 1, 'life_drain': 10, 'speed': 10,
}


# ═══════════════════════════════════════════════════════════════════
# 内置条件函数注册
# ═══════════════════════════════════════════════════════════════════

def _builtin_is_weekend(ctx):
    import datetime
    return datetime.date.today().weekday() >= 5

register_condition_fn('is_weekend', _builtin_is_weekend)


# ═══════════════════════════════════════════════════════════════════
# JSON 加载 & 注册
# ═══════════════════════════════════════════════════════════════════


def load_data_trait(filepath: str) -> DataDrivenTrait | None:
    """从 JSON 文件加载一个数据驱动特性。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    name = data.get('name', '')
    triggers = data.get('triggers', [])
    trait_id = data.get('id', 0)
    if not name or not triggers:
        return None

    return DataDrivenTrait(name, triggers, trait_id=trait_id)


def register_data_traits(data_dir: str) -> int:
    """扫描 data_dir 下所有 .json 文件，加载并缓存到 _DATA_TRAIT_INSTANCES。

    数据驱动特性通过 get_data_trait_instance() 查找，优先于 TRAIT_REGISTRY。
    返回成功加载的数量。
    """
    count = 0
    root = Path(data_dir)
    if not root.is_dir():
        return 0

    for fpath in sorted(root.glob('*.json')):
        trait = load_data_trait(str(fpath))
        if trait is not None:
            _DATA_TRAIT_INSTANCES[trait.name] = trait
            if trait.trait_id:
                _DATA_TRAIT_INSTANCES_BY_ID[trait.trait_id] = trait
            count += 1

    return count


# 数据驱动特性实例缓存
_DATA_TRAIT_INSTANCES: dict[str, DataDrivenTrait] = {}
_DATA_TRAIT_INSTANCES_BY_ID: dict[int, DataDrivenTrait] = {}


def get_data_trait_instance(name_or_id) -> DataDrivenTrait | None:
    """获取数据驱动特性的预构造实例。支持名称(str)或ID(int)查找。"""
    if isinstance(name_or_id, int):
        return _DATA_TRAIT_INSTANCES_BY_ID.get(name_or_id)
    return _DATA_TRAIT_INSTANCES.get(name_or_id)
