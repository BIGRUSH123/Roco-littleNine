"""backend/sim/pipeline.py — 回合管线。

TurnPipeline: 回合开始阶段（trait / 传动 / 位置效果预扫描 / 不朽）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .traits import dispatch_turn_start
from .traits.trait_engine import fire_hook, has_hook

if TYPE_CHECKING:
    from .battle import Battle
    from .battleskill import BattleSkill

_POSITION_POWER_BONUS_CACHE: dict[tuple[str, int], int] = {}


def _literal_number(value) -> float | None:
    """IRValue → float（只认字面量；Query/RefExpr 等动态值返回 None）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    inner = getattr(value, "value", None)
    if isinstance(inner, (int, float)) and not isinstance(inner, bool):
        return float(inner)
    if isinstance(value, dict) and isinstance(value.get("value"), (int, float)):
        return float(value["value"])
    return None


def _skill_at_positions(cond, out: set[int] | None = None) -> set[int]:
    """从编译后的条件树里收集 `skill_at` 的位置集合（支持 and/or/not 嵌套）。"""
    out = set() if out is None else out
    if cond is None:
        return out
    if isinstance(cond, dict):
        if cond.get("cond") == "skill_at":
            try:
                out.add(int(cond.get("position", -1)))
            except (TypeError, ValueError):
                pass
        for sub in (cond.get("conditions") or ()):
            _skill_at_positions(sub, out)
        inner = cond.get("condition")
        if inner is not None:
            _skill_at_positions(inner, out)
        return out
    if getattr(cond, "cond", "") == "skill_at":
        params = getattr(cond, "params", None) or {}
        try:
            out.add(int(params.get("position", -1)))
        except (TypeError, ValueError):
            pass
    for sub in getattr(cond, "conditions", ()) or ():
        _skill_at_positions(sub, out)
    inner = getattr(cond, "condition", None)
    if inner is not None:
        _skill_at_positions(inner, out)
    return out


# ═══════════════════════════════════════════════════════════════════════
# TurnPipeline: 回合开始阶段
# ═══════════════════════════════════════════════════════════════════════

class TurnPipeline:
    """回合管线：管理回合开始阶段的所有效果。"""

    @staticmethod
    def execute_turn_start(battle: Battle) -> list[str]:
        """执行回合开始阶段。"""
        events: list[str] = []

        # 1. 延时效果结算（phase=start）
        events += battle._execute_scheduled_effects('start')

        # 2. trait turn_start。位置型特性先作用于当前槽位，再参与本次传动。
        if not battle.player_a.active.is_fainted:
            events += dispatch_turn_start(battle.player_a.active, battle, 'A')
        if not battle.player_b.active.is_fainted:
            events += dispatch_turn_start(battle.player_b.active, battle, 'B')

        # 3. 传动（回合开始自动执行）
        has_after_transmission_hook = has_hook('after_transmission')
        prev_a: list[str] = []
        prev_b: list[str] = []
        if not battle.player_a.active.is_fainted:
            if has_after_transmission_hook:
                prev_a = [bs.name for bs in battle.player_a.active.skills]
            transmission_events = battle._apply_transmission(battle.player_a.active, team="A")
            events += transmission_events
            if transmission_events or getattr(battle, '_last_transmission_changed', False):
                opp = battle.player_b.active
                events += battle._reapply_position_modifiers("turn_start", "A", battle.player_a.active, opp)
        if not battle.player_b.active.is_fainted:
            if has_after_transmission_hook:
                prev_b = [bs.name for bs in battle.player_b.active.skills]
            transmission_events = battle._apply_transmission(battle.player_b.active, team="B")
            events += transmission_events
            if transmission_events or getattr(battle, '_last_transmission_changed', False):
                opp = battle.player_a.active
                events += battle._reapply_position_modifiers("turn_start", "B", battle.player_b.active, opp)

        # 4. 传动后 hook（机械变式 等）
        if has_after_transmission_hook:
            for team, sprite, prev in [('A', battle.player_a.active, prev_a), ('B', battle.player_b.active, prev_b)]:
                if sprite.is_fainted or not prev:
                    continue
                res = fire_hook('after_transmission', sprite, prev, battle, team)
                if res:
                    events += res

        # 5. 位置效果预扫描（仅用于 API 展示技能面板，MCTS/headless 跳过）
        if not getattr(battle, '_mcts_sim', False):
            battle._position_power_bonus = TurnPipeline._scan_position_effects(battle)

        # 6. 不朽：力竭后 3 回合复活
        for team in ('A', 'B'):
            player = battle.get_player(team)
            for i, s in enumerate(player.team):
                if not s.is_fainted:
                    continue
                faint_turn = getattr(s, '_faint_turn', 0)
                if faint_turn <= 0:
                    continue
                if battle.turn - faint_turn < 3:
                    continue
                s.current_hp = max(1, s.max_hp)
                s.energy = min(5, s.energy + 3)
                s._faint_turn = 0
                events.append(f'{s.name} 不朽: 第{faint_turn}回合力竭 → 第{battle.turn}回合复活')
                if player.active.is_fainted and i != player.active_index:
                    old_active = player.active
                    player.active_index = i
                    battle._invalidate_ctx_team_cache()
                    new = player.active
                    new.clear_effects('battlefield')
                    new.entry_turn = battle.turn
                    new.first_action = True
                    new.inc_counter('times_entered')
                    events.append(f'{old_active.name}↓ {new.name}↑(不朽复活)')

        return events

    @staticmethod
    def _scan_position_effects(battle: Battle) -> dict[tuple[str, int], int]:
        """扫描双方精灵所有技能，预计算 skill_at → stat power 威力加成。
        返回 {(team, skill_index): power_bonus}。"""
        result: dict[tuple[str, int], int] = {}
        for team in ('A', 'B'):
            player = battle.get_player(team)
            sprite = player.active
            if sprite.is_fainted:
                continue
            for i, bs in enumerate(sprite.skills):
                bonus = TurnPipeline._extract_position_power_bonus(battle, bs, i)
                if bonus:
                    result[(team, i)] = bonus
        return result

    @staticmethod
    def _extract_position_power_bonus(battle: Battle, bs: BattleSkill, skill_index: int) -> int:
        """单个技能在 `skill_at` 位置条件下加在**威力**上的总量（展示口径）。

        数据面：`{"when": {"cond": "skill_at", "position": N}, "then": [...威力修正...]}`
        （械斗「位于1号位时威力+60」/磁暴/轮盘/齿轮切开…）。读的是引擎自己的编译产物
        `CompiledSkill.effects`——与实战同一份 IR。此前读 `Skill.effects`（旧 kind
        形式，IR 语料下恒空），该显示器对全库恒为 0。动态公式（Query）不计。
        """
        name = getattr(bs.skill, "name", "")
        cache_key = (name, skill_index)
        cached = _POSITION_POWER_BONUS_CACHE.get(cache_key)
        if cached is not None:
            return cached

        from backend.vm.ir_skill import PowerModOp, StatStageOp, WhenBlock

        total = 0
        try:
            effects = battle._get_skill_record(name).effects
        except (KeyError, FileNotFoundError, ValueError, AttributeError, TypeError):
            effects = ()
        for node in effects or ():
            if not isinstance(node, WhenBlock):
                continue
            if skill_index not in _skill_at_positions(node.cond):
                continue
            for op in node.then:
                if isinstance(op, StatStageOp) and op.stat == "power":
                    total += int(op.steps or 0) * 10     # _STEP_UNIT['power'] = 10
                elif isinstance(op, PowerModOp) and op.attr == "power":
                    value = _literal_number(op.delta)
                    if value is not None:
                        total += int(value)
        _POSITION_POWER_BONUS_CACHE[cache_key] = total
        return total
