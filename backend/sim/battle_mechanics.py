"""backend/sim/battle_mechanics.py — 场地变动 Mixin

换宠、返场、脱离、借用、力竭中断 —— 精灵进出场的全部逻辑，
从 Battle 中提取为 Mixin，保持 Battle 的回合调度和动作执行精简。
"""

import random
from typing import TYPE_CHECKING

from backend.common.constants import ELEMENTAL_BLOODLINES, ITEM_VARIANT_SLOTS

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

    def _apply_pending_entry_effects(self, team: str, sprite: 'Sprite') -> None:
        pending = self.pending_effects.get(team, [])
        for e in pending:
            sprite.add_effect(e)
        if pending:
            self.pending_effects[team] = []

    def _fire_post_entry(self, team: str, sprite: 'Sprite', opp: 'Sprite') -> list[str]:
        ctx_entry = self._make_ctx(sprite, opp, None, None, self.globals, team=team, turn=self.turn)
        return self._vm_engine.fire_trigger(
            "post_entry", ctx_entry, sprite, opp, self.globals, team=team, battle=self
        )

    def _reapply_position_modifiers(self, trigger: str, team: str, sprite: 'Sprite', opp: 'Sprite') -> list[str]:
        ctx = self._make_ctx(sprite, opp, None, None, self.globals, team=team, turn=self.turn)
        return self._vm_engine.reapply_position_modifiers(
            trigger, ctx, sprite, opp, self.globals, team=team, battle=self
        )

    def _fire_skill_position_changed(self, team: str, sprite: 'Sprite', moved_skill) -> list[str]:
        opp = self.get_opponent(team).active
        ctx = self._make_ctx(
            sprite, opp, moved_skill, None, self.globals,
            team=team, turn=self.turn,
            skill_position_changed=True,
            battle_skill=moved_skill,
        )
        return self._vm_engine.fire_skill_position_changed(
            ctx, sprite, opp, self.globals,
            team=team, self_skill=moved_skill, battle=self,
        )

    def _apply_entry_transmission(self, team: str, sprite: 'Sprite', opp: 'Sprite') -> list[str]:
        events = self._apply_transmission(sprite, team=team)
        if events or getattr(self, '_last_transmission_changed', False):
            events += self._reapply_position_modifiers("post_entry", team, sprite, opp)
        return events

    def _apply_surge_leave_debuffs(self, team: str, new: 'Sprite', events: list[str], mcts_sim: bool) -> None:
        """暗涌印记：持有方精灵离场后，更换入场的精灵获得随机5层属性减益/层。

        "随机"以确定性轮转近似（按回合数偏移在五维间轮转），保证回放可复现。
        """
        total = self.globals.mark_leave_random_debuffs(team)
        if not total or new.is_fainted:
            return
        from backend.vm.effect import StatBuffEffect
        rotation = ('atk', 'def', 'sp_atk', 'sp_def', 'speed')
        for i in range(total):
            stat = rotation[(self.turn + i) % 5]
            new.add_effect(StatBuffEffect(
                name=f'暗涌·{stat}↓', source='暗涌印记',
                stat_key=stat, steps=-1, scope='battlefield',
            ))
        if not mcts_sim:
            events.append(f'{new.name} 暗涌-{total}层随机减益')

    def _refresh_mechanisms(self) -> None:
        """重算引擎机制声明（aura 等）——换人/入场/离场后调用（幂等，内部有快路径）。"""
        from backend.engine import mechanisms
        mechanisms.refresh(self)

    def _resolve_switch(self, team: str, action: Action,
                         faint_events: list[str] | None = None) -> list[str]:
        mcts_sim = getattr(self, '_mcts_sim', False)
        events: list[str] = []
        player = self.get_player(team)
        old = player.active

        # 禁足：无法离场（游戏内文本 3023）。主动换人被拒时该行动作废。
        if getattr(old, 'locked_turns', 0) > 0:
            if not mcts_sim:
                events.append(f'{old.name} 被禁足，无法离场')
            return events

        if action.switch_index is None or action.switch_index >= len(player.team):
            return events

        # 防护：不能换到已力竭的精灵
        if player.team[action.switch_index].is_fainted:
            return events

        # 换宠打断蓄力
        if getattr(old, '_charging', False):
            self._clear_charge_target(old)
            if not mcts_sim:
                events.append(f'{old.name} 蓄力中断（换宠）')

        player.active_index = action.switch_index
        self._invalidate_ctx_team_cache()
        new = player.active

        # 瞳中倒影：离场者持有该特性时，与换入者交换血量百分比
        try:
            from .traits import get_trait as _get_trait_swap
            _old_trait = _get_trait_swap(old)
            if _old_trait is not None and _old_trait.name == "瞳中倒影" \
                    and not old.is_fainted and not new.is_fainted:
                _r1 = old.current_hp / max(1, old.max_hp)
                _r2 = new.current_hp / max(1, new.max_hp)
                old.current_hp = max(1, round(old.max_hp * _r2))
                new.current_hp = max(1, round(new.max_hp * _r1))
                if not mcts_sim:
                    events.append(f'{old.name} 与 {new.name} 交换了血量比例')
        except Exception:
            pass

        opp_team = 'B' if team == 'A' else 'A'
        # 印记归属：mark_effects[X] 上的「离场」系印记，作用于 X 方自己的换入者
        # （棘刺/降灵/暗涌同族；此前棘刺/降灵读 opp_team → 反噬施放者）
        dmg = self.globals.mark_switch_damage(team, new)
        if dmg:
            new.take_damage(dmg)
            if not mcts_sim:
                events.append(f'{new.name} 棘刺-{dmg}HP')

        lost = self.globals.mark_switch_energy_loss(team)
        if lost:
            new.lose_energy(lost)
            if not mcts_sim:
                events.append(f'{new.name} 降灵-{lost}E')

        new.clear_effects('battlefield')
        new.entry_turn = self.turn
        new.first_action = True
        new.inc_counter('times_entered')
        # 暗涌印记：本队精灵离场，换入者承受随机属性减益
        self._apply_surge_leave_debuffs(team, new, events, mcts_sim)
        if not mcts_sim:
            events.append(f'{old.name}↓ {new.name}↑')

        # ── trait hooks ──
        entry_events = dispatch_entry(new, self, team)
        if not mcts_sim:
            events += entry_events
        # team counter: enemy switch (搜刮 等 pre-entry accumulator)
        opp_active = self.get_opponent(team).active
        self.inc_team_counter(opp_team, 'enemy_switch')
        self.inc_team_counter(opp_team, 'enemy_action')
        # Observer: post_leave + post_entry + post_enemy_leave
        # (dispatch_leave delayed until after fire_trigger so inherit ops
        #  can read effects from the departing sprite before they are cleared)
        ctx_leave = self._make_ctx(old, opp_active, None, None, self.globals, team=team, turn=self.turn, self_switched=True)
        post_leave_events = self._vm_engine.fire_trigger("post_leave", ctx_leave, old, opp_active, self.globals, team=team, battle=self)
        if not mcts_sim:
            events += post_leave_events
        # 洁癖等 post_leave observer 可能写入新的 pending_effects，在 post_entry 前应用
        self._apply_pending_entry_effects(team, new)
        post_entry_events = self._fire_post_entry(team, new, opp_active)
        if not mcts_sim:
            events += post_entry_events
        # 入场传动：位置型特性先让当前槽位参与传动，传动后再投影回当前槽位。
        transmission_events = self._apply_entry_transmission(team, new, opp_active)
        if not mcts_sim:
            events += transmission_events
        if not opp_active.is_fainted:
            ctx_enemy_leave = self._make_ctx(opp_active, new, None, None, self.globals, team=opp_team, turn=self.turn, opp_switched=True)
            post_enemy_leave_events = self._vm_engine.fire_trigger("post_enemy_leave", ctx_enemy_leave, opp_active, new, self.globals, team=opp_team, battle=self, leaving_sprite=old)
            if not mcts_sim:
                events += post_enemy_leave_events
        leave_events = dispatch_leave(old, self, team)
        if not mcts_sim:
            events += leave_events

        if faint_events is not None:
            self._check_faint_interrupt(team, faint_events)
        else:
            self._check_faint_interrupt(team, events)
        self._refresh_mechanisms()
        return events

    def _resolve_return(self, team: str) -> list[str]:
        """返场：清 battlefield 效果，重置进场标记。精灵不变。"""
        mcts_sim = getattr(self, '_mcts_sim', False)
        events: list[str] = []
        sprite = self.get_player(team).active
        if sprite.is_fainted:
            return events
        n = len([e for e in getattr(sprite, 'active_effects', []) if getattr(e, 'scope', '') == 'battlefield'])
        sprite.clear_effects('battlefield')
        sprite.entry_turn = self.turn
        sprite.first_action = True
        sprite.inc_counter('times_entered')
        if not mcts_sim:
            events.append(f'{sprite.name} 返场(-{n}效果)')

        # ── trait entry hook ──
        entry_events = dispatch_entry(sprite, self, team)
        if not mcts_sim:
            events += entry_events
        # Observer: post_entry
        opp = self.get_opponent(team).active
        post_entry_events = self._fire_post_entry(team, sprite, opp)
        if not mcts_sim:
            events += post_entry_events
        transmission_events = self._apply_entry_transmission(team, sprite, opp)
        if not mcts_sim:
            events += transmission_events

        self._refresh_mechanisms()
        return events

    def _check_faint_interrupt(self, team: str, events: list[str]) -> None:
        """检查力竭并立即强制换宠。扣减魔力，无存活则判负。"""
        player = self.get_player(team)
        s = player.active
        if not s.is_fainted:
            return
        mcts_sim = getattr(self, '_mcts_sim', False)

        # 力竭发生后 _make_ctx 的 fainted 计数已过期，清缓存使其重算
        self._invalidate_ctx_team_cache()

        old = s

        agent = self._get_agent(team)
        replacement = agent.choose_replacement(self)
        if replacement < 0 or replacement >= len(player.team):
            # No bench: deduct life and check loss immediately
            player.lives -= 1
            if not mcts_sim:
                events.append(f'{old.name} 力竭({player.name} 魔力-1→{player.lives})')
            self.winner = 'B' if team == 'A' else 'A'
            if not mcts_sim:
                events.append(f'{old.name} 力竭 → 无存活精灵 → {self.get_opponent(team).name} 胜')
            return

        # 防护：若替补已力竭（agent bug 或并发），同样扣魔力
        if player.team[replacement].is_fainted:
            player.lives -= 1
            if not mcts_sim:
                events.append(f'{old.name} 力竭({player.name} 魔力-1→{player.lives})')
            self.winner = 'B' if team == 'A' else 'A'
            if not mcts_sim:
                events.append(f'{old.name} 力竭 → 替补已死 → {self.get_opponent(team).name} 胜')
            return

        player.active_index = replacement
        self._invalidate_ctx_team_cache()
        new = player.active
        new.clear_effects('battlefield')
        new.entry_turn = self.turn
        new.first_action = True
        new.inc_counter('times_entered')
        if not mcts_sim:
            events.append(f'{old.name} 力竭↓ {new.name}↑')

        # ── 印记入场效果 ──
        # 游戏描述 3009 把「离场」限定为「主动更换精灵或触发脱离效果」，明确排除
        # 力竭下场，因此棘刺/降灵/暗涌这三条「离场」系印记在力竭换人时不触发。
        # （此前与主动换人对齐，等于凭空多打一次）
        opp_team = 'B' if team == 'A' else 'A'
        self.inc_team_counter(opp_team, 'enemy_action')

        # ── trait hooks ──
        entry_events = dispatch_entry(new, self, team)
        if not mcts_sim:
            events += entry_events
        # Observer: post_ko（先触发，让诈死/御驾亲征等修改 lives）→ 再扣默认1魔力
        # 这个 ctx 的 self 是**刚力竭的那只**，语义应是 `self_koed`；`target_fainted` 留
        # False（它的 target 是对面在场精灵，并没有力竭）。凶手视角的观察者由
        # `Ctx.swapped_view()` 负责对调标志——这里若置 target_fainted=True，
        # `on_ko`（=target_fainted）与 `on_self_ko` 会同时成立，一次力竭多扣两份魔力。
        opp_active = self.get_opponent(team).active
        # self_switched=False：游戏描述 3009 把「离场」限定为「主动更换精灵或触发脱离
        # 效果」，力竭下场不算；自己力竭走 post_ko（on_self_ko），凶手视角由伤害日志
        # 那条 post_ko 路径服务。
        ctx_ko_leave = self._make_ctx(old, opp_active, None, None, self.globals, team=team, turn=self.turn, self_switched=False)
        post_ko_events = self._vm_engine.fire_trigger("post_ko", ctx_ko_leave, old, opp_active, self.globals, team=team, battle=self, ko_side="self")
        if not mcts_sim:
            events += post_ko_events
        player.lives -= 1
        if not mcts_sim:
            events.append(f'{old.name} 力竭({player.name} 魔力-1→{player.lives})')
        if player.lives <= 0:
            self.winner = 'B' if team == 'A' else 'A'
            if not mcts_sim:
                events.append(f'{player.name} 魔力耗尽 → {self.get_opponent(team).name} 胜')
            return
        ctx_ko_entry = self._make_ctx(new, opp_active, None, None, self.globals, team=team, turn=self.turn)
        # 力竭不发 post_leave / post_enemy_leave（3009）；但 pending_effects 里可能已有
        # 别的路径写入的效果，仍按入场流程应用。
        self._apply_pending_entry_effects(team, new)
        post_entry_events = self._vm_engine.fire_trigger("post_entry", ctx_ko_entry, new, opp_active, self.globals, team=team, battle=self)
        if not mcts_sim:
            events += post_entry_events
        transmission_events = self._apply_entry_transmission(team, new, opp_active)
        if not mcts_sim:
            events += transmission_events
        leave_events = dispatch_leave(old, self, team, is_faint=True)
        if not mcts_sim:
            events += leave_events
        self._refresh_mechanisms()

    def item_variants(self, team: str) -> list:
        """道具可选的「首领形态」候选列表（空 = 无可选项或道具不可用）。

        动作空间约定：愿力用动作 16；进化之力用动作 17-21 —— 每个候选形态
        一个槽位（玩家选择首领化的目标形态，如圣光/圣水/圣火/圣草迪莫）。
        列表顺序由 SpriteDB.leader_form_candidates 固定（同外观 → 默认外观 →
        其余按名），保证动作索引在整局内稳定。
        """
        player = self.get_player(team)
        item = player.item
        if not item or not item.can_use(self.turn) or item.name != '进化之力':
            return []
        sprite = player.active
        if sprite is None or self.species_db is None:
            return []
        if sprite.bloodline != '首领' or sprite.species.is_leader_stage():
            return []
        return self.species_db.leader_form_candidates(
            sprite.species.number, sprite.species.appearance)[:ITEM_VARIANT_SLOTS]

    def item_usable(self, team: str) -> bool:
        """当前场上精灵能否使用队伍道具（供动作掩码与结算共用同一判定）。"""
        player = self.get_player(team)
        item = player.item
        if not item or not item.can_use(self.turn):
            return False
        sprite = player.active
        if sprite is None:
            return False
        if item.name == '进化之力':
            return bool(self.item_variants(team))
        if item.name == '愿力':
            return sprite.bloodline in ELEMENTAL_BLOODLINES
        return True

    def _resolve_item(self, team: str, variant: int | None = None) -> str:
        """使用道具，立即应用效果。返回道具名（用于记录）。

        variant: 进化之力的首领形态槽位（动作 17-21 → 0-4）。缺省或越界时取
        候选列表首位（兼容 API/旧调用点，行为与加入候选列表之前一致）。
        """
        player = self.get_player(team)
        item = player.item
        if not item or not item.can_use(self.turn):
            return ''

        sprite = player.active

        if not self.item_usable(team):
            return ''

        if item.name == '进化之力':
            # 进化之力：同编号有首领形态的精灵可进化为首领形态（形态由玩家选）
            candidates = self.item_variants(team)
            if not candidates:
                return ''
            idx = variant if variant is not None and 0 <= variant < len(candidates) else 0
            boss_species = candidates[idx]
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
            self._invalidate_ctx_team_cache()
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

    def _find_leader_form(self, number: str, appearance: str = ''):
        """查找同编号的首个首领形态（优先同外观，回退默认外观）。

        首领阶段 = form 含『首领』；外观由 appearance 字段独立标记。
        多候选场景请用 SpriteDB.leader_form_candidates / item_variants。
        """
        if self.species_db is None or not number:
            return None
        candidates = self.species_db.leader_form_candidates(number, appearance)
        return candidates[0] if candidates else None

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
        # 禁足：无法离场——脱离类效果同样被拦（原文 3023「精灵无法离场」）
        if getattr(player.active, 'locked_turns', 0) > 0:
            events.append(f'{player.active.name} 被禁足，无法脱离')
            return
        if urgent:
            # 紧急脱离：随机选择场下存活精灵
            bench = [i for i in player.alive_sprites if i != player.active_index]
            replacement = random.choice(bench) if bench else -1
        else:
            agent = self._get_agent(team)
            replacement = agent.choose_replacement(self)
        if replacement >= 0:
            player.active_index = replacement
            self._invalidate_ctx_team_cache()
            new_sprite = player.active
            new_sprite.inc_counter('times_entered')
            new_sprite.clear_effects('battlefield')
            new_sprite.entry_turn = self.turn
            new_sprite.first_action = True
            events.append(f'{user.name} 脱离→{new_sprite.name}')

            # ── trait hooks ──
            # dispatch_leave 延后到 post_leave/post_entry 之后（与 _resolve_switch 同序）：
            # 否则离场精灵的 post_leave 观察者已被卸载，木桶戏法/洁癖 这类
            # 「离场时写入 pending_effects」的特性在脱离路径整条失效
            events += dispatch_entry(new_sprite, self, team)
            # Observer: post_leave + post_entry
            opp_esc = self.get_opponent(team).active
            ctx_esc_leave = self._make_ctx(user, opp_esc, None, None, self.globals, team=team, turn=self.turn, self_switched=True)
            ctx_esc_entry = self._make_ctx(new_sprite, opp_esc, None, None, self.globals, team=team, turn=self.turn)
            events += self._vm_engine.fire_trigger("post_leave", ctx_esc_leave, user, opp_esc, self.globals, team=team, battle=self)
            # 洁癖/木桶戏法等 post_leave observer 可能写入新的 pending_effects
            self._apply_pending_entry_effects(team, new_sprite)
            events += self._vm_engine.fire_trigger("post_entry", ctx_esc_entry, new_sprite, opp_esc, self.globals, team=team, battle=self)
            events += self._apply_entry_transmission(team, new_sprite, opp_esc)
            events += dispatch_leave(user, self, team)

    def _handle_escape_inherit(self, team: str, user: 'Sprite', events: list[str], urgent: bool = False) -> None:
        """脱离 + 下个入场精灵继承增益。

        urgent=True:  紧急脱离，随机自动选择替补。
        urgent=False: 普通脱离，由 agent 选择替补。
        """
        player = self.get_player(team)
        # 禁足：无法离场（与 _handle_escape 一致）
        if getattr(player.active, 'locked_turns', 0) > 0:
            events.append(f'{player.active.name} 被禁足，无法脱离')
            return
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
            self._invalidate_ctx_team_cache()
            new_sprite = player.active
            new_sprite.clear_effects('battlefield')
            new_sprite.entry_turn = self.turn
            new_sprite.first_action = True
            new_sprite.inc_counter('times_entered')
            for e in inherited:
                new_sprite.add_effect(e)
            events.append(f'{user.name} 脱离→{new_sprite.name}(继承{len(inherited)}增益)')

            # ── trait hooks ──（dispatch_leave 延后，理由同 _handle_escape）
            events += dispatch_entry(new_sprite, self, team)
            # Observer: post_leave + post_entry
            opp_inh = self.get_opponent(team).active
            ctx_inh_leave = self._make_ctx(old, opp_inh, None, None, self.globals, team=team, turn=self.turn, self_switched=True)
            ctx_inh_entry = self._make_ctx(new_sprite, opp_inh, None, None, self.globals, team=team, turn=self.turn)
            events += self._vm_engine.fire_trigger("post_leave", ctx_inh_leave, old, opp_inh, self.globals, team=team, battle=self)
            # 洁癖/木桶戏法等 post_leave observer 可能写入新的 pending_effects
            self._apply_pending_entry_effects(team, new_sprite)
            events += self._vm_engine.fire_trigger("post_entry", ctx_inh_entry, new_sprite, opp_inh, self.globals, team=team, battle=self)
            events += self._apply_entry_transmission(team, new_sprite, opp_inh)
            events += dispatch_leave(old, self, team)

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
        self._invalidate_ctx_team_cache()
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

        # ── trait hooks ──（dispatch_leave 延后，理由同 _handle_escape）
        events += dispatch_entry(new_sprite, self, team)
        # Observer: post_leave + post_entry
        opp = self.get_opponent(team).active
        ctx_leave = self._make_ctx(user, opp, None, None, self.globals,
                                   team=team, turn=self.turn, self_switched=True)
        ctx_entry = self._make_ctx(new_sprite, opp, None, None, self.globals,
                                   team=team, turn=self.turn)
        events += self._vm_engine.fire_trigger("post_leave", ctx_leave, user, opp,
                                               self.globals, team=team, battle=self)
        self._apply_pending_entry_effects(team, new_sprite)
        events += self._vm_engine.fire_trigger("post_entry", ctx_entry, new_sprite, opp,
                                               self.globals, team=team, battle=self)
        events += self._apply_entry_transmission(team, new_sprite, opp)
        events += dispatch_leave(user, self, team)

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
    # 跨精灵技能轮转（过山车）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _skill_slot_locked(bs) -> bool:
        """该技能槽位是否**位置锁定**（不参与跨精灵轮转、也不占位）。

        口径（见 data/IR_GUIDE.md §3C `skill_rotate`）：
          - 主轴：`_transmission == -1`（与传动 pass 同一语义：位置不动）；
          - 临时/被替换槽位：`replaced_by is not None`（借用 / 愿力 / 巧变产物 /
            `replace_skill`）——这些槽位的还原登记是 `(队伍, 槽位号)`，移动槽位对象会让
            回合末还原命中错误的槽位，故视作位置锁定；
          - `is_temporary`（`gain_skills` 等临时技能）。
        """
        if bs is None:
            return True
        if getattr(bs, '_transmission', 0) == -1:
            return True
        if getattr(bs, 'replaced_by', None) is not None:
            return True
        if getattr(bs, 'is_temporary', False):
            return True
        return False

    def rotate_team_skills(self, team: str, offset: int = 1) -> list[str]:
        """过山车：己方**全队**携带技能跨精灵整体轮转 `offset` 位（向下移动）。

        语义（数据 `skill_rotate`，见 data/IR_GUIDE.md §3C）：
          - 取序列 = 按「精灵顺序（`player.team` 顺序）× 槽位顺序」拼接**参与槽位**
            （位置锁定的槽位被跳过，不占位）；
          - 整体轮转 `offset` 位：`rot[i] = seq[i-offset]`，末尾的 `offset` 个技能回到
            序列开头（= 每只精灵的技能列表整体向下移动 1 位、最后一位回到第一只的第一槽位）；
          - 按各精灵**原槽位数**分段写回：槽位数不同的精灵也无需补齐，技能总数不变；
          - 轮转的是 `BattleSkill` **槽位对象整体**：冷却 / 替换 / 临时标记 / 技能级
            `_modifiers` 随对象一起移动（底层 `Skill` 数据不动）。
          - 移动到的每个槽位触发一次 `skill_position_changed` 通知（与传动 pass 一致）。
        返回事件列表。
        """
        events: list[str] = []
        mcts_sim = getattr(self, '_mcts_sim', False)
        try:
            step = int(offset)
        except (TypeError, ValueError):
            step = 1
        if step == 0:
            return events

        player = self.get_player(team)
        slots: list[tuple] = []          # [(sprite, slot_index, BattleSkill)]
        for sprite in player.team:
            for i, bs in enumerate(getattr(sprite, 'skills', None) or ()):
                if self._skill_slot_locked(bs):
                    continue
                slots.append((sprite, i, bs))
        n = len(slots)
        if n < 2:
            return events
        step %= n
        if step == 0:
            return events

        values = [bs for _, _, bs in slots]
        new_values = values[-step:] + values[:-step]
        moved: list[tuple] = []          # [(sprite, slot_index, bs)]
        for (sprite, i, old_bs), new_bs in zip(slots, new_values):
            if new_bs is old_bs:
                continue
            sprite.skills[i] = new_bs
            moved.append((sprite, i, new_bs))
        if not moved:
            return events
        if not mcts_sim:
            for sprite in player.team:
                names = '/'.join(getattr(b, 'name', '?') for b in (sprite.skills or ()))
                events.append(f'{sprite.name} 技能轮转→ {names}')
        for sprite, _i, bs in moved:
            position_events = self._fire_skill_position_changed(team, sprite, bs)
            if not mcts_sim:
                events += position_events
        self._invalidate_ctx_team_cache()
        return events

    # ═══════════════════════════════════════════════════════════════
    # 传动系统
    # ═══════════════════════════════════════════════════════════════

    def _apply_transmission(self, sprite: 'Sprite', team: str | None = None) -> list[str]:
        """执行一次传动 pass：传动技能向下移动一个槽位，相邻传动合成块。

        传动X: 传动 >= 当前 pass 的参与本次移动。_transmission=-1 为主轴（不参与不阻挡）。
        返回事件列表。"""
        self._last_transmission_changed = False
        skills = sprite.skills
        n = len(skills)
        if n < 2:
            return []

        mcts_sim = getattr(self, '_mcts_sim', False)
        events: list[str] = []
        if team is None:
            team = None
            for candidate in ("A", "B"):
                try:
                    if self.get_player(candidate).active is sprite:
                        team = candidate
                        break
                except (IndexError, AttributeError):
                    continue
        max_lv = max((getattr(bs, '_transmission', 0) for bs in skills), default=0)
        if max_lv <= 0:
            return events

        track_mech_variant = self._has_mechanical_variant(sprite)
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

            # ── 第六步：位置变化结算。机械变式减耗；技能自身的
            # skill_position_changed observer 绑定到移动的 BattleSkill 对象。
            moved_skills = []
            for i, bs in enumerate(skills):
                if pre_pos.get(id(bs), -1) != i:
                    moved_skills.append(bs)
                    if track_mech_variant:
                        bs._mech_energy_reduction -= 1
            if moved_skills:
                self._last_transmission_changed = True
            if team is not None:
                for bs in moved_skills:
                    position_events = self._fire_skill_position_changed(team, sprite, bs)
                    if not mcts_sim:
                        events += position_events

            if not mcts_sim:
                names = '/'.join(bs.name for bs in skills)
                events.append(f'{sprite.name} 传动→ {names}')

        return events

    @staticmethod
    def _has_mechanical_variant(sprite: 'Sprite') -> bool:
        if getattr(sprite, '_trait_suppressed', False):
            return False
        species = getattr(sprite, 'species', None)
        if species is None:
            return False
        return (
            getattr(species, 'ability_id', 0) == 20159
            or getattr(species, 'ability', '') == '机械变式'
        )
