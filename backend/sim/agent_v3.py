"""backend/sim/agent_v3.py — RuleAgentV3：把社区 PVP 攻略的战术层落成规则流水线。

**这一版补的是 V2 缺的那几条**（来源见 `docs/洛克王国世界-PVP攻略要点.md`，
2026-09 网上检索；每条规则都在代码里标了出处）：

1. **主动轮转**（不是残血才换）："多次复盘显示，约 63% 的逆转胜局源于精准的精灵轮换
   决策，而非初始阵容强度优势"；且换人要有对位收益、要算换入者会不会白吃一击
   （洛神杯复盘里 B 换人 5 次被白打 426 伤害就是反面教材）。
2. **清强化 / 退化**：对手已经叠起增益时，优先用驱散/退化把它的赢点拆掉
   （"其余四只精灵承担联防职责：针对性抗伤、清除敌方强化 Buff、消耗对手能量条"）。
3. **能量压制**：对手能量将够放它的大招 / 已经很高时，优先抽能、加能耗、偷取
   （恶作剧=抽 6 能量、精神扰乱=全技能能耗 +3、封锁类）。
4. **叠层窗口**：对手这回合杀不掉我、我也打不死它时，用自增益叠层（"越打越强"的
   永动体系；泥浆铠甲/热身这类还有"应对成功增益翻倍"的强分支）。
5. **收割位**：`role='closer'` 的精灵不在开局/中期当炮灰，残局（对手残血或能量枯竭）
   才登场（"龙息帕尔与圣羽翼王定位为残局收割位"）。
6. **读牌**：前 2-3 回合统计对手行为（换人率 / 防御率 / 状态率 / 单回合最高伤害），
   分类成 速攻 / 控场 / 消耗，反过来调整自己的防守档位与叠层许可
   （"经验型玩家常通过前 2-3 回合试探性技能释放，判断对手是否倾向速攻、控场或消耗流派"）。
7. **打断 / 封锁**：对手蓄力中、或已暴露高耗大招时，用 interrupt/lock 拆节奏。

保留 V2 已经验证过的机制（不重写、直接复用）：斩杀（含星陨印记组合杀）、
"对手先手能杀我"时的交换价值判定、应对三角（防御读攻击 / 状态读防御）、
道具判据（`item_policy`）、规划层（`plan`）、印记进场伤害的安全替补。

**与 V2 的接口差异**：V3 不继承 V2，也不 import 它；策略配置走鸭子类型
（任何提供 `for_species(name) -> 对象` 的 TeamStrategy 都能直接用），
所以现有的队伍配置/BC 管线可以原样喂进来。默认参数下 V3 是"V2 的能力 + 上面 7 条"。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from . import plan
from .action import Action
from .agent import (
    _GATHER_ACTION, _ITEM_ACTION, _skill_action, _switch_action,
    SwitchStreak, best_self_buff_skill_index, first_usable_skill_index, gather_is_noop,
)
from .battleskill import SkillUse
from .item_policy import should_evolve, wish_decision
from .skill_ir import SkillProfile, skill_profile, total_buff_steps
from .sprite import Sprite
from .tactics import (
    moves_first,
    opponent_likely_switch,
    opponent_team,
    predict_turn_end_damage,
    starfall_bonus,
    switch_in_damage,
)

_WEAK_ATTACK_RATIO = 0.12          # 最强攻击 < 12% 对手 HP 视为"缺乏有效输出"（V2 同口径）
_PLAN_MAX_CANDIDATES = 7
_TRADE_MARGIN = 0.15               # 它比我健康多少（血量比例差）才值得做一换一
_PRESERVE_HOLD_RATIO = 0.35


@dataclass
class V3Params:
    """V3 新增规则的阈值（集中可调；None/0 = 关闭该规则）。"""

    # 防御读攻击：默认**关闭**（0）。仓库自己 2000 局同局配对实测过 V2 的
    # `defend_threshold=0.30`：0.457 [0.445,0.469]（-4.3 点）——被状态反制惩罚。
    # 所以 V3 只在"读牌判定对手是速攻"且这一击达到 lethal_ratio 时才举盾，
    # 且默认不开；想复测把它设成 0.35 之类即可。
    defend_lethal_ratio: float = 0.0

    # ① 主动轮转：我打不动（最强攻击 < 对手 HP 的该比例）才考虑主动换人
    rotate_weak_ratio: float = 0.18
    # 换入者要比我强多少倍才值得付 tempo（进攻收益倍数）
    rotate_gain: float = 1.35
    # 轮换后对手这回合的预计伤害不得达到换入者血量的该比例（否则白送）
    rotate_safe_ratio: float = 0.55
    # 刚换上来 1 回合内的不参与轮换（避免换出去又换回来）
    rotate_cooldown_turns: int = 1

    # ② 清强化：对手场上增益步数 ≥ 该值 → 优先驱散/退化
    dispel_buff_threshold: float = 8.0

    # ③ 能量压制：对手能量 ≥ 该值（快够放大招）或 "对手能量 ≤ 其大招能耗" 时优先抽能
    pressure_energy_high: int = 6
    # 我这回合的最强攻击 < 对手 HP 的该比例时，才愿意把出手让给功能技（否则先打输出）
    utility_max_damage_ratio: float = 0.35

    # ④ 叠层窗口：对手这回合最高伤害 < 我血量的该比例 → 安全，可以叠
    setup_safe_ratio: float = 0.65
    # 连续换人上限（0=关闭；防"双方无限轮转"僵局）
    max_consecutive_switches: int = 5
    # 叠层收益门槛（自身增益步数 ≥ 该值才算"值得花一回合"）
    setup_min_gain: float = 6.0

    # ⑤ 收割位：对手存活 ≥ 该数量时不派 closer 上场
    closer_hold_alive: int = 3

    # ⑥ 读牌：前 N 回合观察，之后每回合更新
    read_window: int = 3
    # 判定为"速攻"的对手单回合最高伤害 / 我最大 HP 阈值
    read_burst_ratio: float = 0.35
    # 判定为"控场"的状态/防御使用次数阈值（窗口内）
    read_control_count: int = 2

    # ⑦ 打断：对手蓄力中时优先打断；0 = 关闭
    interrupt_on_charge: bool = True


@dataclass
class _OpponentModel:
    """读牌：只统计**公开可观测**的对手行为，不做隐藏信息推断。

    数据源是引擎自己的回合记录（`battle.log[-1].action_a/action_b`）：技能类型、
    换人、道具都是台面上的。用途只有一个——判断对手偏速攻/控场/消耗，从而调整
    自己的防守档位与叠层许可（攻略："前 2-3 回合试探性技能释放，判断对手是否
    倾向速攻、控场或消耗流派"）。
    """

    turns: int = 0
    switches: int = 0
    defenses: int = 0
    statuses: int = 0
    max_hit_ratio: float = 0.0      # 它单回合打出的最高伤害 / 我方最大 HP

    def mode(self, window: int, burst_ratio: float, control_count: int) -> str:
        """'burst' | 'control' | 'stall' | 'unknown'（前 window 回合不下结论）。"""
        if self.turns < window:
            return "unknown"
        if self.max_hit_ratio >= burst_ratio:
            return "burst"
        if (self.defenses + self.statuses) >= control_count:
            return "stall"
        if self.switches >= 2:
            return "control"
        return "unknown"


class RuleAgentV3:
    """社区 PVP 攻略规则层（6v6）。"""

    def __init__(self, team: str, player, strategy: Any | None = None,
                 params: V3Params | None = None):
        self.team = team
        self.player = player
        self.strategy = strategy
        self.params = params or V3Params()
        self.opp_model = _OpponentModel()
        # 诊断（不影响行为）：最近一次决策走了哪条规则
        self.last_rule: str = ""
        # 连续换人计数（决策层状态，不进战斗快照）
        self._switches = SwitchStreak()
        self.last_read: str = "unknown"
        self._last_seen_turn: int = -1
        self._last_hp: int = 0
        self._last_active: str = ""

    # ── 策略配置（鸭子类型，未配置时用默认）──

    def _st(self, sprite: Sprite):
        if self.strategy is None:
            return None
        fn = getattr(self.strategy, "for_species", None)
        return fn(sprite.name) if callable(fn) else None

    def _role(self, sprite: Sprite) -> str:
        st = self._st(sprite)
        return str(getattr(st, "role", "attack") or "attack") if st is not None else "attack"

    def _flag(self, sprite: Sprite, name: str, default):
        st = self._st(sprite)
        return getattr(st, name, default) if st is not None else default

    def is_closer(self, sprite: Sprite) -> bool:
        return self._role(sprite) == "closer"

    # ── 通用计算（与 V2 同口径，避免两份实现漂移）──

    def _attack_table(self, battle, attacker: Sprite, defender: Sprite):
        """**合法**攻击技能的 (index, dmg, cost)（合法性 = 引擎唯一判据，同 V2）。"""
        table = []
        for i, skill in enumerate(attacker.skills):
            if not skill.is_attack:
                continue
            if not battle.action_legality(self.team, Action(kind='skill', skill_index=i)).ok:
                continue
            dmg, _ = battle._resolver.calc_damage(
                attacker, defender, SkillUse(battle_skill=skill, skill_index=i),
                battle.globals, attacker_team=self.team,
            )
            table.append((i, dmg, skill.energy_cost))
        return table

    def _best_hit(self, battle, attacker: Sprite, defender: Sprite):
        table = self._attack_table(battle, attacker, defender)
        if not table:
            return 0, 0, -1
        i, dmg, cost = max(table, key=lambda x: (x[1], -x[2]))
        return dmg, cost, i

    def _safe_bench(self, battle, player) -> list[int]:
        """可安全上场的替补：跳过"换上去就被印记进场伤害打死"的。"""
        out = []
        for idx in player.alive_sprites:
            if idx == player.active_index:
                continue
            target = player.team[idx]
            if switch_in_damage(battle, self.team, target) < target.current_hp:
                out.append(idx)
        return out

    def _usable(self, sprite: Sprite, idx: int):
        skill = sprite.skills[idx]
        if skill.cooldown > 0 or skill.sealed:
            return None
        if skill.energy_cost > sprite.energy:
            return None
        return skill

    # ── 读牌：从引擎回合记录里读对手上一回合干了什么 ──

    def _skill_type(self, battle, name: str) -> str:
        """技能名 → skill_type（物攻/魔攻/动态攻击/防御/状态）。"""
        getter = getattr(battle, "_get_skill_record", None)
        if getter is not None and name:
            try:
                return getter(name).skill_type or ""
            except (KeyError, FileNotFoundError, ValueError, AttributeError, TypeError):
                pass
        for player in (battle.player_a, battle.player_b):
            for sprite in player.team:
                for skill in sprite.skills or ():
                    if skill.name == name:
                        return skill.skill_type
        return ""

    def _observe(self, battle) -> None:
        turn = getattr(battle, "turn", 0)
        if turn == self._last_seen_turn:
            return
        self._last_seen_turn = turn

        entries = getattr(battle, "log", None) or []
        if entries:
            rec = entries[-1]
            their = getattr(rec, "action_b" if self.team == "A" else "action_a", None)
            if their is not None:
                self.opp_model.turns += 1
                kind = getattr(their, "kind", "")
                if kind == "switch":
                    self.opp_model.switches += 1
                elif kind == "skill":
                    stype = self._skill_type(battle, getattr(their, "skill_name", ""))
                    if stype == "防御":
                        self.opp_model.defenses += 1
                    elif stype == "状态":
                        self.opp_model.statuses += 1

        # 伤害观察：同一只精灵还在场上时，血量下降即它这一击的力度
        me = self.player.active
        if me is not None:
            if me.name == self._last_active and self._last_hp > me.current_hp:
                taken = self._last_hp - me.current_hp
                if me.max_hp > 0:
                    self.opp_model.max_hit_ratio = max(
                        self.opp_model.max_hit_ratio, taken / me.max_hp)
            self._last_active = me.name
            self._last_hp = me.current_hp

    # ── Agent 接口 ──

    def choose_lead(self, battle) -> int:
        """首发对位：伤害期望 + 速度 + 生存；closer 不当首发。"""
        p = self.player
        opp_lead = battle.get_opponent(self.team).active
        alive = [i for i, s in enumerate(p.team) if not s.is_fainted]
        leads = [i for i in alive if self._flag(p.team[i], "lead", False)]
        candidates = leads or [i for i in alive if not self.is_closer(p.team[i])] or alive
        best_idx, best_score = (candidates[0] if candidates else 0), -1e9
        for i in candidates:
            s = p.team[i]
            dmg, _, _ = self._best_hit(battle, s, opp_lead)
            dmg_ratio = dmg / max(1, opp_lead.current_hp)
            speed_win = 1 if s.effective_stat("speed") >= opp_lead.effective_stat("speed") else 0
            bulk = s.current_hp + s.effective_stat("def") + s.effective_stat("sp_def")
            score = dmg_ratio * 2.0 + speed_win * 0.2 + bulk / 1500.0
            if score > best_score:
                best_score, best_idx = score, i
        return best_idx

    def choose_action(self, battle):
        active = self.player.active
        forced = bool(getattr(active, "is_fainted", False))
        action = self._decide(battle)
        self._switches.note(battle, action, forced=forced)
        return action

    def _decide(self, battle):
        self._observe(battle)
        p = self.player
        s = p.active
        opp_player = battle.get_opponent(self.team)
        opp = opp_player.active
        opp_team = opponent_team(self.team)
        pr = self.params

        if s is None:
            return _GATHER_ACTION

        # 力竭 → 强制换宠（收割位保护）
        if s.is_fainted:
            idx = self._pick_replacement(battle, opp)
            self.last_rule = "faint_switch"
            return _switch_action(idx) if idx >= 0 else _GATHER_ACTION

        # ── 道具·进化之力（不耗回合）──
        item = p.item
        if (item and item.can_use(battle.turn) and item.name == "进化之力"
                and should_evolve(battle, self.team, s)):
            return self._leader_form_action(battle, s, opp)

        # ── 蓄力（引擎语义）──
        if getattr(s, "_charging", False):
            charged_idx, charged_skill = battle._charged_skill(s)
            if charged_skill is not None:
                self.last_rule = "release_charge"
                return _skill_action(charged_idx)
            rep = self._pick_replacement(battle, opp)
            self.last_rule = "charge_switch"
            return _switch_action(rep) if rep >= 0 else _GATHER_ACTION

        table = [(i, dmg, cost) for (i, dmg, cost) in self._attack_table(battle, s, opp)
                 if cost <= s.energy]
        opp_best, _, _ = self._best_hit(battle, opp, s)
        my_speed_first = moves_first(battle, s, self.team, opp, opp_team)
        hp_ratio = s.current_hp / max(1, s.max_hp)
        best_dmg = max((dmg for _i, dmg, _c in table), default=0)
        my_tick_death = 0 < s.current_hp <= predict_turn_end_damage(battle, s, self.team)
        opp_reacting = opponent_likely_switch(battle, self.team, opp_player, best_dmg)
        mode = self.opp_model.mode(pr.read_window, pr.read_burst_ratio, pr.read_control_count)
        self.last_read = mode

        may_switch = not self._switches.blocked(pr.max_consecutive_switches)

        def emit(rule: str, action: Action) -> Action:
            self.last_rule = rule
            return action

        # ── 1. 斩杀优先（含印记组合杀）──
        dies_before_acting = opp_best >= s.current_hp > 0 and not my_speed_first
        kills = []
        for i, dmg, cost in table:
            total = dmg + starfall_bonus(battle, s, opp, s.skills[i], opp_team)
            if total >= opp.current_hp > 0:
                kills.append((i, total, cost))
        if kills and not dies_before_acting:
            kills.sort(key=lambda x: (x[2], -x[1]))
            return emit("kill", _skill_action(kills[0][0]))
        if kills and dies_before_acting:
            their_ratio = opp.current_hp / max(1, opp.max_hp)
            if (their_ratio - hp_ratio) >= _TRADE_MARGIN and not (
                    self._flag(s, "preserve", False) and hp_ratio > _PRESERVE_HOLD_RATIO):
                kills.sort(key=lambda x: (x[2], -x[1]))
                return emit("trade_kill", _skill_action(kills[0][0]))

        # ── 2. 应对三角（读对手这一回合在做什么）──
        # ②a 防御读攻击：读牌判定它偏速攻/控场 **且** 这一击达到 lethal_ratio 才举盾。
        # 默认关闭：仓库自己量过"被重击就防御"是 -4.3 点（被状态反制惩罚，见 V3Params）。
        if (pr.defend_lethal_ratio > 0 and mode in ("burst", "control") and not kills
                and opp_best >= s.current_hp * pr.defend_lethal_ratio):
            defs = [i for i in range(len(s.skills))
                    if (sk := self._usable(s, i)) is not None and sk.is_defense]
            if defs:
                return emit("defend_read", _skill_action(defs[0]))
        # ②b 状态读防御：我这一击够疼 → 预期它举盾 → 用"应对防御"技吃强分支
        if not kills and table:
            threatened = best_dmg >= max(1, opp.current_hp) * 0.30
            their_shield = [sk for sk in opp.skills
                            if sk.is_defense and sk.cooldown <= 0 and not sk.sealed
                            and sk.energy_cost <= opp.energy]
            if threatened and their_shield:
                cands = [(i, skill_profile(battle, s.skills[i]))
                         for i in range(len(s.skills))
                         if (sk := self._usable(s, i)) is not None and sk.counter == "防御"]
                cands = [(i, prof) for i, prof in cands
                         if prof.self_buff_on_counter > 0 or prof.abnorms or prof.opp_debuff_value > 0]
                if cands:
                    i, _prof = max(cands, key=lambda x: (
                        x[1].self_buff_on_counter + x[1].opp_debuff_value
                        + sum(v for _n, v in x[1].abnorms), -s.skills[x[0]].energy_cost))
                    return emit("counter_shield", _skill_action(i))

        # 注：清强化 / 能量压制 / 叠层这三条**不做独立硬规则**，而是进规划层的候选权重
        # （`_plan_candidates`）。理由是实测：手写规则抢在规划层前面会把它压掉 ——
        # 第一版 V3 把这三条放在规划层之前，同局配对 A/B 直接 9:21 输给 V2。
        # 规划层（一回合 rollout + 效果感知叶子）本身就是"这条功能技值不值得花一回合"
        # 的裁决者，把攻略意图做成候选/权重比做成硬优先级更稳。

        # ── 3. 打断 / 封锁：对手蓄力中（明确的窗口期）──
        if pr.interrupt_on_charge and getattr(opp, "_charging", False):
            for i in range(len(s.skills)):
                sk = self._usable(s, i)
                if sk is None:
                    continue
                prof = skill_profile(battle, sk)
                if prof.interrupts or prof.locks:
                    return emit("interrupt", _skill_action(i))

        # ── 6. 被杀威胁 / 回合末必死 → 撤人 ──
        speed_ok = (not my_speed_first) or self._flag(s, "preserve", False)
        direct_threat = (opp_best >= s.current_hp > 0
                         and hp_ratio < self._flag(s, "threat_switch_hp", 0.9) and speed_ok)
        if direct_threat or my_tick_death:
            idx = self._pick_replacement(battle, opp)
            if idx >= 0:
                return emit("retreat", _switch_action(idx))

        # 愿力（能斩杀 → 同回合先愿力再放招）
        wish = (wish_decision(battle, self.team, s, opp, best_dmg)
                if item and item.can_use(battle.turn) and item.name == "愿力"
                and not opp_reacting else "")
        if wish == "kill" and not dies_before_acting:
            return emit("wish_kill", _ITEM_ACTION)

        if wish == "better":
            return emit("wish_better", _ITEM_ACTION)

        # ── 4. 规划层（与 V2 同口径：一回合 rollout + 效果感知叶子）──
        # 候选里已经带上了攻略的战术意图（驱散/抽能/打断/自增益的效用权重 + 健康替补），
        # 所以"清强化/能量压制/叠层/对位轮转"这四条的落地位置就是这里。
        plan_depth = int(self._flag(s, "plan_depth", 1) or 0)
        if plan_depth > 0:
            cands = self._plan_candidates(battle, s, table, opp)
            picked, _info = plan.choose(
                battle, self.team, cands, plies=plan_depth,
                k_responses=int(self._flag(s, "plan_responses", 3) or 3), rng=random)
            if picked is not None:
                self.last_rule = "plan"
                return picked

        # ── 5. 无规划层时的兜底：进攻 → 叠层 → 主动轮转 → 聚能 ──
        if table:
            table.sort(key=lambda x: (-x[1], x[2]))
            i, dmg, cost = table[0]
            hold = int(self._flag(s, "energy_hold", 0) or 0)
            if dmg >= opp.current_hp * _WEAK_ATTACK_RATIO or (
                    cost <= s.energy - 4 and s.energy >= hold):
                return emit("attack", _skill_action(i))

        setup = self._try_setup(battle, s, opp, opp_best, mode)
        if setup is not None:
            return emit("setup", _skill_action(setup))

        rotate = (self._try_rotate(battle, s, opp, opp_player, best_dmg, hp_ratio)
                  if may_switch else None)
        if rotate is not None:
            return emit("rotate", _switch_action(rotate))

        # 满能量时"聚能"是空操作（+0 能量、白丢一回合）→ 能出招就出招
        if gather_is_noop(s):
            idx = first_usable_skill_index(battle, self.team, s)
            if idx >= 0:
                return emit("attack", _skill_action(idx))

        return emit("gather", _GATHER_ACTION)

    # ── 子规则 ──

    def _leader_form_action(self, battle, sprite: Sprite, opp) -> Action:
        try:
            candidates = battle.item_variants(self.team)
        except AttributeError:
            return _ITEM_ACTION
        if len(candidates) <= 1:
            return _ITEM_ACTION
        opp_elements = tuple(getattr(opp.species, "elements", ()) or ())
        if not opp_elements:
            return _ITEM_ACTION
        from .resolver import _TYPE_CHART

        best_idx, best_score = 0, float("-inf")
        for idx, species in enumerate(candidates):
            score = 0.0
            for my_elem in (species.elements or ()):
                chart = _TYPE_CHART.get(my_elem, {})
                score += sum(chart.get(opp_elem, 1.0) for opp_elem in opp_elements)
            if score > best_score:
                best_idx, best_score = idx, score
        return _ITEM_ACTION if best_idx == 0 else Action(kind="item", variant=best_idx)

    def _try_rotate(self, battle, s: Sprite, opp, opp_player, best_dmg: int,
                    hp_ratio: float) -> int | None:
        """主动轮转（兜底路径）：换到对位明显更好的替补上。返回替补索引或 None。

        只有在我**完全**打不动时才主动换：换人要吃 tempo，且换入者会挨一击
        （洛神杯复盘里 B 换人 5 次白吃 426 伤害）。规划层在时这条不会触发。
        """
        pr = self.params
        _ = opp_player
        if pr.rotate_gain <= 0:
            return None
        if best_dmg >= opp.current_hp * pr.rotate_weak_ratio:
            return None                      # 我打得动，先打
        if self._flag(s, "preserve", False) and hp_ratio > _PRESERVE_HOLD_RATIO:
            return None
        # 对手留场必死（被 tick / 我斩杀线内）→ 不值得为它付 tempo
        if 0 < opp.current_hp <= predict_turn_end_damage(battle, opp, opponent_team(self.team)):
            return None
        just_entered = battle.turn - getattr(s, "entry_turn", 0) <= pr.rotate_cooldown_turns
        if just_entered:
            return None
        opp_best, _, _ = self._best_hit(battle, opp, s)
        bench = [i for i in self._safe_bench(battle, self.player)
                 if not self._is_held_closer(battle, self.player.team[i])]
        if not bench:
            return None
        best_idx, best_dmg_bench = -1, -1.0
        for idx in bench:
            cand = self.player.team[idx]
            dmg, _, _ = self._best_hit(battle, cand, opp)
            # 换入者会被对手这回合打死 → 白送（复盘的教训）
            incoming = opp_best
            if incoming >= cand.current_hp * pr.rotate_safe_ratio:
                continue
            if dmg > best_dmg_bench:
                best_idx, best_dmg_bench = idx, dmg
        if best_idx < 0:
            return None
        if best_dmg_bench >= max(1.0, best_dmg) * pr.rotate_gain:
            return best_idx
        return None

    def _try_setup(self, battle, s: Sprite, opp, opp_best: int, mode: str) -> int | None:
        """叠层窗口：安全时用自增益技能。返回技能索引或 None。"""
        pr = self.params
        if pr.setup_min_gain <= 0:
            return None
        if mode == "burst":
            return None                      # 对面速攻，叠层会被打崩
        if opp_best >= s.current_hp * pr.setup_safe_ratio:
            return None                      # 它这回合够疼，先防守/换人/输出
        if s.energy < 3:
            return None
        return best_self_buff_skill_index(
            battle, s, min_gain=pr.setup_min_gain,
            usable=lambda i, _sk: self._usable(s, i) is not None) or None

    def _is_held_closer(self, battle, sprite: Sprite) -> bool:
        """收割位保护：对手还活着 ≥ closer_hold_alive 只时不派它上场。"""
        if not self.is_closer(sprite):
            return False
        opp_alive = len([x for x in battle.get_opponent(self.team).team if not x.is_fainted])
        return opp_alive >= self.params.closer_hold_alive

    def _pick_replacement(self, battle, opp) -> int:
        """力竭/撤人时的替补选择：对位伤害 + 生存；closer 在场数够时不早登场。"""
        p = self.player
        bench = self._safe_bench(battle, p) or [i for i in p.alive_sprites
                                               if i != p.active_index]
        if not bench:
            return -1
        # 残局（对手场上残血）且 closer 能一击斩杀 → 直接上收割位
        best_idx, best_score = -1, -1.0
        for idx in bench:
            cand = p.team[idx]
            dmg, _, _ = self._best_hit(battle, cand, opp)
            score = dmg + cand.current_hp * 0.1
            if self._is_held_closer(battle, cand):
                score *= 0.5
            elif self.is_closer(cand) and dmg >= opp.current_hp > 0:
                score += dmg                 # 收割位能斩杀 → 立即可用
            if self._flag(cand, "preserve", False) and dmg < opp.current_hp:
                score *= 0.5
            if score > best_score:
                best_score, best_idx = score, idx
        return best_idx if best_idx >= 0 else bench[0]

    def _utility_score(self, battle, prof: SkillProfile, s: Sprite, opp, mode: str) -> float:
        """攻略战术的**情境权重**：同一个功能技在不同局面下价值不同。

        - 驱散/退化：对手身上增益越多越值（"清除敌方强化 Buff"）。
        - 抽能/加能耗/偷取：对手能量越接近大招线越值（"消耗对手能量条"）。
        - 自增益：它这回合打不疼我时才有窗口（否则叠了就被打崩）；对手是消耗流派更值。
        - 回血/吸血：我血线越低越值。
        """
        val = 0.0
        opp_buffs = total_buff_steps(opp)
        if prof.dispels_opp and opp_buffs > 0:
            val += 2.0 + opp_buffs * 0.12
        if prof.opp_debuff_value > 0:
            val += prof.opp_debuff_value * 0.15
        if prof.opp_energy_drain:
            val += prof.opp_energy_drain * (1.5 if opp.energy >= self.params.pressure_energy_high
                                            else 0.4)
        if prof.opp_energy_cost_pressure:
            val += prof.opp_energy_cost_pressure * (1.2 if opp.energy >=
                                                    self.params.pressure_energy_high else 0.3)
        if prof.steals:
            val += 1.5
        if prof.self_buff_value > 0:
            opp_best, _, _ = self._best_hit(battle, opp, s)
            safe = opp_best < s.current_hp * self.params.setup_safe_ratio
            if safe or mode == "stall":
                val += prof.self_buff_value * 0.5
            elif prof.doubles_buffs:
                val += prof.self_buff_value * 0.2
        if prof.heals or prof.life_drain:
            val += 2.0 * (1.0 - s.current_hp / max(1, s.max_hp))
        if prof.interrupts or prof.locks:
            val += 1.0
        return val

    def _plan_candidates(self, battle, s: Sprite, table, opp) -> list:
        """规划层的候选：**全部**可用技能 + 健康替补 + 聚能，按"即时伤害 + 情境效用"粗排。

        与 V2 的关键差别：V2 的排序项是 `20.0 * len(skill.effects)`，而 IR 语料的
        `skill.effects` 是空的（见 `skill_ir` 模块说明）→ 那条排序实际只剩伤害，功能技
        在有 ≥7 个攻击技时会被截掉。这里换成按 IR 字段算的情境效用，功能技才进得了候选。
        """
        attack_dmg = {i: dmg for i, dmg, _cost in table}
        mode = self.last_read
        my_best = max((dmg for _i, dmg, _c in table), default=1)
        attacks: list[tuple[float, object]] = []
        others: list[tuple[float, object]] = []
        for i, skill in enumerate(s.skills):
            if skill.cooldown > 0 or skill.sealed or skill.energy_cost > s.energy:
                continue
            prof = skill_profile(battle, skill)
            utility = self._utility_score(battle, prof, s, opp, mode)
            score = float(attack_dmg.get(i, 0)) + 6.0 * utility
            (attacks if skill.is_attack else others).append((score, _skill_action(i)))
        # 攻击技**一律进候选**：只按总分裁剪会把低伤攻击挤出去，规划层就失去了"这回合
        # 打输出"这个选项（第一版按总分取前 7，实测 0.465 → 加这条再测）。
        scored = sorted(attacks, key=lambda x: -x[0]) + sorted(others, key=lambda x: -x[0])
        # 换人候选：分数必须与"出招"同量纲（伤害）。给一个**上限很低**的对位比值分，
        # 否则换人会挤掉出招候选 —— 第一版按 `dmg*0.6` 算，换人分数直接压过技能，
        # 规划层于是偏好换人，A/B 立刻掉到 0.42。
        opp_best, _, _ = self._best_hit(battle, opp, s)
        # 连续换人到上限 → 本轮不再提供换人候选（防"双方无限轮转"的僵局）
        if not self._switches.blocked(self.params.max_consecutive_switches):
            for idx in self._safe_bench(battle, self.player):
                cand = self.player.team[idx]
                dmg, _, _ = self._best_hit(battle, cand, opp)
                if opp_best >= cand.current_hp * self.params.rotate_safe_ratio:
                    continue                  # 换上去就被打死，不进候选
                if self._is_held_closer(battle, cand):
                    continue                  # 收割位留着
                gain = dmg / max(1.0, float(my_best))
                scored.append((min(20.0, 10.0 * gain), _switch_action(idx)))
        cap = max(_PLAN_MAX_CANDIDATES, len(attacks) + 1)
        out = [act for _score, act in scored[:cap]]
        if not gather_is_noop(s):
            out.append(_GATHER_ACTION)
        return out

    def choose_replacement(self, battle) -> int:
        opp = battle.get_opponent(self.team).active
        return self._pick_replacement(battle, opp)

    def on_game_end(self, winner: str) -> None:
        pass
