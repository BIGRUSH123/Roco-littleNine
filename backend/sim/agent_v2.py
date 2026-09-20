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

道具口径（2026-09-20 改，判据集中在 `item_policy.py`）：道具**不消耗回合**，
所以「先道具、再出招」在同一回合内完成——进化之力只要当前场上首领血脉精灵可用
就立刻首领化（不再限定 turn <= 2）；愿力只在换出的血脉技能比本回合最强攻击更疼
或能斩杀时才用。旧的"进化之力开局 / 愿力残血就用"让七成首领队整局变不了身、
愿力也常在换人前白白用掉。

战术预判（`tactics.py`，全部只算不落伤）：出手顺序按**先手值 → 印记减速后的速度**
判定（不再是裸速度比较）；斩杀包含"我这招 + 星陨印记追加伤害"的组合杀；
换宠会跳过"上场就被印记进场伤害打死"的替补；会用"对面留场必死 + 有替补"
判断对手要撤人（它不会来打我，也不必为它花道具），以及"我这回合结束会被 tick 死"
时主动换人躲掉。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import ev
from .action import Action
from .agent import _GATHER_ACTION, _ITEM_ACTION, _skill_action, _switch_action
from .battleskill import SkillUse
from .item_policy import should_evolve, wish_decision
from .sprite import Sprite
from .tactics import (
    moves_first,
    opponent_likely_switch,
    opponent_team,
    predict_turn_end_damage,
    starfall_bonus,
    switch_in_damage,
)

_WEAK_ATTACK_RATIO = 0.12  # 最强攻击 < 12% 对手 HP 视为"缺乏有效输出"

# 交换价值（蒸馏自决策审计 `native/tools/audit_ruleagent_decisions.py`）：
# 能一击斩杀、但我出手前会被打死时，旧版一律撤人 —— 那等于"拿残的换好的也不敢打"。
# 只有"我在用更健康的一只去换它更残的一只"才该撤；反过来（它的血量比例比我高
# 15 个百分点以上）就该吃下这个一换一。`preserve` 精灵血量还够时除外。
_TRADE_MARGIN = 0.15       # 它比我健康多少（血量比例差）才值得换
_PRESERVE_HOLD_RATIO = 0.35  # 必保精灵血量高于此比例时不做一换一
_ANTI_SWITCH_LOOP = True   # 刚换上来的那只不参与"残血换位"，避免换出去又换回来

# 防御（应对攻击）：职业复盘里"防御/状态/应对"是胜负基础，而我们的专家出招里防御占 0%
# （配装里却有 17% 防御技、减伤 0.7~1.0）。实测激励：一个粗糙的"被威胁就防御"对手
# 对我们出厂专家胜率 **0.571 [0.511,0.631]**（300 局配对）→ 这一层被我们整个漏掉了。
# 规则：对手这一击够疼（≥ 阈值×我最大生命）、我打不死它、也没更适合的换人 → 举盾。
_DEFEND_THRESHOLD = 0.30   # 0 = 关闭（A/B 对照用）


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
    # ── 概率预判 / 期望值（E3，见 backend/sim/ev.py）──
    # 默认**关闭**：2000 局配对实测期望值层 0.476 [0.454,0.498] 略低于旧启发式，
    # 调参（`native/tools/tune_ev_params.py`）过拟合搜索种子、在未见过种子上更差（0.453）。
    # 想用/想继续调就显式开：`SpriteStrategy(ev_decide=True)`，或把校准好的
    # `checkpoints/ev_tuning.json` 的 `best_cfg` 传给 `RuleAgentV2(..., ev_params=…)`。
    ev_decide: bool = False
    risk_lambda: float = 0.0   # 风险厌恶：期望值 − λ·(最好格 − 最坏格)
    mix_temperature: float = 0.0    # >0 → 按 softmax(EV/T) 混合出招（实测有害，别开）

    # ── 逐规则 A/B 开关（默认取模块级常量，可逐策略实例覆盖）──
    # 模块级常量是全进程共享的：**一局里两侧读到的是同一个值**，所以靠改模块常量
    # 只能比较"整局用新口径"与"整局用旧口径"两个镜像，量到的是先手/侧别偏差，不是
    # 规则差异（`eval_expert_change.py` 旧版就是这么写错的）。要真正对照，必须把值
    # 放在策略实例上，让同一局的两侧用不同值。
    trade_margin: float = field(default_factory=lambda: _TRADE_MARGIN)
    anti_switch_loop: bool = field(default_factory=lambda: _ANTI_SWITCH_LOOP)
    defend_threshold: float = field(default_factory=lambda: _DEFEND_THRESHOLD)


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

    def __init__(self, team: str, player, strategy: TeamStrategy | None = None,
                 ev_params: "ev.EVParams | None" = None,
                 belief_params: "belief.BeliefParams | None" = None):
        self.team = team
        self.player = player
        self.strategy = strategy or TeamStrategy()
        # 校准过的收益/信念参数（None = 用 `ev.EVParams()` / `belief.BeliefParams()` 的默认值）。
        # 调参工具 `native/tools/tune_ev_params.py` 就是从这里注入候选参数。
        self.ev_params = ev_params
        self.belief_params = belief_params
        # 最近一次决策的期望值/信念（审计与测试用；不影响行为）
        self.last_ev: dict | None = None
        # 信念注入点：可调用对象 (battle) -> {列: 概率}，用于剥削者/校准模型覆盖
        # `belief.py` 的手设先验（None = 用先验）。见 native/tools/eval_prediction_mix.py。
        self.belief_provider = None

    def _st(self, sprite: Sprite) -> SpriteStrategy:
        return self.strategy.for_species(sprite.name)

    def _safe_bench(self, battle, player) -> list[int]:
        """可安全上场的替补索引：跳过换上场就会被印记进场伤害打死的（纯函数可算）。"""
        out = []
        for idx in player.alive_sprites:
            if idx == player.active_index:
                continue
            target = player.team[idx]
            if switch_in_damage(battle, self.team, target) < target.current_hp:
                out.append(idx)
        return out

    # ── 期望值决策（E3）──

    def _ev_candidates(self, battle, s, opp, table, st: SpriteStrategy) -> list:
        """EV 层的候选动作：够格的攻击技 + 其他可用技能 + 安全替补 + 聚能。

        攻击技沿用旧的能量纪律（`_WEAK_ATTACK_RATIO` / `energy_hold`）作为**过滤器**：
        打不动又耗光的攻击不进候选，剩下的（打哪个技能 / 强化 / 换人 / 聚能）交给期望值。
        """
        out: list[ev.Candidate] = []
        attack_idx = {i for i, _dmg, _cost in table}
        for i, dmg, cost in table:
            if dmg >= opp.current_hp * _WEAK_ATTACK_RATIO or (
                    cost <= s.energy - 4 and s.energy >= st.energy_hold):
                out.append(ev.Candidate('skill', i, s.skills[i].name))
        for i, skill in enumerate(s.skills):
            if i in attack_idx or skill.cooldown > 0 or skill.sealed:
                continue
            if skill.energy_cost > s.energy:
                continue
            out.append(ev.Candidate('skill', i, skill.name))
        for idx in self._safe_bench(battle, self.player):
            out.append(ev.Candidate('switch', idx, f"→{self.player.team[idx].name}"))
        out.append(ev.Candidate('gather', 0, "聚能"))
        return out

    def _candidate_action(self, cand: ev.Candidate) -> Action:
        if cand.kind == 'skill':
            return _skill_action(cand.index)
        if cand.kind == 'switch':
            return _switch_action(cand.index)
        return _GATHER_ACTION

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
        opp_player = battle.get_opponent(self.team)
        opp = opp_player.active
        opp_team = opponent_team(self.team)
        st = self._st(s)

        # 力竭 → 强制换宠
        if s.is_fainted:
            replacement = p.find_replacement()
            if replacement is not None:
                return _switch_action(replacement)
            return _GATHER_ACTION

        # ── 道具·进化之力（**不消耗回合**：结算后引擎会重新让本 agent 选行动，
        #    所以「先进化、再出招」在同一回合内完成；判据见 item_policy）──
        item = p.item
        if (item and item.can_use(battle.turn) and item.name == '进化之力'
                and should_evolve(battle, self.team, s)):
            # 首领化没有代价（技能槽不变、六维按首领形态重算、不耗回合），
            # 能变就变——旧规则的 turn <= 2 窗口让七成首领队整局变不了身。
            return _leader_form_action(battle, self.team, s, opp)

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
        # 出手顺序按引擎口径：先手值 → 印记减速后的速度（旧版只比裸速度）
        my_speed_first = moves_first(battle, s, self.team, opp, opp_team)
        hp_ratio = s.current_hp / max(1, s.max_hp)
        best_dmg = max((dmg for _i, dmg, _c in table), default=0)
        # 回合末固定伤害（异常 tick + 印记末伤）的致死预判，只算不落伤
        my_tick_death = 0 < s.current_hp <= predict_turn_end_damage(battle, s, self.team)
        opp_tick_death = 0 < opp.current_hp <= predict_turn_end_damage(battle, opp, opp_team)
        # 对手大概率撤人（留场必死 + 有替补）→ 它这回合不会来打我，也不必为它花道具
        opp_reacting = opponent_likely_switch(battle, self.team, opp_player, best_dmg)

        # ── 1. 斩杀优先（快攻体系）──
        # 含"我这招 + 星陨印记追加伤害 = 斩杀"的组合杀（印记追加伤害不吃技能威力）
        # 但若**对手先手且这一击就能杀我**，我根本没机会出手，斩杀是幻影 → 交给规则 2 撤人
        dies_before_acting = opp_best >= s.current_hp > 0 and not my_speed_first
        kills = []
        for i, dmg, cost in table:
            total = dmg + starfall_bonus(battle, s, opp, s.skills[i], opp_team)
            if total >= opp.current_hp > 0:
                kills.append((i, total, cost))
        if kills and not dies_before_acting:
            kills.sort(key=lambda x: (x[2], -x[1]))
            return _skill_action(kills[0][0])
        # 斩杀可用但"对手先手且能杀我"：不是无脑撤——先算**交换价值**
        # （决策审计实测：2.8% 的决策点在这里白丢斩杀，很多局面其实是划算的一换一）
        if kills and dies_before_acting:
            their_ratio = opp.current_hp / max(1, opp.max_hp)
            trade_ok = (their_ratio - hp_ratio) >= st.trade_margin
            if trade_ok and not (st.preserve and hp_ratio > _PRESERVE_HOLD_RATIO):
                kills.sort(key=lambda x: (x[2], -x[1]))
                return _skill_action(kills[0][0])

        # 道具·愿力：血脉技能能斩杀 → 同一回合内「先愿力、再放招」（不耗回合）；
        # 对面这回合要撤人时不花——换出的技能持续时间只有本回合，打在换入者身上是浪费
        wish = (wish_decision(battle, self.team, s, opp, best_dmg)
                if item and item.can_use(battle.turn) and item.name == '愿力'
                and not opp_reacting else '')
        if wish == 'kill' and not dies_before_acting:
            return _ITEM_ACTION

        # ── 2.5 防御（应对攻击）：被重击且打不死它时举盾 ──
        # 依据：三份洛神杯复盘里"防御/状态/应对"是胜负基础（一场里应对 2:2 vs 0:3 直接定胜负），
        # 而我们专家的防御出招占比是 **0%**——配装里却有 17% 防御技、减伤 0.7~1.0。实测：
        # 只加"被威胁就防御"的粗糙对手，对我们出厂专家 **0.571 [0.511,0.631]**（300 局）；
        # 把这条放进专家后对手降到 0.545（撤人之后再防御时）→ 说明**顺序也得对**：
        # 防御挡的是本回合的伤害，撤人却把伤害转给换上来的那只（还吃印记进场伤害），
        # 所以"该防就防"要排在撤人**之前**。
        if st.defend_threshold > 0 and not kills:
            if opp_best >= s.current_hp * st.defend_threshold:
                defs = [sk for sk in s.skills
                        if sk.is_defense and sk.cooldown <= 0 and not sk.sealed
                        and sk.energy_cost <= s.energy]
                if defs:
                    from .ev import defense_reduction as _dr

                    best = max(defs, key=lambda sk: (_dr(sk), -sk.energy_cost))
                    return _skill_action(s.skills.index(best))

        # ── 2. 被杀威胁 / 回合末必死 → 换宠 ──
        # ① 对手最高先手值的攻击先于我出手且能杀我（先手值 + 速度判定）
        # ② 我这回合结束会被 tick 死（出手顺序救不了，换人正好躲掉）
        # 注：**不**用"对面大概率要撤"来抑制这里的换人——它先手时照样会打我，
        # 而它要撤的情形本就被 speed_ok（我更快 → 不需要换）挡掉了。
        speed_ok = (not my_speed_first) or st.preserve
        direct_threat = (opp_best >= s.current_hp > 0 and hp_ratio < st.threat_switch_hp
                         and speed_ok)
        if direct_threat or my_tick_death:
            bench = self._safe_bench(battle, p)
            if bench:
                idx, _ = self._best_matchup(battle, bench, opp)
                if idx >= 0:
                    return _switch_action(idx)

        # 愿力让本回合打得更疼 → 先换技能再出手（放在换宠之后：换来的技能只
        # 持续本回合，先换人再放等于白换，还吃掉一次次数与冷却）
        if wish == 'better':
            return _ITEM_ACTION

        # ── 3'. 期望值决策（E3）：打哪个技能 / 强化 / 换人 / 聚能，按"信念 × 收益矩阵"选 ──
        # 对手动作的概率来自 `belief.py`（手设先验 + 可校准），收益含应对三角
        # （防御反制攻击、攻击反制状态、状态反制防御）与印记/先手/换人进场代价。
        if st.ev_decide:
            candidates = self._ev_candidates(battle, s, opp, table, st)
            if candidates:
                params = (self.ev_params if self.ev_params is not None
                          else ev.EVParams(risk_lambda=st.risk_lambda))
                picked, evs, dist = ev.choose(
                    battle, s, p, opp_player, candidates, self.team,
                    params=params,
                    belief_params=self.belief_params,
                    temperature=st.mix_temperature, my_best_dmg=best_dmg,
                    rng=random,
                    belief_override=(self.belief_provider(battle)
                                     if self.belief_provider is not None else None),
                )
                self.last_ev = {"picked": str(picked), "evs": {str(k): v for k, v in evs.items()},
                                "belief": dist}
                return self._candidate_action(picked)

        # ── 3. 进攻（旧启发式，ev_decide=False 时的路径）：可负担攻击中取最高伤害 ──
        # energy_hold：无斩杀窗口时低于能量预算不泄招，攒大招（能量管理）
        if table:
            table.sort(key=lambda x: (-x[1], x[2]))
            i, dmg, cost = table[0]
            if dmg >= opp.current_hp * _WEAK_ATTACK_RATIO or (
                cost <= s.energy - 4 and s.energy >= st.energy_hold
            ):
                return _skill_action(i)

        # ── 3''. 防御（应对攻击）：被重击且打不死它时举盾 ──
        # 依据：三份洛神杯复盘里"防御/状态/应对"是胜负基础，而我们专家防御出招占比 0%
        # （配装却有 17% 防御技、减伤 0.7~1.0）。实测"被威胁就防御"的对手对出厂专家
        # 0.571 [0.511,0.631]（300 局）→ 必须把这一层补上。
        if st.defend_threshold > 0 and not kills and table is not None:
            threaten = opp_best >= s.max_hp * st.defend_threshold
            if threaten:
                defs = [sk for sk in s.skills
                        if sk.is_defense and sk.cooldown <= 0 and not sk.sealed
                        and sk.energy_cost <= s.energy]
                if defs:
                    from .ev import defense_reduction as _dr

                    best = max(defs, key=lambda sk: _dr(sk))
                    return _skill_action(s.skills.index(best))

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
        # 刚换上来的那只不参与：审计里"换出去又换回来"的空转占了 11% 的换人决策
        just_entered = battle.turn - getattr(s, 'entry_turn', 0) <= 1
        if hp_ratio < st.switch_hp and not (just_entered and st.anti_switch_loop):
            bench = self._safe_bench(battle, p)
            if bench:
                idx, bench_dmg = self._best_matchup(battle, bench, opp)
                my_dmg = table[0][1] if table else 0
                if idx >= 0 and bench_dmg > my_dmg * 1.3:
                    return _switch_action(idx)

        # ── 6. 兜底：聚能攒爆发 ──
        return _GATHER_ACTION

    def choose_replacement(self, battle) -> int:
        """力竭换宠：最强攻击伤害 + 生存；preserve 精灵降权（无斩杀时不当炮灰）。

        换上场会被印记进场伤害打死的替补直接跳过（`mark_switch_damage` 是纯函数）。
        """
        p = self.player
        opponent = battle.get_opponent(self.team).active
        alive = self._safe_bench(battle, p) or [
            i for i in p.alive_sprites if i != p.active_index]
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
