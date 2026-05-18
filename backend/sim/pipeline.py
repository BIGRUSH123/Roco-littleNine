"""scripts/sim/pipeline.py — 单次技能执行管线。

从 Battle._execute_single_action 提取，包含 L0-L5 完整技能管线。
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .effects import SpecialName
from .resolver import SkillResolver, TurnContext
from .battleskill import SkillUse
from scripts.common.skill_trait_ids import TRAIT_对流, TRAIT_嫉妒
from .traits import (
    dispatch_energy_short, dispatch_modifier, dispatch_damage, dispatch_skill_use,
    dispatch_take_damage, dispatch_ko_enemy, dispatch_defend,
    dispatch_before_take_damage,
)
from .traits import get_trait as _get_trait

if TYPE_CHECKING:
    from .battle import Battle
    from .sprite import Sprite
    from .player import Player
    from .action import Action
    from .battleskill import BattleSkill

# 每 hit 生效的资源类特效 (heal/gain_energy 等)
_PER_HIT_SPECIALS = {'heal', 'direct_heal', 'gain_energy', 'steal_energy', 'gain_energy_by_enemy'}


class SkillPipeline:
    """单次技能执行管线。构造→execute()→返回 events。"""

    def __init__(self, battle: 'Battle', team: str, action: 'Action',
                 is_countered: bool = False,
                 countered_skill: 'BattleSkill | None' = None,
                 countering_skill: 'BattleSkill | None' = None,
                 is_first: bool = False,
                 opponent_switched: bool = False):
        self.battle = battle
        self.team = team
        self.action = action
        self.is_countered = is_countered
        self.countered_skill = countered_skill
        self.countering_skill = countering_skill
        self.is_first = is_first
        self.opponent_switched = opponent_switched

        # 在 execute() 中设置
        self.events: list[str] = []
        self.player: 'Player' = None  # type: ignore[assignment]
        self.opponent: 'Player' = None  # type: ignore[assignment]
        self.user: 'Sprite' = None  # type: ignore[assignment]
        self.target: 'Sprite' = None  # type: ignore[assignment]
        self.skill: 'BattleSkill | None' = None
        self.use: 'SkillUse | None' = None
        self.ctx: 'TurnContext | None' = None
        self.effective_combo: int = 1
        self.extra_uses: int = 1

    # ═══════════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════════

    def execute(self) -> list[str]:
        """执行完整技能管线，返回事件列表。"""
        self.player = self.battle.get_player(self.team)
        self.opponent = self.battle.get_opponent(self.team)
        self.user = self.player.active
        self.target = self.opponent.active

        if self.user.is_fainted:
            return self.events

        # ── 聚能 ──
        if self.action.kind == 'gather':
            return self._do_gather()

        # ── 技能 ──
        if self.action.kind != 'skill' or self.action.skill_index is None:
            return self.events

        self.skill = self.battle._get_skill(self.team, self.action)
        if self.skill is None:
            self.events.append(f'[错误] {self.user.name} 无技能[{self.action.skill_index}]')
            return self.events

        # ═══ gate: 冷却 ═══
        if self._gate_cooldown():
            return self.events

        # ═══ gate: 迸发能耗 ═══
        self._gate_burst_energy()

        # 应对日志
        if self.is_countered and self.countering_skill:
            opp_sprite = self.opponent.active
            self.events.append(
                f'{opp_sprite.name}应对{self.user.name}：{self.user.name}使用了'
                f'{self.skill.name}，但被{opp_sprite.name}（{self.countering_skill.name}）应对了！'
            )

        # ═══ gate: 能量支付 ═══
        if not self._gate_energy_payment():
            return self.events

        # ═══ gate: 蓄力 ═══
        charge_result = self._gate_charge()
        if charge_result is True:
            return self.events   # 进入蓄力
        if charge_result is False:
            return self.events   # 蓄力中，拒绝

        # ═══ L0: modifier 预计算 ═══
        self._stage_L0_collect()

        # ═══ L1: 动态威力解算 ═══
        self._stage_L1_power()

        # ═══ L2: 伤害层 [per-hit loop] ═══
        self._stage_L2_damage_loop()

        # ═══ L3: 状态层 ═══
        self._stage_L3_state()

        # ═══ L3.5: 技能使用后永久增长 ═══
        self._stage_L35_post_use()

        # ═══ L4: 反击层 ═══
        self._stage_L4_counter()

        # ═══ L5: 换宠层 + post ═══
        self._stage_L5_field()
        self._stage_L5_post()

        return self.events

    # ═══════════════════════════════════════════════════════════════
    # 聚能
    # ═══════════════════════════════════════════════════════════════

    def _do_gather(self) -> list[str]:
        gained = self.user.gain_energy(5)
        self.user.first_action = False
        self.user.inc_counter('times_gathered')
        self.events.append(f'{self.user.name} 聚能+{gained}E(→{self.user.energy})')
        opp_team = 'B' if self.team == 'A' else 'A'
        self.battle.inc_team_counter(opp_team, 'enemy_gather')
        return self.events

    # ═══════════════════════════════════════════════════════════════
    # Gate 1: 冷却
    # ═══════════════════════════════════════════════════════════════

    def _gate_cooldown(self) -> bool:
        """True = 拦截（冷却中）。"""
        if self.skill and self.skill.cooldown > 0:
            self.events.append(
                f'[冷却中] {self.user.name} {self.skill.name} 还需{self.skill.cooldown}回合冷却'
            )
            return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # Gate 2: 迸发能耗
    # ═══════════════════════════════════════════════════════════════

    def _gate_burst_energy(self) -> None:
        if not self.user.first_action:
            return
        for e in self.skill.effects:  # type: ignore[union-attr]
            if getattr(e, 'kind', '') == 'special' and getattr(e, 'name', '') in (SpecialName.BURST, SpecialName.FIRST_ACTION):
                e_target = getattr(e, 'target', 'opp')
                if e_target == 'burst_collect':
                    burst_history = getattr(self.user, '_burst_effects_used', set())
                    collected = len(burst_history)
                    if collected > 0:
                        self.skill.energy_cost_mod += collected  # type: ignore[union-attr]
                        self.events.append(f'{self.user.name} 雷暴收集{collected}种迸发 能耗+{collected}')
                else:
                    ec_change = int(getattr(e, 'amount', 0) or 0)
                    if ec_change != 0:
                        self.skill.energy_cost_mod += ec_change  # type: ignore[union-attr]
                        self.events.append(f'{self.user.name} 迸发 {self.skill.name}能耗{ec_change:+d}')  # type: ignore[union-attr]

    # ═══════════════════════════════════════════════════════════════
    # Gate 3: 能量支付
    # ═══════════════════════════════════════════════════════════════

    def _gate_energy_payment(self) -> bool:
        """True = 继续，False = 拦截（能量不足）。"""
        cost = self.skill.energy_cost  # type: ignore[union-attr]
        ecost = self.user.effective_stat('energy_cost')
        if getattr(self.user.species, 'ability_id', 0) == TRAIT_对流:
            ecost = -ecost
        cost += ecost
        cost = round(cost * self.battle.globals.weather_energy_mod(self.skill.element or ''))  # type: ignore[union-attr]
        cost = max(0, cost - self.battle.globals.mark_energy_mod(self.team))
        # 一次性能耗修正消费
        self.user.clear_effects('next_use')

        if self.user.energy < cost:
            deficit = cost - self.user.energy
            hp_sub = dispatch_energy_short(self.user, deficit, self.battle, self.team)
            if hp_sub > 0:
                self.user.take_damage(hp_sub)
                self.user.lose_energy(self.user.energy)
                self.events.append(f'{self.user.name} 消耗{hp_sub}HP代替{deficit}E')
            else:
                self.events.append(f'[能量不足] {self.user.name} E={self.user.energy} < {cost}')
                return False

        self.user.lose_energy(cost)
        self.user.inc_counter(f'skill_used:{self.skill.name}')  # type: ignore[union-attr]
        self.user.inc_counter('skills_used')
        return True

    # ═══════════════════════════════════════════════════════════════
    # Gate 4: 蓄力
    # ═══════════════════════════════════════════════════════════════

    def _gate_charge(self) -> bool | None:
        """True = 进入蓄力(return events)，False = 拦截(蓄力中错误技能)，
        None = 通过，继续管线。"""
        has_charge = any(
            e.name == SpecialName.CHARGE
            for e in self.skill.effects  # type: ignore[union-attr]
            if getattr(e, 'kind', '') == 'special'
        )
        is_charging = getattr(self.user, '_charging', False)
        charged_idx = getattr(self.user, '_charged_skill_index', -1)

        # 龙息环爆等：下个技能无需蓄力
        if has_charge and getattr(self.user, '_skip_charge_next', False):
            self.user._skip_charge_next = False
            self.events.append(f'{self.user.name} 跳过蓄力({self.skill.name})')  # type: ignore[union-attr]
            return None

        if is_charging and has_charge and self.action.skill_index == charged_idx:
            self.user._charging = False
            self.user._charged_skill_index = -1
            self.events.append(f'{self.user.name} 蓄力释放！')
            return None

        if is_charging:
            _trait = _get_trait(self.user)
            if _trait and _trait.trait_id == TRAIT_嫉妒:
                self.user._charging = False
                self.user._charged_skill_index = -1
                self.events.append(f'{self.user.name} 蓄力中断（嫉妒）')
                return None

            if self.action.kind == 'gather':
                self.events.append(f'[蓄力中] {self.user.name} 只能使用蓄力技能或换宠')
                return False

            charged_name = (
                self.user.skills[charged_idx].name
                if 0 <= charged_idx < len(self.user.skills) else '?'
            )
            self.events.append(f'[蓄力中] {self.user.name} 只能使用{charged_name}或换宠')
            return False

        if has_charge:
            self.user._charging = True
            self.user._charged_skill_index = self.action.skill_index
            self.events.append(f'{self.user.name} 蓄力({self.skill.name})')  # type: ignore[union-attr]
            return True

        return None

    # ═══════════════════════════════════════════════════════════════
    # Stage L0: modifier 预计算
    # ═══════════════════════════════════════════════════════════════

    def _stage_L0_collect(self) -> None:
        self.ctx = TurnContext(
            turn=self.battle.turn, is_first=self.is_first,
            countered_skill=self.countered_skill,
            opponent_switched=self.opponent_switched,
            battle=self.battle, team=self.team,
        )

        self.use = SkillUse(
            battle_skill=self.skill,
            is_countered=self.is_countered,
            is_first=self.is_first,
            countered_skill=self.countered_skill,
            countering_skill=self.countering_skill,
            skill_index=self.action.skill_index or -1,
        )

        # L0 扩展：需要 sprite 上下文的 modifier
        self.events += self.battle._resolver.dispatch_modifiers(self.user, self.use)

        # trait modifier hook（L0→L1 之间）
        self.events += dispatch_modifier(self.user, self.use, self.battle, self.team)

        # 迸发 (burst / first_action) 威力/额外使用效果
        self._collect_burst_power()

        # 动态 damage_reduction（不可接触等）
        self._collect_dynamic_dr()

        # 动态 power_bonus（鸩毒等）
        self._collect_dynamic_power_bonus()

    def _collect_burst_power(self) -> None:
        if not self.user.first_action:
            return
        for e in self.skill.effects:  # type: ignore[union-attr]
            if getattr(e, 'kind', '') != 'special' or getattr(e, 'name', '') not in (SpecialName.BURST, SpecialName.FIRST_ACTION):
                continue
            e_target = getattr(e, 'target', 'opp')

            if e_target == 'burst_collect':
                burst_history = getattr(self.user, '_burst_effects_used', set())
                collected = len(burst_history)
                if collected > 0:
                    self.use.modifiers['power_bonus'] = self.use.modifiers.get('power_bonus', 0) + collected * 10  # type: ignore[union-attr]
                    self.events.append(f'{self.user.name} 雷暴收集{collected}种迸发 威力+{collected * 10}')
                if not hasattr(self.user, '_burst_effects_used'):
                    self.user._burst_effects_used = set()
                self.user._burst_effects_used.add(self.skill.base.name)  # type: ignore[union-attr]
                continue

            power_bonus = int(getattr(e, 'value', 0) or 0)
            if power_bonus > 0:
                self.use.modifiers['power_bonus'] = self.use.modifiers.get('power_bonus', 0) + power_bonus  # type: ignore[union-attr]
                self.events.append(f'{self.user.name} 迸发 威力+{power_bonus}')

            if e_target == 'extra_use':
                self.user.extra_skill_use = True
                self.events.append(f'{self.user.name} 迸发 使用次数+1')

            if not hasattr(self.user, '_burst_effects_used'):
                self.user._burst_effects_used = set()
            self.user._burst_effects_used.add(self.skill.base.name)  # type: ignore[union-attr]

    def _collect_dynamic_dr(self) -> None:
        if not (self.use and self.use.is_countered and self.use.countering_skill):
            return
        for e in self.use.countering_skill.effects:
            if getattr(e, 'kind', '') != 'special':
                continue
            if e.name == SpecialName.DAMAGE_REDUCTION_BY_ABNORMAL:
                aname = getattr(e, 'abnormal_name', '')
                base = getattr(e, 'value', 0.0) or 0.0
                per = getattr(e, 'per_stack_value', 0.0) or 0.0
                cap = getattr(e, 'max_value', 1.0) or 1.0
                stacks = self.user.get_stacks(aname)
                dynamic = min(base + per * stacks, cap)
                old = self.use.modifiers.get('damage_reduction', 0)
                if dynamic > old:
                    self.use.modifiers['damage_reduction'] = dynamic
                    self.events.append(
                        f'{self.target.name} 不可接触 减伤{old:.0%}→{dynamic:.0%}({aname}×{stacks})'
                    )

    def _collect_dynamic_power_bonus(self) -> None:
        if not self.skill.is_attack:  # type: ignore[union-attr]
            return
        best_pba = None
        for e in self.skill.effects:  # type: ignore[union-attr]
            if getattr(e, 'kind', '') == 'special' and getattr(e, 'name', '') == SpecialName.POWER_BY_ABNORMAL:
                best_pba = e
            elif getattr(e, 'kind', '') == 'conditional':
                when = getattr(e, 'when', None) or {}
                then = getattr(e, 'then', None) or []
                if when.get('kind') == 'counter_succeeded' and self.is_countered:
                    for sub in then:
                        if getattr(sub, 'kind', '') == 'special' and getattr(sub, 'name', '') == SpecialName.POWER_BY_ABNORMAL:
                            best_pba = sub
        if best_pba:
            aname = getattr(best_pba, 'abnormal_name', '')
            per_stack = getattr(best_pba, 'value', 0) or 0
            stacks = self.target.get_stacks(aname)
            bonus = int(per_stack * stacks)
            old = self.use.modifiers.get('power_bonus', 0)  # type: ignore[union-attr]
            if bonus > old:
                self.use.modifiers['power_bonus'] = bonus  # type: ignore[union-attr]
                self.events.append(f'{self.user.name} {self.skill.name} 威力+{bonus}({aname}×{stacks})')  # type: ignore[union-attr]

    # ═══════════════════════════════════════════════════════════════
    # Stage L1: 动态威力解算
    # ═══════════════════════════════════════════════════════════════

    def _stage_L1_power(self) -> None:
        # 消费 next_attack_mult
        if self.skill.is_attack and self.skill.next_attack_mult != 1.0:  # type: ignore[union-attr]
            self.use.modifiers['power_mult'] = self.use.modifiers.get('power_mult', 1.0) * self.skill.next_attack_mult  # type: ignore[union-attr]
            self.skill.next_attack_mult = 1.0  # type: ignore[union-attr]

        # 动态威力
        self._resolve_dynamic_power()

        # 有效连击数
        self._resolve_combo()

        # 额外使用次数
        self.extra_uses = 2 if self.user.extra_skill_use else 1
        self.user.extra_skill_use = False

        # trait damage hook（L1→L2 之间）
        self.events += dispatch_damage(self.user, self.target, self.use, self.battle, self.team)

    def _resolve_dynamic_power(self) -> None:
        for e in self.skill.effects:  # type: ignore[union-attr]
            if getattr(e, 'kind', '') != 'special':
                continue
            if e.name == SpecialName.POWER_BY_ENEMY_ENERGY:
                total_e = sum(bs.energy_cost for bs in self.target.skills)
                self.skill.power_override = int(total_e * (e.value or 10))  # type: ignore[union-attr]
            elif e.name == SpecialName.POWER_BY_ADJACENT:
                adj_sum = 0
                si = self.action.skill_index
                if si is not None:
                    for offset in (-1, 1):
                        idx = si + offset
                        if 0 <= idx < len(self.user.skills):
                            adj_sum += self.user.skills[idx].power
                self.skill.power_override = max(1, int(adj_sum * (e.value or 0.333)))  # type: ignore[union-attr]
            elif e.name == SpecialName.POWER_BY_FAINTED:
                opp_player = self.battle.get_player('B' if self.team == 'A' else 'A')
                fainted = sum(1 for s in opp_player.team if s.is_fainted)
                bonus = int(fainted * (e.value or 30))
                if bonus > 0:
                    self.use.modifiers['power_bonus'] = self.use.modifiers.get('power_bonus', 0) + bonus  # type: ignore[union-attr]
                    self.events.append(f'{self.user.name} {self.skill.name} 威力+{bonus}(力竭×{fainted})')  # type: ignore[union-attr]
            elif e.name == SpecialName.POWER_BY_MISSING_HP:
                hp_pct_lost = max(0.0, 1.0 - self.user.current_hp / self.user.max_hp if self.user.max_hp else 0)
                step = max(1.0, float(e.value or 5))
                per_step = int(e.amount or 5)
                chunks = int(hp_pct_lost * 100.0 / step)
                bonus = chunks * per_step
                if bonus > 0:
                    self.use.modifiers['power_bonus'] = self.use.modifiers.get('power_bonus', 0) + bonus  # type: ignore[union-attr]
                    self.events.append(f'{self.user.name} {self.skill.name} 威力+{bonus}(损失{hp_pct_lost:.0%}HP)')  # type: ignore[union-attr]
            elif e.name == SpecialName.POWER_PENALTY_BY_ENERGY:
                enemy_energy = self.target.energy
                penalty = int(enemy_energy * (e.value or 10))
                if penalty > 0:
                    self.use.modifiers['power_bonus'] = self.use.modifiers.get('power_bonus', 0) - penalty  # type: ignore[union-attr]
                    self.events.append(f'{self.user.name} {self.skill.name} 威力-{penalty}(敌方能量{enemy_energy})')  # type: ignore[union-attr]
            elif e.name == SpecialName.CONSUME_ENERGY_FOR_POWER:
                consumed = self.user.energy
                if consumed > 0:
                    per_e = int(e.value or 50)
                    bonus = consumed * per_e
                    self.user.lose_energy(consumed)
                    self.use.modifiers['power_bonus'] = self.use.modifiers.get('power_bonus', 0) + bonus  # type: ignore[union-attr]
                    self.events.append(f'{self.user.name} 消耗{consumed}E 威力+{bonus}')
            elif e.name == SpecialName.POWER_BY_ENEMY_POWER:
                mult = float(e.value or 1.0)
                max_power = max((s.power for s in self.target.skills), default=0)
                self.skill.power_override = max(1, int(max_power * mult))  # type: ignore[union-attr]
                self.events.append(f'{self.user.name} {self.skill.name} 威力={self.skill.power_override}(敌方威力{max_power}×{mult})')  # type: ignore[union-attr]

    def _resolve_combo(self) -> None:
        self.effective_combo = self.skill.combo  # type: ignore[union-attr]
        if self.skill.combo < 1:  # type: ignore[union-attr]
            return

        combo_mod = self.user.effective_stat('combo')
        if combo_mod:
            self.effective_combo = max(1, self.effective_combo + combo_mod)
        combo_mult_steps = self.user.effective_stat('combo_mult')
        if combo_mult_steps > 0:
            self.effective_combo = max(1, int(self.effective_combo * (1.0 + combo_mult_steps)))

        # 动态条件效果（应对/先手/后手/换宠/能量触发）
        best_multi = 1
        best_power_mult = 1.0
        best_damage_mult = 1.0
        for e in self.skill.effects:  # type: ignore[union-attr]
            if getattr(e, 'kind', '') != 'conditional':
                continue
            when = getattr(e, 'when', None) or {}
            then = getattr(e, 'then', None) or []
            met = SkillResolver._check_condition(when, self.user, self.target, self.battle.globals, self.ctx)
            if met:
                for sub in then:
                    if getattr(sub, 'kind', '') != 'special':
                        continue
                    name = getattr(sub, 'name', '')
                    if name == SpecialName.MULTI_HIT:
                        val = int(getattr(sub, 'value', 0) or 0)
                        if val > best_multi:
                            best_multi = val
                    elif name == SpecialName.POWER_MULT:
                        val = float(getattr(sub, 'value', 1.0) or 1.0)
                        if val > best_power_mult:
                            best_power_mult = val
                    elif name == SpecialName.DAMAGE_MULT:
                        val = float(getattr(sub, 'value', 1.0) or 1.0)
                        if val > best_damage_mult:
                            best_damage_mult = val

        if best_multi > 1:
            old = self.use.modifiers.get('multi_hit', 0)  # type: ignore[union-attr]
            if best_multi > old:
                self.use.modifiers['multi_hit'] = best_multi  # type: ignore[union-attr]
                self.events.append(f'{self.user.name} {self.skill.name} 连击→{best_multi}')  # type: ignore[union-attr]
        if best_power_mult > 1.0:
            self.use.modifiers['power_mult'] = self.use.modifiers.get('power_mult', 1.0) * best_power_mult  # type: ignore[union-attr]
            self.events.append(f'{self.user.name} {self.skill.name} 威力×{best_power_mult}')  # type: ignore[union-attr]
        if best_damage_mult > 1.0:
            self.use.modifiers['damage_mult'] = self.use.modifiers.get('damage_mult', 1.0) * best_damage_mult  # type: ignore[union-attr]
            self.events.append(f'{self.user.name} {self.skill.name} 伤害×{best_damage_mult}')  # type: ignore[union-attr]

        dynamic_combo = int(self.use.modifiers.get('multi_hit', 1.0))  # type: ignore[union-attr]
        if dynamic_combo > 1:
            self.effective_combo = max(self.effective_combo, dynamic_combo)
            self.use.modifiers.pop('multi_hit', None)  # type: ignore[union-attr]

    # ═══════════════════════════════════════════════════════════════
    # Stage L2: 伤害层 [per-hit loop]
    # ═══════════════════════════════════════════════════════════════

    def _stage_L2_damage_loop(self) -> None:
        for extra_i in range(self.extra_uses):
            if extra_i > 0:
                self.events.append(f'{self.user.name} {self.skill.name} 额外使用(不耗能)')  # type: ignore[union-attr]

            for hit_i in range(self.effective_combo):
                if self.target.is_fainted:
                    break

                if self.skill.is_attack:  # type: ignore[union-attr]
                    # trait: 防御方伤害修正
                    target_team_def = 'B' if self.team == 'A' else 'A'
                    self.events += dispatch_defend(self.target, self.user, self.use, self.battle, target_team_def)

                    damage, dmg_events = self.battle._resolver.calc_damage(
                        self.user, self.target, self.use, self.battle.globals,
                        attacker_team=self.team,
                    )
                    # before_take_damage hook
                    target_team = 'B' if self.team == 'A' else 'A'
                    modified = dispatch_before_take_damage(
                        self.target, self.user, damage, self.skill.element or '', self.battle, target_team)  # type: ignore[union-attr]
                    if modified is not None:
                        if modified == 0:
                            self.events.append(f'{self.target.name} 免疫 {self.skill.name} 伤害')  # type: ignore[union-attr]
                            damage = 0
                        elif modified < 0:
                            healed = self.target.heal(-modified)
                            if healed:
                                self.events.append(f'{self.target.name} 吸收+{healed}HP')
                            damage = 0
                        else:
                            damage = modified
                    if damage > 0:
                        self.target.take_damage(damage)
                    self.target.inc_counter('times_hit')
                    self.user.inc_counter('times_dealt')
                    combo_label = f' ({hit_i+1}/{self.effective_combo})' if self.effective_combo > 1 else ''
                    self.events.append(f'{self.user.name} {self.skill.name} → {self.target.name} -{damage}HP{combo_label}')  # type: ignore[union-attr]
                    self.events.extend(dmg_events)

                    # trait: 受到伤害 / 击败敌人
                    target_team = 'B' if self.team == 'A' else 'A'
                    self.events += dispatch_take_damage(self.target, self.user, damage, self.battle, target_team)
                    if self.target.is_fainted:
                        self.events += dispatch_ko_enemy(self.user, self.target, self.battle, self.team)

                    # 吸血
                    self._apply_life_drain(damage)

        # 迸发/初次行动标记消费
        remaining = getattr(self.user, '_burst_remaining', 0)
        if remaining > 0:
            self.user._burst_remaining = remaining - 1
        else:
            self.user.first_action = False

        # 星陨结算
        if self.skill.is_attack and not self.target.is_fainted:  # type: ignore[union-attr]
            defender_team_star = 'B' if self.team == 'A' else 'A'
            star_dmg, star_events = self.battle._resolver.resolve_starfall(
                self.user, self.target, self.skill, self.battle.globals, defender_team_star)
            self.events += star_events
            if self.target.is_fainted:
                self.events += dispatch_ko_enemy(self.user, self.target, self.battle, self.team)

    def _apply_life_drain(self, damage: int) -> None:
        drain_pct = 0.0
        life_drain_effects = [
            e for e in self.skill.effects  # type: ignore[union-attr]
            if getattr(e, 'kind', '') == 'special' and e.name == SpecialName.LIFE_DRAIN
        ]
        for ld in life_drain_effects:
            pct = ld.value / 100.0 if ld.value > 1 else ld.value
            drain_pct = max(drain_pct, pct)
        sprite_drain = self.user.effective_stat('life_drain')
        if sprite_drain > 0:
            drain_pct = max(drain_pct, sprite_drain * 0.1)
        if drain_pct > 0:
            healed = self.user.heal(round(damage * drain_pct))
            if healed:
                self.events.append(f'{self.user.name} 吸血{drain_pct*100:.0f}%+{healed}HP')

    # ═══════════════════════════════════════════════════════════════
    # Stage L3: 状态层
    # ═══════════════════════════════════════════════════════════════

    def _stage_L3_state(self) -> None:
        self.events += self.battle._resolver.dispatch_L3(
            self.user, self.target, self.use, self.battle.globals, self.ctx,
            team=self.team, battle=self.battle,
        )

        # 非攻击技能连击：L3 已处理第1 hit，额外应用剩余 hit
        if not self.skill.is_attack and self.effective_combo > 1:  # type: ignore[union-attr]
            for hit_i in range(1, self.effective_combo):
                for effect in self.skill.effects:  # type: ignore[union-attr]
                    kind = getattr(effect, 'kind', '')
                    if kind == 'special' and effect.name in _PER_HIT_SPECIALS:
                        self.events += self.battle._resolver._handle_special(
                            self.user, self.target, effect, self.battle.globals, self.ctx, self.use)
                    elif kind == 'stat':
                        self.events += SkillResolver._handle_stat(self.user, self.target, effect)
                    elif kind == 'mark':
                        self.events += SkillResolver._handle_mark(self.battle.globals, effect, self.team)

    # ═══════════════════════════════════════════════════════════════
    # Stage L3.5: 技能使用后永久增长
    # ═══════════════════════════════════════════════════════════════

    def _stage_L35_post_use(self) -> None:
        self.events += self.battle._resolver.dispatch_post_use(
            self.user, self.target, self.use, self.battle.globals, self.ctx)

    # ═══════════════════════════════════════════════════════════════
    # Stage L4: 反击层
    # ═══════════════════════════════════════════════════════════════

    def _stage_L4_counter(self) -> None:
        self.events += self.battle._resolver.resolve_counter_damage(
            self.user, self.target, self.use, self.battle.globals, self.ctx,
        )

        # 防御技能冷却
        if self.skill.base.is_defense:  # type: ignore[union-attr]
            self.skill.cooldown = 2  # type: ignore[union-attr]

        # 应对成功效果
        if self.countered_skill is not None:
            for e in self.skill.effects:  # type: ignore[union-attr]
                if getattr(e, 'kind', '') == 'conditional':
                    when = getattr(e, 'when', None) or {}
                    if when.get('kind') == 'counter_succeeded':
                        for sub in getattr(e, 'then', []):
                            if getattr(sub, 'kind', '') != 'special':
                                continue
                            name = getattr(sub, 'name', '')
                            if name == SpecialName.DEFENSE_COOLDOWN_REDUCE:
                                if self.skill.cooldown > 0:  # type: ignore[union-attr]
                                    self.skill.cooldown -= 1  # type: ignore[union-attr]
                                    self.events.append(f'{self.user.name} {self.skill.name} 应对成功，冷却-1')  # type: ignore[union-attr]
                            elif name == SpecialName.SKIP_NEXT_CHARGE:
                                self.user._skip_charge_next = True
                                self.events.append(f'{self.user.name} 下个技能无需蓄力')

    # ═══════════════════════════════════════════════════════════════
    # Stage L5: 换宠层
    # ═══════════════════════════════════════════════════════════════

    def _stage_L5_field(self) -> None:
        special_names = {
            getattr(e, 'name', '')
            for e in self.skill.effects  # type: ignore[union-attr]
            if getattr(e, 'kind', '') == 'special'
        }

        if SpecialName.ESCAPE_INHERIT in special_names:
            self.battle._handle_escape_inherit(self.team, self.user, self.events)
        elif SpecialName.ESCAPE in special_names:
            self.battle._handle_escape(self.team, self.user, self.events)

        if SpecialName.FORCE_RETURN in special_names:
            opp_team = 'B' if self.team == 'A' else 'A'
            self.events += self.battle._resolve_return(opp_team)

        if SpecialName.BORROW_SKILL in special_names:
            self.battle._handle_borrow_skill(self.team, self.user, self.action.skill_index or 0, self.events)

    # ═══════════════════════════════════════════════════════════════
    # Stage L5 post: trait + team counters
    # ═══════════════════════════════════════════════════════════════

    def _stage_L5_post(self) -> None:
        if self.action.kind == 'skill':
            self.events += dispatch_skill_use(self.user, self.skill, self.battle, self.team)
            if self.skill.element:  # type: ignore[union-attr]
                self.battle.inc_team_counter(self.team, f'element:{self.skill.element}')  # type: ignore[union-attr]
            if self.skill.base.is_defense:  # type: ignore[union-attr]
                self.battle.inc_team_counter(self.team, 'defense_skill')
            elif not self.skill.base.is_attack:  # type: ignore[union-attr]
                self.battle.inc_team_counter(self.team, 'status_skill')
