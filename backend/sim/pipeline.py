"""backend/sim/pipeline.py — 回合管线。

TurnPipeline: 回合开始阶段（传动 / 位置效果预扫描 / trait / 不朽）
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .traits import dispatch_turn_start
from .traits.trait_engine import fire_hook

if TYPE_CHECKING:
    from .battle import Battle
    from .battleskill import BattleSkill


# ═══════════════════════════════════════════════════════════════════════
# TurnPipeline: 回合开始阶段
# ═══════════════════════════════════════════════════════════════════════

class TurnPipeline:
    """回合管线：管理回合开始阶段的所有效果。"""

    @staticmethod
    def execute_turn_start(battle: 'Battle') -> list[str]:
        """执行回合开始阶段。"""
        events: list[str] = []

        # 1. 延时效果结算（phase=start）
        events += battle._execute_scheduled_effects('start')

        # 2. 传动（回合开始自动执行）
        prev_a: list[str] = []
        prev_b: list[str] = []
        if not battle.player_a.active.is_fainted:
            prev_a = [bs.name for bs in battle.player_a.active.skills]
            events += battle._apply_transmission(battle.player_a.active)
        if not battle.player_b.active.is_fainted:
            prev_b = [bs.name for bs in battle.player_b.active.skills]
            events += battle._apply_transmission(battle.player_b.active)

        # 3. 传动后 hook（机械变式 等）
        for team, sprite, prev in [('A', battle.player_a.active, prev_a), ('B', battle.player_b.active, prev_b)]:
            if sprite.is_fainted or not prev:
                continue
            res = fire_hook('after_transmission', sprite, prev, battle, team)
            if res:
                events += res

        # 4. 位置效果预扫描（传动后、行动前）
        battle._position_power_bonus = TurnPipeline._scan_position_effects(battle)

        # 5. trait turn_start
        if not battle.player_a.active.is_fainted:
            events += dispatch_turn_start(battle.player_a.active, battle, 'A')
        if not battle.player_b.active.is_fainted:
            events += dispatch_turn_start(battle.player_b.active, battle, 'B')

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
                    new = player.active
                    new.clear_effects('battlefield')
                    new.entry_turn = battle.turn
                    new.first_action = True
                    new.inc_counter('times_entered')
                    events.append(f'{old_active.name}↓ {new.name}↑(不朽复活)')

        return events

    @staticmethod
    def _scan_position_effects(battle: 'Battle') -> dict[tuple[str, int], int]:
        """扫描双方精灵所有技能，预计算 skill_at → stat power 威力加成。
        返回 {(team, skill_index): power_bonus}。"""
        result: dict[tuple[str, int], int] = {}
        for team in ('A', 'B'):
            player = battle.get_player(team)
            sprite = player.active
            if sprite.is_fainted:
                continue
            for i, bs in enumerate(sprite.skills):
                bonus = TurnPipeline._extract_position_power_bonus(bs, i)
                if bonus:
                    result[(team, i)] = bonus
        return result

    @staticmethod
    def _extract_position_power_bonus(bs: 'BattleSkill', skill_index: int) -> int:
        """提取单个技能中 skill_at 条件下的 stat power 总加成。"""
        total = 0
        for eff in bs.effects:
            if getattr(eff, 'kind', '') != 'conditional':
                continue
            when = getattr(eff, 'when', None) or {}
            if when.get('kind') != 'skill_at':
                continue
            if skill_index not in when.get('positions', []):
                continue
            for sub in getattr(eff, 'then', []):
                if getattr(sub, 'kind', '') == 'stat' and getattr(sub, 'stat', '') == 'power':
                    steps = int(getattr(sub, 'steps', 0) or 0)
                    total += steps * 10  # _STEP_UNIT['power'] = 10
        return total
