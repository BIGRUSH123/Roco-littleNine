"""backend/sim/battle_mechanics.py — 场地变动 Mixin

换宠、返场、脱离、借用、力竭中断 —— 精灵进出场的全部逻辑，
从 Battle 中提取为 Mixin，保持 Battle 的回合调度和动作执行精简。
"""

import random
from typing import TYPE_CHECKING

from .action import Action
from .traits import dispatch_enemy_leave, dispatch_entry, dispatch_faint, dispatch_leave

if TYPE_CHECKING:
    from .sprite import Sprite


class BattleMechanicsMixin:
    """场地变动：换宠 / 返场 / 脱离 / 借用 / 力竭 / 道具。

    作为 Mixin 混入 Battle，直接通过 self 访问：
      get_player, get_opponent, _get_agent,
      turn, winner, globals, is_finished, _borrowed_restore.
    """

    def _resolve_switch(self, team: str, action: Action) -> list[str]:
        events: list[str] = []
        player = self.get_player(team)
        old = player.active

        if action.switch_index is None or action.switch_index >= len(player.team):
            return events

        # 换宠打断蓄力
        if getattr(old, '_charging', False):
            old._charging = False
            old._charged_skill_index = -1
            events.append(f'{old.name} 蓄力中断（换宠）')

        player.active_index = action.switch_index
        new = player.active

        opp_team = 'B' if team == 'A' else 'A'
        dmg = self.globals.mark_switch_damage(opp_team, new)
        if dmg:
            new.take_damage(dmg)
            events.append(f'{new.name} 棘刺-{dmg}HP')

        lost = self.globals.mark_switch_energy_loss(opp_team)
        if lost:
            new.lose_energy(lost)
            events.append(f'{new.name} 降临-{lost}E')

        new.clear_effects('battlefield')
        new.entry_turn = self.turn
        new.first_action = True
        new.inc_counter('times_entered')
        events.append(f'>>>ACTION:{old.name}:换宠→{new.name}')
        events.append(f'{old.name}↓ {new.name}↑')
        events.append('<<<ACTION')

        # ── trait hooks ──
        events += dispatch_leave(old, self, team)
        events += dispatch_entry(new, self, team)
        # 入场传动（首回合入场自动传动一次）
        events += self._apply_transmission(new)
        # 通知敌方：观测到对手换宠（做噩梦/下黑手/珊瑚骨）
        opp_active = self.get_opponent(team).active
        if not opp_active.is_fainted:
            events += dispatch_enemy_leave(opp_active, old, new, self, opp_team)
        # team counter: enemy switch (搜刮 等 pre-entry accumulator)
        self.inc_team_counter(opp_team, 'enemy_switch')

        self._check_faint_interrupt(team, events)
        return events

    def _resolve_return(self, team: str) -> list[str]:
        """返场：清 battlefield 效果，重置进场标记。精灵不变。"""
        events: list[str] = []
        sprite = self.get_player(team).active
        if sprite.is_fainted:
            return events
        n = len([e for e in sprite.effects if e.scope == 'battlefield'])
        sprite.clear_effects('battlefield')
        sprite.first_action = True
        sprite.inc_counter('times_entered')
        events.append(f'{sprite.name} 返场(-{n}效果)')

        # ── trait entry hook ──
        events += dispatch_entry(sprite, self, team)
        events += self._apply_transmission(sprite)

        return events

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

        # ── trait hooks ──
        events += dispatch_leave(old, self, team, is_faint=True)
        events += dispatch_faint(old, None, self, team)
        events += dispatch_entry(new, self, team)
        events += self._apply_transmission(new)
        # 通知敌方：观测到对手力竭换宠（做噩梦/下黑手/珊瑚骨）
        opp_team = 'B' if team == 'A' else 'A'
        opp_active = self.get_opponent(team).active
        if not opp_active.is_fainted:
            events += dispatch_enemy_leave(opp_active, old, new, self, opp_team)

    def _resolve_item(self, team: str) -> str:
        """使用道具，立即应用效果。返回道具名（用于记录）。"""
        from .sprite import StatusEffect

        player = self.get_player(team)
        item = player.item
        if not item or not item.can_use(self.turn):
            return ''

        sprite = player.active

        if item.name == '进化之力':
            # 进化之力：同编号有首领形态的精灵可进化为首领形态
            if self.species_db is None:
                return ''
            boss_species = self._find_leader_form(sprite.species.number)
            if boss_species is None:
                return ''
            item.use(self.turn)
            # 用首领形态的种族值 + 原IV/性格重新计算六维
            from backend.common.formulas import StatsCalc
            calc = StatsCalc()
            result = calc.compute(
                boss_species,
                nature=sprite.nature,
                iv=sprite.iv,
            )
            hp_ratio = sprite.current_hp / max(1, sprite.max_hp)
            sprite.species = boss_species
            sprite.initial_stats = dict(result.final_stats)
            sprite.max_hp = result.final_stats['hp']
            sprite.current_hp = max(1, round(result.final_stats['hp'] * hp_ratio))
            sprite.bloodline_skills = dict(boss_species.bloodline_skills)
            sprite.first_action = True
            # 萌化状态在形态变化后失效
            sprite._reset_moe_state()
            sprite.remove_effect('萌化', 'abnormal')
            for key in ['atk', 'sp_atk', 'def', 'sp_def', 'speed']:
                sprite.add_effect(StatusEffect(
                    name='首领化', category='stat', stat_key=key, steps=2,
                    scope='permanent', source='进化之力',
                ))
            return '进化之力'

        elif item.name == '愿力':
            # 愿力：用血脉对应的血脉技能替换一技能（技能槽0），持续一回合
            bl_element = sprite.bloodline
            bl_skill_id = sprite.bloodline_skills.get(bl_element)
            if bl_skill_id is None:
                return ''
            if self.skill_loader is None:
                return ''
            item.use(self.turn)
            new_skill_name = self._get_skill_name_by_id(bl_skill_id)
            if new_skill_name:
                new_skills = self.skill_loader([new_skill_name])
                if new_skills and len(sprite.skills) > 0:
                    old_name = sprite.skills[0].name
                    # 存入还原队列，回合结束时还原
                    self._wish_restore[(team, 0)] = sprite.skills[0]
                    sprite.skills[0] = new_skills[0]
                    return f'愿力({old_name}→{new_skills[0].name})'
            return '愿力'

        return item.name

    def _find_leader_form(self, number: str):
        """查找同编号的首领形态。"""
        if self.species_db is None or not number:
            return None
        for p in self.species_db._by_number.get(number, []):
            s = self.species_db._read_one(p)
            if s and '首领' in s.form:
                return s
        return None

    def _get_skill_name_by_id(self, skill_id: int) -> str | None:
        """按技能ID反查名称。"""
        try:
            from backend.common.skill_trait_ids import SKILL_ID_TO_NAME
            return SKILL_ID_TO_NAME.get(skill_id)
        except ImportError:
            return None

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

            # ── trait hooks ──
            events += dispatch_leave(user, self, team)
            events += dispatch_entry(new_sprite, self, team)

    def _handle_escape_inherit(self, team: str, user: 'Sprite', events: list[str]) -> None:
        """脱离 + 下个入场精灵继承增益。"""
        player = self.get_player(team)
        agent = self._get_agent(team)
        replacement = agent.choose_replacement(self)
        if replacement >= 0:
            old = player.active
            inherited = [e for e in old.effects if e.is_stat and e.steps > 0]
            player.active_index = replacement
            new_sprite = player.active
            new_sprite.clear_effects('battlefield')
            new_sprite.entry_turn = self.turn
            new_sprite.first_action = True
            new_sprite.inc_counter('times_entered')
            for e in inherited:
                new_sprite.add_effect(e)
            events.append(f'{user.name} 脱离→{new_sprite.name}(继承{len(inherited)}增益)')

            # ── trait hooks ──
            events += dispatch_leave(old, self, team)
            events += dispatch_entry(new_sprite, self, team)

    def _handle_borrow_skill(self, team: str, user: 'Sprite', skill_index: int, events: list[str]) -> None:
        """借用：从替补精灵随机借用一个技能替换当前技能槽，回合结束时还原。"""
        player = self.get_player(team)
        bench = [s for i, s in enumerate(player.team) if i != player.active_index and not s.is_fainted]
        if not bench:
            events.append(f'{user.name} 借用失败(无替补)')
            return
        donor = random.choice(bench)
        if not donor.skills:
            events.append(f'{user.name} 借用失败({donor.name}无技能)')
            return
        borrowed = random.choice(donor.skills)
        if skill_index < len(user.skills):
            bs = user.skills[skill_index]
            self._borrowed_restore[(team, skill_index)] = bs.base
            bs.replaced_by = borrowed.base
            events.append(f'{user.name} 借用 {donor.name} 的 {borrowed.name}')

    # ═══════════════════════════════════════════════════════════════
    # 传动系统
    # ═══════════════════════════════════════════════════════════════

    def _apply_transmission(self, sprite: 'Sprite') -> list[str]:
        """执行一次传动 pass：传动技能向下移动一个槽位，相邻传动合成块。

        传动X: 传动 >= 当前 pass 的参与本次移动。
        返回事件列表。"""
        skills = sprite.skills
        n = len(skills)
        if n < 2:
            return []

        events: list[str] = []
        max_lv = max((getattr(bs, '_transmission', 0) for bs in skills), default=0)
        if max_lv <= 0:
            return events

        # 多 pass：先所有传动一起移动（pass 0），再仅传动2 单独移动（pass 1），依此类推
        for pass_num in range(max_lv):
            moved: set[int] = set()
            moves: list[tuple[int, int]] = []  # (old_pos, new_pos)

            i = 0
            while i < n:
                bs = skills[i]
                trans_lv = getattr(bs, '_transmission', 0)
                main_axis = getattr(bs, '_main_axis', False)
                if trans_lv <= pass_num or main_axis:
                    i += 1
                    continue

                # 找到传动块 [block_start, block_end]
                block_start = i
                block_end = i
                while block_end + 1 < n:
                    next_bs = skills[block_end + 1]
                    next_lv = getattr(next_bs, '_transmission', 0)
                    next_axis = getattr(next_bs, '_main_axis', False)
                    if next_lv > pass_num and not next_axis:
                        block_end += 1
                    else:
                        break

                # 被顶替的技能位置（块下端 + 1，四号位下行到一号位）
                displaced_idx = (block_end + 1) % n
                displaced = skills[displaced_idx]
                if getattr(displaced, '_main_axis', False):
                    # 主轴技能不参与：跳过整个块
                    i = block_end + 1
                    continue

                # 块内每个技能下移一个位置
                for pos in range(block_start, block_end + 1):
                    moves.append((pos, (pos + 1) % n))
                    moved.add(pos)

                # 被顶替的技能移到块顶端
                moves.append((displaced_idx, block_start))
                moved.add(displaced_idx)

                i = block_end + 1

            # 应用移动
            if moves:
                temp = list(skills)
                for old_pos, new_pos in moves:
                    skills[new_pos] = temp[old_pos]
                names = '/'.join(bs.name for bs in skills)
                events.append(f'{sprite.name} 传动→ {names}')

        return events
