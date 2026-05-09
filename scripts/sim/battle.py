"""scripts/sim/battle.py — 对局引擎

回合 = 开始阶段 → 选择阶段 → 结算阶段 → 结束阶段
"""

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .globals import GlobalEffects
from .resolver import SkillResolver, TurnContext
from .battleskill import SkillUse
from .action import Action

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


class Battle:
    """对局引擎。"""

    MAX_TURNS = 150

    def __init__(
        self, player_a: 'Player', player_b: 'Player',
        weather: str = '',
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
        record.action_a = str(action_a)
        record.action_b = str(action_b)
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

    # ── 换宠 ──

    def _resolve_switch(self, team: str, action: Action) -> list[str]:
        events: list[str] = []
        player = self.get_player(team)
        old = player.active

        if action.switch_index is None or action.switch_index >= len(player.team):
            return events

        player.active_index = action.switch_index
        new = player.active

        # 入场印记伤害
        opp_team = 'B' if team == 'A' else 'A'
        dmg = self.globals.mark_switch_damage(opp_team, new)
        if dmg:
            new.take_damage(dmg)
            events.append(f'{new.name} 棘刺-{dmg}HP')

        # 入场扣能
        lost = self.globals.mark_switch_energy_loss(opp_team)
        if lost:
            new.lose_energy(lost)
            events.append(f'{new.name} 降临-{lost}E')

        new.clear_effects('battlefield')
        new.entry_turn = self.turn
        new.first_action = True
        new.inc_counter('times_entered')
        events.append(f'{old.name}↓ {new.name}↑')

        # 力竭检查（进场的精灵可能被棘刺弹死）
        self._check_faint_interrupt(team, events)
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

        if countered:
            # 应对成功 → 双方同时（均视为"同时"，无先后之分）
            events += self._execute_single_action('A', action_a, is_countered=counter_a, is_first=True)
            self._check_faint_interrupt('A', events)
            self._check_faint_interrupt('B', events)
            if not self.is_finished:
                events += self._execute_single_action('B', action_b, is_countered=counter_b, is_first=True)
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

        ctx = TurnContext(turn=self.turn, is_first=is_first)

        use = SkillUse(
            battle_skill=skill,
            is_countered=is_countered,
            is_first=is_first,
        )

        # 攻击技能 → 伤害计算
        if skill.is_attack:
            damage, dmg_events = self._resolver.calc_damage(
                user, target, use, self.globals,
                attacker_team=team,
            )
            target.take_damage(damage)
            target.inc_counter('times_hit')
            user.inc_counter('times_dealt')
            events.append(f'{user.name} {skill.name} → {target.name} -{damage}HP')
            events.extend(dmg_events)

            # 吸血
            life_drain_effects = [
                e for e in skill.effects
                if getattr(e, 'kind', '') == 'special' and e.name == 'life_drain'
            ]
            for ld in life_drain_effects:
                pct = ld.value / 100.0 if ld.value > 1 else ld.value
                healed = user.heal(round(damage * pct))
                if healed:
                    events.append(f'{user.name} 吸血+{healed}HP')

        # 技能效果
        effect_events = self._resolver.dispatch(
            user, target, use, self.globals, ctx, team=team,
        )
        events.extend(effect_events)

        # 防御技能冷却
        if skill.is_defense:
            skill.cooldown = 1

        # 脱离/折返
        escape_effects = [
            e for e in skill.effects
            if getattr(e, 'kind', '') == 'special' and e.name == 'escape'
        ]
        if escape_effects:
            self._handle_escape(team, user, events)

        return events

    # ── 力竭中断 ──

    def _check_faint_interrupt(self, team: str, events: list[str]) -> None:
        """检查力竭并立即强制换宠。扣减魔力，无存活则判负。"""
        player = self.get_player(team)
        s = player.active
        if not s.is_fainted:
            return

        player.lives -= 1
        events.append(f'{s.name} 力竭({player.name} 魔力-1→{player.lives})')

        if player.lives <= 0:
            self.winner = 'B' if team == 'A' else 'A'
            events.append(f'{player.name} 魔力耗尽 → {self.get_opponent(team).name} 胜')
            return

        agent = self._get_agent(team)
        replacement = agent.choose_replacement(self)
        if replacement < 0:
            self.winner = 'B' if team == 'A' else 'A'
            events.append(f'{s.name} 力竭 → 无存活精灵 → {self.get_opponent(team).name} 胜')
            return

        old = s
        player.active_index = replacement
        new = player.active
        new.clear_effects('battlefield')
        new.entry_turn = self.turn
        new.first_action = True
        new.inc_counter('times_entered')
        events.append(f'{old.name} 力竭↓ {new.name}↑(本回合跳过)')

    # ── 道具 ──

    def _resolve_item(self, team: str) -> str:
        """使用道具，立即应用效果。返回道具名（用于记录）。"""
        from .sprite import StatusEffect

        player = self.get_player(team)
        item = player.item
        if not item or not item.can_use(self.turn):
            return ''

        item.use(self.turn)
        sprite = player.active

        if item.name == '进化之力':
            for key in ['atk', 'sp_atk', 'def', 'sp_def', 'speed']:
                sprite.add_effect(StatusEffect(
                    name='首领化', category='stat', stat_key=key, steps=2,
                    scope='permanent', source='进化之力',
                ))
            return '进化之力'

        elif item.name == '愿力强化':
            sprite.add_effect(StatusEffect(
                name='愿力强化', category='stat', stat_key='power', steps=5,
                scope='battlefield', source='愿力强化',
            ))
            return '愿力强化'

        return item.name

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

    def _handle_escape(self, team: str, user: 'Sprite', events: list[str]) -> None:
        """处理脱离/折返：agent 选择换宠。"""
        player = self.get_player(team)
        agent = self._get_agent(team)
        replacement = agent.choose_replacement(self)
        if replacement >= 0:
            player.active_index = replacement
            new_sprite = player.active
            new_sprite.inc_counter('times_entered')
            new_sprite.clear_effects('battlefield')
            new_sprite.entry_turn = self.turn
            new_sprite.first_action = True
            events.append(f'{user.name} 脱离→{new_sprite.name}')

    # ═══════════════════════════════════════════════════════════════
    # Phase 4: 回合结束
    # ═══════════════════════════════════════════════════════════════

    def _phase_turn_end(self) -> list[str]:
        events: list[str] = []

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

        # 回合循环
        while not self.is_finished:
            self.execute_turn(agent_a, agent_b)

        # 终局
        result = self.winner or 'draw'
        agent_a.on_game_end(result)
        agent_b.on_game_end(result)
        return result
