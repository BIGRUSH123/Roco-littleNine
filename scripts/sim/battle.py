"""scripts/sim/battle.py — 对局引擎

回合 = 开始阶段 → 选择阶段 → 结算阶段 → 结束阶段
"""

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .globals import GlobalEffects
from .resolver import SkillResolver, TurnContext
from .battleskill import BattleSkill, SkillUse
from .action import Action
from .battle_mechanics import BattleMechanicsMixin

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

    @property
    def is_finished(self) -> bool:
        return self.winner is not None or self.turn >= self.MAX_TURNS

    def get_player(self, team: str) -> 'Player':
        return self.player_a if team == 'A' else self.player_b

    def get_opponent(self, team: str) -> 'Player':
        return self.player_b if team == 'A' else self.player_a

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

        # 1. 回合开始阶段
        events = self._phase_turn_start()

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
        """回合开始效果（预留钩子 — 天气、场地等触发）。"""
        return []

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: 行动选择（含私有道具循环）
    # ═══════════════════════════════════════════════════════════════

    def _select_action(self, agent: 'Agent', team: str) -> tuple[Action, str]:
        """道具循环：使用道具后重新选择。返回 (最终行动, 道具名)。"""
        item_used = ''
        while True:
            action = agent.choose_action(self)
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
                events += self._resolve_single_action('B', action_b)
            return events

        if b_kind == 'switch':
            events += self._resolve_switch('B', action_b)
            record.first_team = 'A'
            if not self.is_finished and not self.player_a.active.is_fainted:
                events += self._resolve_single_action('A', action_a)
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

        # 应对判定（双向检查）
        counter_a = False
        counter_b = False
        if skill_a and skill_b:
            counter_a = SkillResolver.resolve_counter(skill_b, skill_a)
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

    def _resolve_single_action(self, team: str, action: Action) -> list[str]:
        """执行单方行动（换宠已处理，此处仅技能/聚能）。
        此路径下本侧是唯一技能方 → is_first=True。"""
        if action.kind in ('skill', 'gather'):
            return self._execute_single_action(team, action, is_first=True)
        return []

    def _execute_single_action(
        self, team: str, action: Action,
        is_countered: bool = False,
        countered_skill: 'BattleSkill | None' = None,
        countering_skill: 'BattleSkill | None' = None,
        is_first: bool = False,
    ) -> list[str]:
        """执行单个玩家的技能/聚能行动。返回事件列表。"""
        player = self.get_player(team)
        opponent = self.get_opponent(team)
        user = player.active
        target = opponent.active
        events: list[str] = []

        if user.is_fainted:
            return events

        # ── 聚能 ──
        if action.kind == 'gather':
            gained = user.gain_energy(5)
            user.first_action = False
            user.inc_counter('times_gathered')
            events.append(f'{user.name} 聚能+{gained}E(→{user.energy})')
            return events

        # ── 技能 ──
        if action.kind != 'skill' or action.skill_index is None:
            return events

        skill = self._get_skill(team, action)
        if skill is None:
            events.append(f'[错误] {user.name} 无技能[{action.skill_index}]')
            return events

        # 能耗计算
        cost = skill.energy_cost
        cost = round(cost * self.globals.weather_energy_mod(skill.element or ''))
        cost = max(0, cost - self.globals.mark_energy_mod(team))

        if user.energy < cost:
            events.append(f'[能量不足] {user.name} E={user.energy} < {cost}')
            return events

        user.lose_energy(cost)
        user.inc_counter(f'skill_used:{skill.name}')
        user.inc_counter('skills_used')

        # 迸发：第一次行动标记在伤害/效果前消费
        burst_specials = [e for e in skill.effects if getattr(e, 'kind', '') == 'special']
        is_burst = user.first_action and any(e.name == 'burst' for e in burst_specials)
        user.first_action = False

        ctx = TurnContext(turn=self.turn, is_first=is_first,
                          countered_skill=countered_skill)

        use = SkillUse(
            battle_skill=skill,
            is_countered=is_countered,
            is_first=is_first,
            countered_skill=countered_skill,
            countering_skill=countering_skill,
            skill_index=action.skill_index or -1,
        )

        # 消费 next_attack_mult（热身等设置的下次攻击倍率）
        if skill.is_attack and skill.next_attack_mult != 1.0:
            use.modifiers['power_mult'] = use.modifiers.get('power_mult', 1.0) * skill.next_attack_mult
            skill.next_attack_mult = 1.0

        # 动态威力（冰锋横扫/钢钻等）
        for e in skill.effects:
            if getattr(e, 'kind', '') != 'special':
                continue
            if e.name == 'power_by_enemy_energy':
                total_e = sum(bs.energy_cost for bs in target.skills)
                skill.power_override = int(total_e * (e.value or 10))
            elif e.name == 'power_by_adjacent':
                adj_sum = 0
                si = action.skill_index
                if si is not None:
                    for offset in (-1, 1):
                        idx = si + offset
                        if 0 <= idx < len(user.skills):
                            adj_sum += user.skills[idx].power
                skill.power_override = max(1, int(adj_sum * (e.value or 0.333)))

        # 有效连击数：静态 combo + 精灵连击修正
        effective_combo = skill.combo
        if skill.combo >= 1:
            combo_mod = user.effective_stat('combo')
            if combo_mod:
                effective_combo = max(1, effective_combo + combo_mod)
            combo_mult_steps = user.effective_stat('combo_mult')
            if combo_mult_steps > 0:
                effective_combo = max(1, int(effective_combo * (1.0 + combo_mult_steps)))
            dynamic_combo = int(use.modifiers.get('multi_hit', 1.0))
            if dynamic_combo > 1:
                effective_combo = max(effective_combo, dynamic_combo)
                use.modifiers.pop('multi_hit', None)  # 防止 calc_damage 重复乘

        # 额外使用次数（过载回路返场后：技能双倍执行，不耗能）
        extra_uses = 2 if user.extra_skill_use else 1
        user.extra_skill_use = False

        for extra_i in range(extra_uses):
            if extra_i > 0:
                events.append(f'{user.name} {skill.name} 额外使用(不耗能)')

            for hit_i in range(effective_combo):
                if target.is_fainted:
                    break

                if skill.is_attack:
                    damage, dmg_events = self._resolver.calc_damage(
                        user, target, use, self.globals,
                        attacker_team=team,
                    )
                    target.take_damage(damage)
                    target.inc_counter('times_hit')
                    user.inc_counter('times_dealt')
                    combo_label = f' ({hit_i+1}/{effective_combo})' if effective_combo > 1 else ''
                    events.append(f'{user.name} {skill.name} → {target.name} -{damage}HP{combo_label}')
                    events.extend(dmg_events)

                    # 吸血（技能效果 + 精灵增益）
                    drain_pct = 0.0
                    life_drain_effects = [
                        e for e in skill.effects
                        if getattr(e, 'kind', '') == 'special' and e.name == 'life_drain'
                    ]
                    for ld in life_drain_effects:
                        pct = ld.value / 100.0 if ld.value > 1 else ld.value
                        drain_pct = max(drain_pct, pct)
                    sprite_drain = user.effective_stat('life_drain')
                    if sprite_drain > 0:
                        drain_pct = max(drain_pct, sprite_drain * 0.1)
                    if drain_pct > 0:
                        healed = user.heal(round(damage * drain_pct))
                        if healed:
                            events.append(f'{user.name} 吸血{drain_pct*100:.0f}%+{healed}HP')

                # 技能效果
                effect_events = self._resolver.dispatch(
                    user, target, use, self.globals, ctx, team=team,
                )
                events.extend(effect_events)

        # 防御技能冷却（连击循环外）
        if skill.is_defense:
            skill.cooldown = 1

        # 脱离/折返 + 新效果（连击循环外）
        special_names = {getattr(e, 'name', '') for e in skill.effects if getattr(e, 'kind', '') == 'special'}

        if 'escape_inherit' in special_names:
            self._handle_escape_inherit(team, user, events)
        elif 'escape' in special_names:
            self._handle_escape(team, user, events)

        if 'force_return' in special_names:
            opp_team = 'B' if team == 'A' else 'A'
            events += self._resolve_return(opp_team)

        if 'borrow_skill' in special_names:
            self._handle_borrow_skill(team, user, action.skill_index or 0, events)

        return events

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

    # ═══════════════════════════════════════════════════════════════
    # Phase 4: 回合结束
    # ═══════════════════════════════════════════════════════════════

    def _phase_turn_end(self) -> list[str]:
        events: list[str] = []

        # 借用还原
        for (team, si), original in self._borrowed_restore.items():
            sprite = self.get_player(team).active
            if si < len(sprite.skills):
                bs = sprite.skills[si]
                borrowed_name = bs.name
                bs.replaced_by = None
                events.append(f'{sprite.name} 归还 {borrowed_name}')
        self._borrowed_restore.clear()

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
