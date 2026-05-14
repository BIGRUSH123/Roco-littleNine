"""scripts/sim/battle.py — 对局引擎

回合 = 开始阶段 → 选择阶段 → 结算阶段 → 结束阶段
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .globals import GlobalEffects
from .resolver import SkillResolver, TurnContext
from .battleskill import BattleSkill
from .action import Action
from .battle_mechanics import BattleMechanicsMixin
from scripts.common.skill_trait_ids import TRAIT_星地善良
from .traits import (
    dispatch_entry, dispatch_leave, dispatch_turn_start, dispatch_turn_end,
    dispatch_counter_success, dispatch_faint,
    dispatch_abnormal_tick, dispatch_before_action,
)
from .traits.trait_engine import (
    fire_hook, fire_hook_first, get_data_trait_instance, DataDrivenTrait,
)

if TYPE_CHECKING:
    from .player import Player
    from .sprite import Sprite
    from .skill import Skill
    from .agent import Agent


@dataclass
class TurnRecord:
    """单回合记录。"""
    turn: int
    first_team: str = ''        # "A" or "B"
    action_a: str = ''
    action_b: str = ''
    item_used_a: str = ''
    item_used_b: str = ''
    events: list[str] = field(default_factory=list)
    sprite_a_hp: int = 0
    sprite_b_hp: int = 0
    sprite_a_energy: int = 0
    sprite_b_energy: int = 0
    weather: str = ''

    def summary(self) -> str:
        return (
            f'T{self.turn} [{self.first_team}先] '
            f'A: {self.action_a} | B: {self.action_b} '
            f'(A HP={self.sprite_a_hp} E={self.sprite_a_energy}, '
            f'B HP={self.sprite_b_hp} E={self.sprite_b_energy})'
        )


class Battle(BattleMechanicsMixin):
    """对局引擎。回合调度 + 动作执行。场地变动由 BattleMechanicsMixin 提供。"""

    MAX_TURNS = 150

    def __init__(
        self, player_a: 'Player', player_b: 'Player',
        weather: str = '', verbose: bool = True,
    ):
        self.player_a = player_a
        self.player_b = player_b
        self.globals = GlobalEffects()
        if weather:
            self.globals.set_weather(weather)
        self.turn: int = 0
        self.log: list[TurnRecord] = []
        self.winner: str | None = None
        self._resolver = SkillResolver()
        self._agent_a: 'Agent | None' = None
        self._agent_b: 'Agent | None' = None
        self.verbose = verbose
        self._borrowed_restore: dict[tuple[str, int], 'Skill'] = {}
        self._wish_restore: dict[tuple[str, int], 'BattleSkill'] = {}   # 愿力一回合后还原
        self.team_counters: dict[str, dict[str, int]] = {'A': {}, 'B': {}}  # pre-entry accumulators
        self.pending_effects: dict[str, list] = {'A': [], 'B': []}  # leave-buff → next entry
        self.scheduled_effects: list[dict] = []  # 延时效果队列 [{turn, phase, effects, ...}]
        self.species_db = None  # 由 SimFactory 注入，供形态变换查询
        self.skill_loader = None  # 由 SimFactory 注入，供形态变换加载技能

    def lookup_species(self, name: str, form: str = ''):
        """形态变换时查询目标物种。由 SimFactory 注入数据库后可用。"""
        if self.species_db is None:
            return None
        return self.species_db.get(name, form)

    def lookup_species_by_number(self, number: str, form: str = ''):
        """按精灵编号查找形态（萌化退化用）。"""
        if self.species_db is None:
            return None
        return self.species_db.lookup_by_number(number, form)

    def build_skills(self, skill_names: list[str]) -> list:
        """形态变换时构建技能列表。由 SimFactory 注入后可用。"""
        if self.skill_loader is None:
            return []
        return self.skill_loader(skill_names)

    @property
    def is_finished(self) -> bool:
        return self.winner is not None or self.turn >= self.MAX_TURNS

    def get_player(self, team: str) -> 'Player':
        return self.player_a if team == 'A' else self.player_b

    def get_opponent(self, team: str) -> 'Player':
        return self.player_b if team == 'A' else self.player_a

    def inc_team_counter(self, team: str, key: str, amount: int = 1) -> None:
        """增量队伍级事件计数器（供 pre-entry accumulator 特性使用）。"""
        d = self.team_counters[team]
        d[key] = d.get(key, 0) + amount

    def get_team_counter(self, team: str, key: str) -> int:
        """读取队伍级事件计数器。"""
        return self.team_counters.get(team, {}).get(key, 0)

    def _get_agent(self, team: str) -> 'Agent':
        return self._agent_a if team == 'A' else self._agent_b  # type: ignore

    # ═══════════════════════════════════════════════════════════════
    # 回合主入口
    # ═══════════════════════════════════════════════════════════════

    def execute_turn(self, agent_a: 'Agent', agent_b: 'Agent') -> TurnRecord:
        self.turn += 1
        self._agent_a = agent_a
        self._agent_b = agent_b

        s_a = self.player_a.active
        s_b = self.player_b.active

        record = TurnRecord(turn=self.turn, weather=self.globals.weather)

        # 0. 首发入场（回合1触发 entry trait）
        events: list[str] = []
        if self.turn == 1:
            events += dispatch_entry(self.player_a.active, self, 'A')
            events += dispatch_entry(self.player_b.active, self, 'B')

        # 1. 回合开始阶段
        events += self._phase_turn_start()

        # 2. 行动选择阶段（道具不互见）
        action_a, item_a = self._select_action(agent_a, 'A')
        action_b, item_b = self._select_action(agent_b, 'B')
        record.action_a = self._describe_action('A', action_a)
        record.action_b = self._describe_action('B', action_b)
        record.item_used_a = item_a
        record.item_used_b = item_b

        # 3. 行动结算阶段
        events += self._phase_resolve(action_a, action_b, record)

        # 4. 回合结束阶段
        events += self._phase_turn_end()

        # 回合标题（插入到事件列表头部）
        a_short = self._action_short(record.action_a)
        b_short = self._action_short(record.action_b)
        header = f'[回合{self.turn}] {self.player_a.active.name}：{a_short} | {self.player_b.active.name}：{b_short}'
        events.insert(0, header)

        # 填充记录（用回合结束时的实时精灵引用）
        record.events = events
        final_a = self.player_a.active
        final_b = self.player_b.active
        record.sprite_a_hp = final_a.current_hp
        record.sprite_b_hp = final_b.current_hp
        record.sprite_a_energy = final_a.energy
        record.sprite_b_energy = final_b.energy

        self.log.append(record)
        return record

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: 回合开始
    # ═══════════════════════════════════════════════════════════════

    def _phase_turn_start(self) -> list[str]:
        """回合开始效果（天气、场地、特性触发）。"""
        events: list[str] = []
        # 延时效果结算（phase=start）
        events += self._execute_scheduled_effects('start')
        # 杠杆置换（先于传动）— 预留钩子
        # 传动（回合开始自动执行）
        prev_a = [bs.name for bs in self.player_a.active.skills] if not self.player_a.active.is_fainted else []
        prev_b = [bs.name for bs in self.player_b.active.skills] if not self.player_b.active.is_fainted else []
        if not self.player_a.active.is_fainted:
            events += self._apply_transmission(self.player_a.active)
        if not self.player_b.active.is_fainted:
            events += self._apply_transmission(self.player_b.active)
        # 传动后 hook（机械变式 等）
        for team, sprite, prev in [('A', self.player_a.active, prev_a), ('B', self.player_b.active, prev_b)]:
            if sprite.is_fainted or not prev:
                continue
            res = fire_hook('after_transmission', sprite, prev, self, team)
            if res:
                events += res
        # trait turn_start
        if not self.player_a.active.is_fainted:
            events += dispatch_turn_start(self.player_a.active, self, 'A')
        if not self.player_b.active.is_fainted:
            events += dispatch_turn_start(self.player_b.active, self, 'B')

        # 不朽：力竭后 3 回合复活（扫描 bench）
        for team in ('A', 'B'):
            player = self.get_player(team)
            for i, s in enumerate(player.team):
                if not s.is_fainted:
                    continue
                faint_turn = getattr(s, '_faint_turn', 0)
                if faint_turn <= 0:
                    continue
                if self.turn - faint_turn < 3:
                    continue
                # 复活
                s.current_hp = max(1, s.max_hp)
                s.energy = min(5, s.energy + 3)
                s._faint_turn = 0
                events.append(f'{s.name} 不朽: 第{faint_turn}回合力竭 → 第{self.turn}回合复活')
                # 如果该队无存活精灵，自动换上
                if player.active.is_fainted and i != player.active_index:
                    old_active = player.active
                    player.active_index = i
                    new = player.active
                    new.clear_effects('battlefield')
                    new.entry_turn = self.turn
                    new.first_action = True
                    new.inc_counter('times_entered')
                    events.append(f'{old_active.name}↓ {new.name}↑(不朽复活)')

        return events

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: 行动选择（含私有道具循环）
    # ═══════════════════════════════════════════════════════════════

    def _select_action(self, agent: 'Agent', team: str) -> tuple[Action, str]:
        """道具循环：使用道具后重新选择。返回 (最终行动, 道具名)。"""
        item_used = ''
        while True:
            action = agent.choose_action(self)
            # before_action hook: 特性可修改/否决选技
            sprite = self.get_player(team).active
            modified = dispatch_before_action(sprite, action, self, team)
            if modified is not None:
                action = modified
            if action.kind == 'item':
                item_used = self._resolve_item(team)
                continue
            return action, item_used

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: 行动结算（优先级排序）
    # ═══════════════════════════════════════════════════════════════

    def _phase_resolve(self, action_a: Action, action_b: Action, record: TurnRecord) -> list[str]:
        events: list[str] = []
        a_kind = action_a.kind
        b_kind = action_b.kind

        # 双方换宠 → 随机先后（无技能方，first_team 留空）
        if a_kind == 'switch' and b_kind == 'switch':
            if random.random() < 0.5:
                events += self._resolve_switch('A', action_a)
                if not self.is_finished:
                    events += self._resolve_switch('B', action_b)
            else:
                events += self._resolve_switch('B', action_b)
                if not self.is_finished:
                    events += self._resolve_switch('A', action_a)
            return events

        # 单方换宠 + 单方技能/聚能 → 先换宠，后技能
        # 技能方是唯一的行动方 → first_team = 技能方
        if a_kind == 'switch':
            events += self._resolve_switch('A', action_a)
            record.first_team = 'B'
            if not self.is_finished and not self.player_b.active.is_fainted:
                events += self._resolve_single_action('B', action_b, opponent_switched=True)
            return events

        if b_kind == 'switch':
            events += self._resolve_switch('B', action_b)
            record.first_team = 'A'
            if not self.is_finished and not self.player_a.active.is_fainted:
                events += self._resolve_single_action('A', action_a, opponent_switched=True)
            return events

        # 双方技能/聚能 → 优先级判定
        events += self._resolve_both_skills(action_a, action_b, record)
        return events

    # ── 双方技能/聚能 ──

    def _resolve_both_skills(self, action_a: Action, action_b: Action, record: TurnRecord) -> list[str]:
        events: list[str] = []
        s_a = self.player_a.active
        s_b = self.player_b.active

        skill_a = self._get_skill('A', action_a)
        skill_b = self._get_skill('B', action_b)

        # 应对判定（双向检查，冷却中的技能视为未使用，不能应对）
        counter_a = False
        counter_b = False
        if skill_a and skill_b:
            if skill_a.cooldown <= 0:
                counter_a = SkillResolver.resolve_counter(skill_b, skill_a)
            if skill_b.cooldown <= 0:
                counter_b = SkillResolver.resolve_counter(skill_a, skill_b)
        countered = counter_a or counter_b

        # countered_skill:  被我方反击的对方技能（用于 reflect_damage / interrupt）
        # countering_skill: 反击了我方的对方技能（用于 damage_reduction 注入）
        countered_skill_a = skill_b if counter_a else None
        countered_skill_b = skill_a if counter_b else None
        countering_skill_a = skill_b if counter_b else None
        countering_skill_b = skill_a if counter_a else None

        if countered:
            # 应对成功 → 双方同时（均视为"同时"，无先后之分）
            events += self._execute_single_action(
                'A', action_a, is_countered=counter_b,
                countered_skill=countered_skill_a,
                countering_skill=countering_skill_a, is_first=True,
            )
            self._check_faint_interrupt('A', events)
            self._check_faint_interrupt('B', events)
            if not self.is_finished:
                events += self._execute_single_action(
                    'B', action_b, is_countered=counter_a,
                    countered_skill=countered_skill_b,
                    countering_skill=countering_skill_b, is_first=True,
                )
            # trait: counter success hooks
            if counter_a:
                self.inc_team_counter('A', 'counter_success')
                events += dispatch_counter_success(s_a, countered_skill_a, self, 'A')
            if counter_b:
                self.inc_team_counter('B', 'counter_success')
                events += dispatch_counter_success(s_b, countered_skill_b, self, 'B')
            return events

        # 无应对 → 按优先级先后执行
        priority_a = self._effective_priority('A', action_a)
        priority_b = self._effective_priority('B', action_b)

        if priority_a > priority_b:
            first_team, first_action = 'A', action_a
            second_team, second_action = 'B', action_b
        elif priority_b > priority_a:
            first_team, first_action = 'B', action_b
            second_team, second_action = 'A', action_a
        else:
            # 优先级相等 → 比速度
            speed_a = s_a.effective_stat('speed') - self.globals.mark_speed_penalty('A')
            speed_b = s_b.effective_stat('speed') - self.globals.mark_speed_penalty('B')
            if speed_a >= speed_b:
                first_team, first_action = 'A', action_a
                second_team, second_action = 'B', action_b
            else:
                first_team, first_action = 'B', action_b
                second_team, second_action = 'A', action_a

        # 记录先手方
        record.first_team = first_team

        # 先手执行 (is_first=True)
        events += self._execute_single_action(first_team, first_action, is_first=True)
        self._check_faint_interrupt(first_team, events)

        # 后手执行 (is_first=False)，力竭中断则跳过
        if not self.is_finished:
            if not self.get_player(second_team).active.is_fainted:
                events += self._execute_single_action(second_team, second_action, is_first=False)
                self._check_faint_interrupt(second_team, events)

        return events

    # ── 单方行动执行 ──

    def _resolve_single_action(self, team: str, action: Action,
                               opponent_switched: bool = False) -> list[str]:
        """执行单方行动（换宠已处理，此处仅技能/聚能）。
        此路径下本侧是唯一技能方 → is_first=True。"""
        if action.kind in ('skill', 'gather'):
            return self._execute_single_action(team, action, is_first=True,
                                               opponent_switched=opponent_switched)
        return []

    def _execute_single_action(
        self, team: str, action: Action,
        is_countered: bool = False,
        countered_skill: 'BattleSkill | None' = None,
        countering_skill: 'BattleSkill | None' = None,
        is_first: bool = False,
        opponent_switched: bool = False,
    ) -> list[str]:
        """执行单个玩家的技能/聚能行动。委托给 SkillPipeline。"""
        from .pipeline import SkillPipeline
        pipeline = SkillPipeline(
            battle=self, team=team, action=action,
            is_countered=is_countered,
            countered_skill=countered_skill,
            countering_skill=countering_skill,
            is_first=is_first,
            opponent_switched=opponent_switched,
        )
        return pipeline.execute()

    # ── 辅助 ──

    def _get_skill(self, team: str, action: Action) -> 'Skill | None':
        if action.kind != 'skill' or action.skill_index is None:
            return None
        sprite = self.get_player(team).active
        if action.skill_index < len(sprite.skills):
            return sprite.skills[action.skill_index]
        return None

    def _effective_priority(self, team: str, action: Action) -> int:
        """计算有效先手等级（聚能=0，技能=基础+修正）。"""
        if action.kind == 'gather':
            return 0
        skill = self._get_skill(team, action)
        base = skill.priority if skill else 0
        return base + self.get_player(team).active.priority_mod

    # ── 延时效果结算 ──

    def _execute_scheduled_effects(self, phase: str) -> list[str]:
        """执行到期延时效果。返回事件列表。"""
        events: list[str] = []
        due = [s for s in self.scheduled_effects
               if s['turn'] <= self.turn and s['phase'] == phase]
        for sched in due:
            self.scheduled_effects.remove(sched)
            snap = sched.get('ctx_snapshot', {})
            team = snap.get('team', 'A')
            sprite = snap.get('self') or self.get_player(team).active
            if sprite is None:
                continue
            # DataDrivenTrait 延时 trigger
            trait_name = sched.get('trait_name', '')
            if trait_name:
                trait = get_data_trait_instance(trait_name)
                if trait:
                    # 通过 on_ 前缀调用对应 hook 方法
                    hook = sched.get('hook', '')
                    method_name = f'on_{hook}'
                    method = getattr(trait, method_name, None)
                    if method:
                        result = method(sprite, self, team)
                        if isinstance(result, list):
                            events += result
                    else:
                        # 回退: 手动构建 ctx 调用 _fire
                        ctx = {
                            'self': sprite, 'battle': self, 'team': team,
                            'target': snap.get('target'),
                            'attacker': snap.get('attacker'),
                        }
                        events += trait._fire(hook, ctx)
            else:
                # 直接 effects 列表（schedule 特殊效果）
                ctx = {
                    'self': sprite, 'battle': self, 'team': team,
                    'target': snap.get('target'),
                }
                for eff in sched.get('effects', []):
                    events += DataDrivenTrait._apply_effect(eff, ctx)
        return events

    # ═══════════════════════════════════════════════════════════════
    # Phase 4: 回合结束
    # ═══════════════════════════════════════════════════════════════

    def _phase_turn_end(self) -> list[str]:
        events: list[str] = []

        # 延时效果结算（phase=end）
        events += self._execute_scheduled_effects('end')

        # 借用还原
        for (team, si), original in self._borrowed_restore.items():
            sprite = self.get_player(team).active
            if si < len(sprite.skills):
                bs = sprite.skills[si]
                borrowed_name = bs.name
                bs.replaced_by = None
                events.append(f'{sprite.name} 归还 {borrowed_name}')
        self._borrowed_restore.clear()

        # 愿力还原（一回合后换回原技能）
        for (team, si), original in self._wish_restore.items():
            sprite = self.get_player(team).active
            if not sprite.is_fainted and si < len(sprite.skills):
                current_name = sprite.skills[si].name
                sprite.skills[si] = original
                events.append(f'{sprite.name} 愿力结束({current_name}→{original.name})')
        self._wish_restore.clear()

        # 返场结算（过载回路）：清 battlefield 效果 + 下回合双倍
        for team in ('A', 'B'):
            player = self.get_player(team)
            sprite = player.active
            if not sprite.is_fainted and sprite.pending_return:
                sprite.pending_return = False
                sprite.extra_skill_use = True
                events += self._resolve_return(team)

        sprites: dict[str, 'Sprite'] = {}
        if not self.player_a.active.is_fainted:
            sprites['A'] = self.player_a.active
        if not self.player_b.active.is_fainted:
            sprites['B'] = self.player_b.active

        events += SkillResolver.turn_end(sprites, self.globals)

        # ── 异常 tick trait 通知（只读，不修改层数/HP）──
        for team, sprite in list(sprites.items()):
            for e in sprite.effects:
                if e.category != 'abnormal':
                    continue
                if e.name in ('灼烧', '中毒'):
                    dmg = max(1, round(sprite.max_hp / 16)) if e.name == '灼烧' else max(1, round(sprite.max_hp / 8))
                    events += dispatch_abnormal_tick(sprite, e.name, dmg, self, team)
                    opp_team = 'B' if team == 'A' else 'A'
                    opp = self.get_opponent(team).active
                    if not opp.is_fainted:
                        events += dispatch_abnormal_tick(opp, e.name, dmg, self, opp_team)

        # ── trait turn end hook ──
        for team, sprite in sprites.items():
            events += dispatch_turn_end(sprite, self, team)

        # 星地善良：回合末若己方能量=0，板凳星地善良替换上场
        for team in ('A', 'B'):
            player = self.get_player(team)
            active = player.active
            if active.is_fainted:
                continue
            # Hook: turn_end_bench_check — bench 精灵回合末主动替换检查
            bench_result = fire_hook_first('turn_end_bench_check', self, team, active, player)
            swap_index = bench_result[0] if isinstance(bench_result, tuple) else None
            swap_reason = bench_result[1] if isinstance(bench_result, tuple) and len(bench_result) > 1 else ''
            if swap_index is not None:
                pass  # hook already determined the swap
            elif active.energy > 0:
                continue
            if swap_index is None:
                for i, bench_sprite in enumerate(player.team):
                    if i == player.active_index or bench_sprite.is_fainted:
                        continue
                    from .traits import get_trait
                    h = get_trait(bench_sprite)
                    if h and h.trait_id == TRAIT_星地善良:
                        swap_index = i
                        swap_reason = '星地善良'
                        break
            if swap_index is not None and swap_index != player.active_index:
                old = active
                player.active_index = swap_index
                new = player.active
                new.clear_effects('battlefield')
                new.entry_turn = self.turn
                new.first_action = True
                new.inc_counter('times_entered')
                events.append(f'{old.name} 能量0↓ {new.name}↑({swap_reason})' if swap_reason else f'{old.name}↓ {new.name}↑')
                events += dispatch_leave(old, self, team)
                events += dispatch_entry(new, self, team)

        # 冻结斩杀检查
        for team in ('A', 'B'):
            sprite = self.get_player(team).active
            if not sprite.is_fainted and sprite.check_freeze_death():
                events.append(f'{sprite.name} 冻结斩杀(冻结{sprite.frozen_hp}HP)')

        # 回合结束力竭检查
        for team in ('A', 'B'):
            self._check_faint_interrupt(team, events)

        return events

    # ═══════════════════════════════════════════════════════════════
    # 批量运行
    # ═══════════════════════════════════════════════════════════════

    def run(self, agent_a: 'Agent', agent_b: 'Agent') -> str | None:
        """完整对局流程：出场选择 → 回合循环 → 终局。"""
        self._agent_a = agent_a
        self._agent_b = agent_b

        # 出场选择（双方轮流选首发精灵）
        lead_a = agent_a.choose_lead(self)
        lead_b = agent_b.choose_lead(self)
        self.player_a.active_index = lead_a
        self.player_b.active_index = lead_b

        if self.verbose:
            print(f'\n{"═" * 50}')
            print(f'  {self.player_a.name} ({self.player_a.active.name})'
                  f'  vs  {self.player_b.name} ({self.player_b.active.name})')
            print(f'{"═" * 50}')

        # 回合循环
        while not self.is_finished:
            record = self.execute_turn(agent_a, agent_b)
            if self.verbose:
                self._print_turn(record)

        # 终局
        result = self.winner or 'draw'
        if self.verbose:
            self._print_result(result)
        agent_a.on_game_end(result)
        agent_b.on_game_end(result)
        return result

    # ── 日志输出 ──

    def _describe_action(self, team: str, action: Action) -> str:
        """将 Action 转为可读字符串（含技能名）。"""
        if action.kind == 'skill' and action.skill_index is not None:
            sprite = self.get_player(team).active
            if action.skill_index < len(sprite.skills):
                return f'skill:{sprite.skills[action.skill_index].name}'
        if action.kind == 'switch' and action.switch_index is not None:
            player = self.get_player(team)
            if action.switch_index < len(player.team):
                return f'switch:{player.team[action.switch_index].name}'
        return action.kind

    @staticmethod
    def _action_short(action_str: str) -> str:
        """压缩行动字符串：'skill:闪击' → '闪击'，'switch:迪莫' → '↓迪莫'。"""
        if action_str.startswith('skill:'):
            return action_str[6:]
        if action_str.startswith('switch:'):
            return '↓' + action_str[7:]
        return action_str

    def _print_turn(self, r: TurnRecord) -> None:
        """单回合紧凑日志。"""
        a_short = self._action_short(r.action_a)
        b_short = self._action_short(r.action_b)
        first = r.first_team or '?'

        parts = [f'T{r.turn:03d} [{first}先]']
        parts.append(f'A:{a_short}  B:{b_short}')

        if r.events:
            key_events = [e for e in r.events if 'HP' in e or '力竭' in e or '脱离' in e]
            shown = key_events if key_events else r.events[:2]
            parts.append('| ' + ' '.join(shown))

        weather = f' [{r.weather}]' if r.weather else ''
        parts.append(f'(A:{r.sprite_a_hp}HP/{r.sprite_a_energy}E'
                     f' B:{r.sprite_b_hp}HP/{r.sprite_b_energy}E{weather})')
        print('  '.join(parts))

    def _print_result(self, result: str) -> None:
        """终局总结。"""
        print(f'\n{"═" * 50}')
        winner_name = (
            self.player_a.name if result == 'A'
            else self.player_b.name if result == 'B'
            else '平局'
        )
        print(f'  对局结束: {winner_name} 胜 ({self.turn}回合)')
        print(f'{"═" * 50}')

        # 回合详情表
        print(f'\n  {"回合":<5} {"先":<3} {"A行动":<12} {"B行动":<12} {"A HP/E":<12} {"B HP/E":<12}')
        print(f'  {"─" * 60}')
        for r in self.log:
            a_short = self._action_short(r.action_a)
            b_short = self._action_short(r.action_b)
            print(f'  T{r.turn:<4d} {r.first_team:<3}'
                  f' {a_short:<12} {b_short:<12}'
                  f' {r.sprite_a_hp}/{r.sprite_a_energy:<8}'
                  f' {r.sprite_b_hp}/{r.sprite_b_energy:<8}')

    def save_log(self, path: str) -> None:
        """将对局日志保存到文件。"""
        from datetime import datetime
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'# 对局记录 — {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
            f.write(f'# {self.player_a.name} vs {self.player_b.name}\n')
            f.write(f'# 结果: {self.winner or "draw"} ({self.turn}回合)\n\n')
            for r in self.log:
                f.write(f'## T{r.turn} [{r.first_team}先]\n')
                f.write(f'- A: {r.action_a}')
                if r.item_used_a:
                    f.write(f' (道具:{r.item_used_a})')
                f.write('\n')
                f.write(f'- B: {r.action_b}')
                if r.item_used_b:
                    f.write(f' (道具:{r.item_used_b})')
                f.write('\n')
                if r.events:
                    f.write('- 事件:\n')
                    for e in r.events:
                        f.write(f'  - {e}\n')
                f.write(f'- 结果: A HP={r.sprite_a_hp} E={r.sprite_a_energy}  '
                        f'B HP={r.sprite_b_hp} E={r.sprite_b_energy}')
                if r.weather:
                    f.write(f'  天气={r.weather}')
                f.write('\n\n')
