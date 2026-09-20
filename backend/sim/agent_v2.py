"""backend/sim/agent_v2.py — 基于社区 PVP 攻略经验重写的规则 AI（6v6），支持队伍策略配置。

经验来源（洛克王国世界社区攻略，2026-09 检索）：
1. 速度为王：先手权决定攻防次序（"优先堆速度，抢到先手可优先控场/爆发"）。
2. 斩杀优先：本回合能击杀对手时立即出手（快攻体系"一两个回合结束战斗"）。
3. 能量管理："初期避免直接使用高耗能大招"，低耗试探；能量不足最强攻击时先聚能。
4. 换宠时机：首发控场 → 次发爆发 → 尾发续航；只在 被杀威胁/对位无效 时轮换，
   杜绝无谓换人（换人有 tempo 成本）。
5. 强化推队：T0 思路——工具人/核心拿强化后"一招一个"；无斩杀窗口且能量富余
   时用强化技能堆增益。
6. 首发对位：伤害期望 + 速度 + 生存综合评分。

参数化设计（BC 专家数据用）：TeamStrategy 允许每支队伍声明精灵角色——
首发（lead）/ 必保（preserve）/ 能量预算（energy_hold）/ 换宠阈值覆盖——
策略差异全部走配置，底层斩杀/伤害计算逻辑复用。strategy=None 时行为
与无配置版本完全一致（默认阈值即原常量）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .action import Action
from .agent import _GATHER_ACTION, _ITEM_ACTION, _skill_action, _switch_action
from .agent import ELEMENTAL_BLOODLINES
from .battleskill import SkillUse
from .sprite import Sprite

_WEAK_ATTACK_RATIO = 0.12  # 最强攻击 < 12% 对手 HP 视为"缺乏有效输出"


def _leader_form_action(battle, team: str, sprite: Sprite, opp) -> Action:
    """进化之力：在多首领形态家族里挑对当前对手属性最优的形态。

    候选列表由引擎给出（同外观优先，确定性排序），动作槽位 = 候选下标。
    评测口径：候选形态属性对对手场上属性的进攻克制和（同分保持候选顺序，
    即同外观/默认外观优先）。单候选家族退化为 Action(kind='item')，等价槽 0。
    """
    try:
        candidates = battle.item_variants(team)
    except AttributeError:
        return _ITEM_ACTION
    if len(candidates) <= 1:
        return _ITEM_ACTION
    opp_elements = tuple(getattr(opp.species, 'elements', ()) or ())
    if not opp_elements:
        return _ITEM_ACTION

    from .resolver import _TYPE_CHART

    best_idx, best_score = 0, float('-inf')
    for idx, species in enumerate(candidates):
        score = 0.0
        for my_elem in (species.elements or ()):
            chart = _TYPE_CHART.get(my_elem, {})
            score += sum(chart.get(opp_elem, 1.0) for opp_elem in opp_elements)
        if score > best_score:
            best_idx, best_score = idx, score
    return _ITEM_ACTION if best_idx == 0 else Action(kind='item', variant=best_idx)


@dataclass(frozen=True)
class SpriteStrategy:
    """单只精灵的策略角色配置（role 仅作标签，行为由下列字段驱动）。"""

    role: str = "attack"       # attack / support / tank / closer
    lead: bool = False         # 首发候选
    preserve: bool = False     # 必保精灵：换宠时避免牺牲，被威胁时优先撤出
    energy_hold: int = 0       # 能量预算：低于此值且无斩杀窗口时不低耗泄招（0=关闭）
    threat_switch_hp: float = 0.9   # 规则2 被杀威胁换位的己方血量上限
    switch_hp: float = 0.35         # 规则5 残血换位阈值


@dataclass
class TeamStrategy:
    """整队策略：按精灵名（species name）查找配置，未命中用 default。"""

    name: str = ""
    sprites: dict[str, SpriteStrategy] = field(default_factory=dict)
    default: SpriteStrategy = field(default_factory=SpriteStrategy)

    def for_species(self, name: str) -> SpriteStrategy:
        return self.sprites.get(name, self.default)


class RuleAgentV2:
    """基于社区 PVP 攻略经验的规则 AI（可挂 TeamStrategy）。"""

    def __init__(self, team: str, player, strategy: TeamStrategy | None = None):
        self.team = team
        self.player = player
        self.strategy = strategy or TeamStrategy()

    def _st(self, sprite: Sprite) -> SpriteStrategy:
        return self.strategy.for_species(sprite.name)

    # ── 通用计算 ──

    def _attack_table(self, battle, attacker: Sprite, defender: Sprite):
        """(index, dmg, cost) 列表：可用攻击技能的伤害/能耗（含克制/印记）。"""
        table = []
        for i, skill in enumerate(attacker.skills):
            if skill.cooldown > 0 or skill.sealed:
                continue
            if not skill.is_attack:
                continue
            dmg, _ = battle._resolver.calc_damage(
                attacker, defender, SkillUse(battle_skill=skill),
                battle.globals, attacker_team=self.team,
            )
            table.append((i, dmg, skill.energy_cost))
        return table

    def _best_hit(self, battle, attacker: Sprite, defender: Sprite):
        """最强单发 (dmg, cost, index)；无攻击技能返回 (0, 0, -1)。"""
        table = self._attack_table(battle, attacker, defender)
        if not table:
            return 0, 0, -1
        i, dmg, cost = max(table, key=lambda x: (x[1], -x[2]))
        return dmg, cost, i

    def _best_matchup(self, battle, bench_indices, defender: Sprite):
        """板凳中对位最好的替补 (idx, dmg)。"""
        best_idx, best_dmg = -1, -1.0
        p = self.player
        for idx in bench_indices:
            sprite = p.team[idx]
            dmg, _, _ = self._best_hit(battle, sprite, defender)
            score = dmg + sprite.current_hp * 0.08
            if score > best_dmg:
                best_dmg = score
                best_idx = idx
        return best_idx, best_dmg

    # ── Agent 接口 ──

    def choose_lead(self, battle) -> int:
        """首发对位：伤害期望 + 速度 + 生存综合评分（首发候选受限时在其中选）。"""
        p = self.player
        opp_lead = battle.get_opponent(self.team).active
        alive = [i for i, s in enumerate(p.team) if not s.is_fainted]
        leads = [i for i in alive if self._st(p.team[i]).lead]
        candidates = leads if leads else alive
        best_idx, best_score = candidates[0] if candidates else 0, -1e9
        for i in candidates:
            s = p.team[i]
            dmg, _, _ = self._best_hit(battle, s, opp_lead)
            dmg_ratio = dmg / max(1, opp_lead.current_hp)
            speed_win = 1 if s.effective_stat('speed') >= opp_lead.effective_stat('speed') else 0
            bulk = s.current_hp + s.effective_stat('def') + s.effective_stat('sp_def')
            score = dmg_ratio * 2.0 + speed_win * 0.2 + bulk / 1500.0
            if score > best_score:
                best_score = score
                best_idx = i
        return best_idx

    def choose_action(self, battle):
        p = self.player
        s = p.active
        opp = battle.get_opponent(self.team).active
        st = self._st(s)

        # 力竭 → 强制换宠
        if s.is_fainted:
            replacement = p.find_replacement()
            if replacement is not None:
                return _switch_action(replacement)
            return _GATHER_ACTION

        # 道具（沿用旧经验：进化之力开局首领化 / 愿力残血反打）
        item = p.item
        if item and item.can_use(battle.turn):
            if item.name == '进化之力' and battle.turn <= 2 and s.bloodline == '首领':
                return _leader_form_action(battle, self.team, s, opp)
            if item.name == '愿力':
                hp_ratio = s.current_hp / max(1, s.max_hp)
                if hp_ratio < 0.5 and s.bloodline in ELEMENTAL_BLOODLINES:
                    return _ITEM_ACTION

        # 蓄力中：引擎强制语义（旧版一致）
        has_charging = getattr(s, '_charging', False)
        if has_charging:
            from .traits import get_trait
            h = get_trait(s)
            free_charge = h and h.name in ('游弋', '嫉妒')
            charged_idx, charged_skill = battle._charged_skill(s)
            if charged_skill is not None:
                return _skill_action(charged_idx)
            if not free_charge:
                replacement = p.find_replacement()
                if replacement is not None:
                    return _switch_action(replacement)
                return _GATHER_ACTION

        # ── 攻击面板：全部可用攻击 (i, dmg, cost) ──
        table = [(i, dmg, cost) for (i, dmg, cost) in self._attack_table(battle, s, opp)
                 if cost <= s.energy]
        opp_best, _, _ = self._best_hit(battle, opp, s)
        my_speed_first = s.effective_stat('speed') >= opp.effective_stat('speed')
        hp_ratio = s.current_hp / max(1, s.max_hp)

        # ── 1. 斩杀优先（快攻体系）──
        kills = [(i, dmg, cost) for (i, dmg, cost) in table
                 if dmg >= opp.current_hp > 0]
        if kills:
            kills.sort(key=lambda x: (x[2], -x[1]))
            return _skill_action(kills[0][0])

        # ── 2. 被杀威胁 → 换宠（仅此触发 + 对位更优才换）──
        # preserve 精灵愿意放弃先手权保命（tempo 换生存）
        speed_ok = (not my_speed_first) or st.preserve
        if opp_best >= s.current_hp > 0 and hp_ratio < st.threat_switch_hp and speed_ok:
            bench = [i for i in p.alive_sprites if i != p.active_index]
            if bench:
                idx, _ = self._best_matchup(battle, bench, opp)
                if idx >= 0:
                    return _switch_action(idx)

        # ── 3. 进攻：可负担攻击中取最高伤害（平手取低耗）──
        # energy_hold：无斩杀窗口时低于能量预算不泄招，攒大招（能量管理）
        if table:
            table.sort(key=lambda x: (-x[1], x[2]))
            i, dmg, cost = table[0]
            if dmg >= opp.current_hp * _WEAK_ATTACK_RATIO or (
                cost <= s.energy - 4 and s.energy >= st.energy_hold
            ):
                return _skill_action(i)

        # ── 4. 强化推队：缺乏有效输出且能量富余 → 用增益（状态）技能 ──
        if s.energy >= 3:
            best_buff, best_n = -1, 0
            for i, skill in enumerate(s.skills):
                if skill.cooldown > 0 or skill.sealed:
                    continue
                if skill.energy_cost > s.energy or skill.is_attack or skill.is_defense:
                    continue
                n_stat = sum(1 for e in skill.effects if e.kind == 'stat')
                if n_stat > best_n:
                    best_n = n_stat
                    best_buff = i
            if best_buff >= 0:
                return _skill_action(best_buff)

        # ── 5. 残血换位（对位更优才换；触发式，非无谓换人）──
        if hp_ratio < st.switch_hp:
            bench = [i for i in p.alive_sprites if i != p.active_index]
            if bench:
                idx, bench_dmg = self._best_matchup(battle, bench, opp)
                my_dmg = table[0][1] if table else 0
                if idx >= 0 and bench_dmg > my_dmg * 1.3:
                    return _switch_action(idx)

        # ── 6. 兜底：聚能攒爆发 ──
        return _GATHER_ACTION

    def choose_replacement(self, battle) -> int:
        """力竭换宠：最强攻击伤害 + 生存；preserve 精灵降权（无斩杀时不当炮灰）。"""
        p = self.player
        opponent = battle.get_opponent(self.team).active
        alive = [i for i in p.alive_sprites if i != p.active_index]
        best_idx, best_score = -1, -1.0
        for idx in alive:
            sprite = p.team[idx]
            dmg, _, _ = self._best_hit(battle, sprite, opponent)
            score = dmg + sprite.current_hp * 0.1
            if self._st(sprite).preserve and dmg < opponent.current_hp:
                score *= 0.5
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx if best_idx >= 0 else (alive[0] if alive else -1)

    def on_game_end(self, winner: str) -> None:
        pass
