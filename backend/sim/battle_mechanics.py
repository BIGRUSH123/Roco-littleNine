"""backend/sim/battle_mechanics.py — 场地变动 Mixin

换宠、返场、脱离、借用、力竭中断 —— 精灵进出场的全部逻辑，
从 Battle 中提取为 Mixin，保持 Battle 的回合调度和动作执行精简。
"""

import random
from typing import TYPE_CHECKING

from backend.common.constants import ELEMENTAL_BLOODLINES

from .action import Action
from .traits import dispatch_entry, dispatch_leave

if TYPE_CHECKING:
    from .sprite import Sprite


class BattleMechanicsMixin:
    """场地变动：换宠 / 返场 / 脱离 / 借用 / 力竭 / 道具。

    作为 Mixin 混入 Battle，直接通过 self 访问：
      get_player, get_opponent, _get_agent,
      turn, winner, globals, is_finished, _borrowed_restore.
    """

    def _resolve_switch(self, team: str, action: Action,
                         faint_events: list[str] | None = None) -> list[str]:
        events: list[str] = []
        player = self.get_player(team)
        old = player.active

        if action.switch_index is None or action.switch_index >= len(player.team):
            return events

        # 防护：不能换到已力竭的精灵
        if player.team[action.switch_index].is_fainted:
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
        events.append(f'{old.name}↓ {new.name}↑')

        # ── trait hooks ──
        events += dispatch_entry(new, self, team)
        # 入场传动（首回合入场自动传动一次）
        events += self._apply_transmission(new)
        # team counter: enemy switch (搜刮 等 pre-entry accumulator)
        opp_active = self.get_opponent(team).active
        self.inc_team_counter(opp_team, 'enemy_switch')
        # Observer: post_leave + post_entry + post_enemy_leave
        # (dispatch_leave delayed until after fire_trigger so inherit ops
        #  can read effects from the departing sprite before they are cleared)
        ctx_leave = self._make_ctx(old, opp_active, None, None, self.globals, team=team, turn=self.turn, self_switched=True)
        ctx_entry = self._make_ctx(new, opp_active, None, None, self.globals, team=team, turn=self.turn)
        events += self._vm_engine.fire_trigger("post_leave", ctx_leave, old, opp_active, self.globals, team=team, battle=self)
        # 洁癖等 post_leave observer 可能写入新的 pending_effects，在 post_entry 前应用
        pending = self.pending_effects.get(team, [])
        for e in pending:
            new.add_effect(e)
        if pending:
            self.pending_effects[team] = []
        events += self._vm_engine.fire_trigger("post_entry", ctx_entry, new, opp_active, self.globals, team=team, battle=self)
        if not opp_active.is_fainted:
            ctx_enemy_leave = self._make_ctx(opp_active, new, None, None, self.globals, team=opp_team, turn=self.turn, opp_switched=True)
            events += self._vm_engine.fire_trigger("post_enemy_leave", ctx_enemy_leave, opp_active, new, self.globals, team=opp_team, battle=self, leaving_sprite=old)
        events += dispatch_leave(old, self, team)

        if faint_events is not None:
            self._check_faint_interrupt(team, faint_events)
        else:
            self._check_faint_interrupt(team, events)
        return events

    def _resolve_return(self, team: str) -> list[str]:
        """返场：清 battlefield 效果，重置进场标记。精灵不变。"""
        events: list[str] = []
        sprite = self.get_player(team).active
        if sprite.is_fainted:
            return events
        n = len([e for e in getattr(sprite, 'active_effects', []) if getattr(e, 'scope', '') == 'battlefield'])
        sprite.clear_effects('battlefield')
        sprite.entry_turn = self.turn
        sprite.first_action = True
        sprite.inc_counter('times_entered')
        events.append(f'{sprite.name} 返场(-{n}效果)')

        # ── trait entry hook ──
        events += dispatch_entry(sprite, self, team)
        events += self._apply_transmission(sprite)
        # Observer: post_entry
        opp = self.get_opponent(team).active
        ctx_ret = self._make_ctx(sprite, opp, None, None, self.globals, team=team, turn=self.turn)
        events += self._vm_engine.fire_trigger("post_entry", ctx_ret, sprite, opp, self.globals, team=team, battle=self)

        return events

    def _check_faint_interrupt(self, team: str, events: list[str]) -> None:
        """检查力竭并立即强制换宠。扣减魔力，无存活则判负。"""
        player = self.get_player(team)
        s = player.active
        if not s.is_fainted:
            return

        # 力竭发生后 _make_ctx 的 fainted 计数已过期，清缓存使其重算
        if hasattr(self, '_ctx_team_cache'):
            self._ctx_team_cache.clear()

        old = s

        agent = self._get_agent(team)
        replacement = agent.choose_replacement(self)
        if replacement < 0 or replacement >= len(player.team):
            # No bench: deduct life and check loss immediately
            player.lives -= 1
            events.append(f'{old.name} 力竭({player.name} 魔力-1→{player.lives})')
            self.winner = 'B' if team == 'A' else 'A'
            events.append(f'{old.name} 力竭 → 无存活精灵 → {self.get_opponent(team).name} 胜')
            return

        # 防护：若替补已力竭（agent bug 或并发），同样扣魔力
        if player.team[replacement].is_fainted:
            player.lives -= 1
            events.append(f'{old.name} 力竭({player.name} 魔力-1→{player.lives})')
            self.winner = 'B' if team == 'A' else 'A'
            events.append(f'{old.name} 力竭 → 替补已死 → {self.get_opponent(team).name} 胜')
            return

        player.active_index = replacement
        new = player.active
        new.clear_effects('battlefield')
        new.entry_turn = self.turn
        new.first_action = True
        new.inc_counter('times_entered')
        events.append(f'{old.name} 力竭↓ {new.name}↑')

        # ── trait hooks ──
        events += dispatch_entry(new, self, team)
        events += self._apply_transmission(new)
        # Observer: post_ko（先触发，让诈死/御驾亲征等修改 lives）→ 再扣默认1魔力
        opp_team = 'B' if team == 'A' else 'A'
        opp_active = self.get_opponent(team).active
        ctx_ko_leave = self._make_ctx(old, opp_active, None, None, self.globals, team=team, turn=self.turn, target_fainted=True, self_switched=True)
        events += self._vm_engine.fire_trigger("post_ko", ctx_ko_leave, old, opp_active, self.globals, team=team, battle=self)
        player.lives -= 1
        events.append(f'{old.name} 力竭({player.name} 魔力-1→{player.lives})')
        if player.lives <= 0:
            self.winner = 'B' if team == 'A' else 'A'
            events.append(f'{player.name} 魔力耗尽 → {self.get_opponent(team).name} 胜')
            return
        ctx_ko_entry = self._make_ctx(new, opp_active, None, None, self.globals, team=team, turn=self.turn)
        events += self._vm_engine.fire_trigger("post_leave", ctx_ko_leave, old, opp_active, self.globals, team=team, battle=self)
        # 洁癖等 post_leave observer 可能写入新的 pending_effects
        pending = self.pending_effects.get(team, [])
        for e in pending:
            new.add_effect(e)
        if pending:
            self.pending_effects[team] = []
        events += self._vm_engine.fire_trigger("post_entry", ctx_ko_entry, new, opp_active, self.globals, team=team, battle=self)
        if not opp_active.is_fainted:
            ctx_ko_enemy = self._make_ctx(opp_active, new, None, None, self.globals, team=opp_team, turn=self.turn, opp_switched=True)
            events += self._vm_engine.fire_trigger("post_enemy_leave", ctx_ko_enemy, opp_active, new, self.globals, team=opp_team, battle=self, leaving_sprite=old)
        events += dispatch_leave(old, self, team, is_faint=True)

    def _resolve_item(self, team: str) -> str:
        """使用道具，立即应用效果。返回道具名（用于记录）。"""
        player = self.get_player(team)
        item = player.item
        if not item or not item.can_use(self.turn):
            return ''

        sprite = player.active

        # 血脉限制
        if item.name == '进化之力' and sprite.bloodline != '首领':
            return ''
        if item.name == '愿力' and sprite.bloodline not in ELEMENTAL_BLOODLINES:
            return ''

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
            # 进化后重新加载特性（新形态有不同 ability）
            sprite.entry_turn = self.turn  # 让 sprite_entered 条件生效
            self._vm_engine.trait_loader.load_for_sprite(sprite)
            from backend.sim.traits.trait_engine import fire_hook
            fire_hook('post_entry', sprite, self, team)
            # 触发 Observer 系统的 post_entry（全神贯注等入场特性）
            opp = self.get_opponent(team).active
            ctx = self._make_ctx(sprite, opp, None, None, self.globals, team=team, turn=self.turn)
            self._vm_engine.fire_trigger("post_entry", ctx, sprite, opp, self.globals, team=team, battle=self)
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

    def _handle_escape(self, team: str, user: 'Sprite', events: list[str], urgent: bool = False) -> None:
        """处理脱离/折返。

        urgent=True:  紧急脱离，随机自动选择替补。
        urgent=False: 普通脱离，由 agent 选择替补（玩家可自选）。
        """
        player = self.get_player(team)
        if urgent:
            # 紧急脱离：随机选择场下存活精灵
            bench = [i for i in player.alive_sprites if i != player.active_index]
            replacement = random.choice(bench) if bench else -1
        else:
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
            # Observer: post_leave + post_entry
            opp_esc = self.get_opponent(team).active
            ctx_esc_leave = self._make_ctx(user, opp_esc, None, None, self.globals, team=team, turn=self.turn, self_switched=True)
            ctx_esc_entry = self._make_ctx(new_sprite, opp_esc, None, None, self.globals, team=team, turn=self.turn)
            events += self._vm_engine.fire_trigger("post_leave", ctx_esc_leave, user, opp_esc, self.globals, team=team, battle=self)
            # 洁癖等 post_leave observer 可能写入新的 pending_effects
            pending = self.pending_effects.get(team, [])
            for e in pending:
                new_sprite.add_effect(e)
            if pending:
                self.pending_effects[team] = []
            events += self._vm_engine.fire_trigger("post_entry", ctx_esc_entry, new_sprite, opp_esc, self.globals, team=team, battle=self)

    def _handle_escape_inherit(self, team: str, user: 'Sprite', events: list[str], urgent: bool = False) -> None:
        """脱离 + 下个入场精灵继承增益。

        urgent=True:  紧急脱离，随机自动选择替补。
        urgent=False: 普通脱离，由 agent 选择替补。
        """
        player = self.get_player(team)
        if urgent:
            bench = [i for i in player.alive_sprites if i != player.active_index]
            replacement = random.choice(bench) if bench else -1
        else:
            agent = self._get_agent(team)
            replacement = agent.choose_replacement(self)
        if replacement >= 0:
            old = player.active
            from backend.vm.effect import StatBuffEffect
            inherited = [e for e in old.active_effects if isinstance(e, StatBuffEffect) and e.steps > 0]
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
            # Observer: post_leave + post_entry
            opp_inh = self.get_opponent(team).active
            ctx_inh_leave = self._make_ctx(old, opp_inh, None, None, self.globals, team=team, turn=self.turn, self_switched=True)
            ctx_inh_entry = self._make_ctx(new_sprite, opp_inh, None, None, self.globals, team=team, turn=self.turn)
            events += self._vm_engine.fire_trigger("post_leave", ctx_inh_leave, old, opp_inh, self.globals, team=team, battle=self)
            # 洁癖等 post_leave observer 可能写入新的 pending_effects
            pending = self.pending_effects.get(team, [])
            for e in pending:
                new_sprite.add_effect(e)
            if pending:
                self.pending_effects[team] = []
            events += self._vm_engine.fire_trigger("post_entry", ctx_inh_entry, new_sprite, opp_inh, self.globals, team=team, battle=self)

    def _resolve_pending_escape_if_urgent(self, events: list[str]) -> bool:
        """If pending escape is urgent, resolve immediately (random choice).
        Returns True if escape was resolved.
        """
        pe = self.pending_escape
        if not pe or not pe.get("urgent"):
            return False

        self.pending_escape = None
        team = pe["team"]
        user_name = pe.get("user_name", "")

        player = self.get_player(team)
        user = player.active
        # Verify the escaping sprite is still active (no other switch happened)
        if user.name != user_name:
            return False

        if pe.get("inherit"):
            self._handle_escape_inherit(team, user, events, urgent=True)
        else:
            self._handle_escape(team, user, events, urgent=True)
        return True

    def resolve_escape(self, team: str, switch_index: int) -> list[str]:
        """Resolve a pending non-urgent escape with the player's chosen bench index."""
        events: list[str] = []
        pe = self.pending_escape
        if not pe:
            return events

        self.pending_escape = None
        player = self.get_player(team)
        user = player.active  # sprite that is escaping

        if switch_index < 0 or switch_index >= len(player.team):
            return events
        if switch_index == player.active_index:
            return events  # can't switch to self
        if player.team[switch_index].is_fainted:
            return events

        player.active_index = switch_index
        new_sprite = player.active
        new_sprite.inc_counter('times_entered')
        new_sprite.clear_effects('battlefield')
        new_sprite.entry_turn = self.turn
        new_sprite.first_action = True

        inherit = pe.get("inherit", False)
        if inherit:
            from backend.vm.effect import StatBuffEffect
            inherited = [e for e in user.active_effects
                         if isinstance(e, StatBuffEffect) and e.steps > 0]
            for e in inherited:
                new_sprite.add_effect(e)
            events.append(f'{user.name} 脱离→{new_sprite.name}(继承{len(inherited)}增益)')
        else:
            events.append(f'{user.name} 脱离→{new_sprite.name}')

        # ── trait hooks ──
        events += dispatch_leave(user, self, team)
        events += dispatch_entry(new_sprite, self, team)
        # Observer: post_leave + post_entry
        opp = self.get_opponent(team).active
        ctx_leave = self._make_ctx(user, opp, None, None, self.globals,
                                   team=team, turn=self.turn, self_switched=True)
        ctx_entry = self._make_ctx(new_sprite, opp, None, None, self.globals,
                                   team=team, turn=self.turn)
        events += self._vm_engine.fire_trigger("post_leave", ctx_leave, user, opp,
                                               self.globals, team=team, battle=self)
        pending = self.pending_effects.get(team, [])
        for e in pending:
            new_sprite.add_effect(e)
        if pending:
            self.pending_effects[team] = []
        events += self._vm_engine.fire_trigger("post_entry", ctx_entry, new_sprite, opp,
                                               self.globals, team=team, battle=self)

        return events

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

        传动X: 传动 >= 当前 pass 的参与本次移动。_transmission=-1 为主轴（不参与不阻挡）。
        返回事件列表。"""
        skills = sprite.skills
        n = len(skills)
        if n < 2:
            return []

        events: list[str] = []
        max_lv = max((getattr(bs, '_transmission', 0) for bs in skills), default=0)
        if max_lv <= 0:
            return events

        for pass_num in range(max_lv):
            # Snapshot pre-pass positions (id→index) for move tracking
            pre_pos: dict[int, int] = {id(bs): i for i, bs in enumerate(skills)}

            # ── 第一步：提取非主轴技能组成虚拟数组 ──
            # 主轴不参与传动，也不阻挡——如同不存在
            active_map: list[int] = []  # virtual_index → original_index
            active: list = []           # virtual skills
            for i, bs in enumerate(skills):
                if getattr(bs, '_transmission', 0) != -1:
                    active_map.append(i)
                    active.append(bs)
            m = len(active)
            if m < 2:
                continue

            # ── 第二步：在虚拟数组上收集传动块 ──
            blocks: list[tuple[int, int]] = []  # (start, end) in virtual indices

            i = 0
            while i < m:
                trans_lv = getattr(active[i], '_transmission', 0)
                if trans_lv <= pass_num:
                    i += 1
                    continue

                block_start = i
                block_end = i
                while block_end + 1 < m:
                    next_lv = getattr(active[block_end + 1], '_transmission', 0)
                    if next_lv > pass_num:
                        block_end += 1
                    else:
                        break

                blocks.append((block_start, block_end))
                i = block_end + 1

            # ── 第三步：合并虚拟数组的循环边界块 ──
            if len(blocks) >= 2:
                first_start, first_end = blocks[0]
                last_start, last_end = blocks[-1]
                if first_start == 0 and last_end == m - 1:
                    blocks[0] = (last_start, first_end)
                    blocks.pop()

            # ── 第四步：应用每个块的旋转到虚拟数组 ──
            if blocks:
                temp = list(active)
                for block_start, block_end in blocks:
                    displaced_idx = (block_end + 1) % m
                    # 主轴已被排除，displaced 不会是主轴，无需屏障检查

                    if block_start <= block_end:
                        for pos in range(block_start, block_end + 1):
                            active[(pos + 1) % m] = temp[pos]
                    else:
                        # 循环块：[block_start, m-1] + [0, block_end]
                        for pos in range(block_start, m):
                            active[(pos + 1) % m] = temp[pos]
                        for pos in range(0, block_end + 1):
                            active[(pos + 1) % m] = temp[pos]
                    # 当块覆盖整个虚拟数组时，无外部被挤出元素，跳过
                    if displaced_idx != block_start:
                        active[block_start] = temp[displaced_idx]

            # ── 第五步：映射回原始 skills 数组 ──
            for vi, oi in enumerate(active_map):
                skills[oi] = active[vi]

            # ── 第六步：统计本 pass 位置变化（机械变式）──
            for i, bs in enumerate(skills):
                if pre_pos.get(id(bs), -1) != i:
                    prev_count = getattr(bs, '_transmission_move_count', 0)
                    bs._transmission_move_count = prev_count + 1

            names = '/'.join(bs.name for bs in skills)
            events.append(f'{sprite.name} 传动→ {names}')

        return events
