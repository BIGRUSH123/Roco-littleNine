# -*- coding: utf-8 -*-
"""铁头海豹平衡队专用专家 —— 围绕"全场连击=2 / 治疗机器人 / 应对喂船长"。

机制链（`data/skills` + `data/traits`）：
- **泥吼牙「无差别过滤」**：在场时**所有精灵连击数固定为 2**（双向）——低威多段／需要段数的技能收益翻倍；
  它自己还带 `羽刃`（翼/物攻 75，应对状态：回合结束使敌方**紧急脱离**）。
- **圣剑-X「正位宝剑」**：入场时**封 2 号位起** → 只剩 1 号位 `休息回复`（自己回复 30%）→ 它是**治疗机器人**，
  通用专家会把它当普通精灵上场，白丢回合（这队 34% 的聚能、97/120 平就是这么来的）。
- **海豹船长「身经百练」**：己方**每应对成功 1 次**，它入场时水系/武系技能威力 **+20%**（叠加）→ 越打越强。
- **圆号鱼「泛音列」**：使用**状态技能后**，敌方获得「聒噪」效果 3 回合（干扰维持）。
- **荆棘电环「防过载保护」**：每次行动后**自动脱离**（天然的一次性干扰手）。
- **冰钻布鲁斯「冰钻」**：敌方携带技能总能耗每 1 点 → 自己攻击威力 +10%（对手越"重"越强）。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action

FILTER = "泥吼牙"              # 无差别过滤：全场连击=2
HEAL_BOT = "圣剑-X"            # 正位宝剑：只剩 1 号位（治疗）
ROUND_FISH = "圆号鱼"          # 泛音列：状态技 → 敌方聒噪
COUNTER_DEFENSE = ("听桥", "有效预防")     # 应对攻击后给"应对成功"计数
THREAT_RATIO = 0.25
HEAL_BOT_SAFE_HP = 0.45        # 我方血线高于此值时，不主动把治疗机器人换上场


class IronSealExpert(RuleAgentV2):
    """铁头海豹：守住连击放大器、别浪费回合在治疗机器人身上。"""

    # 实测（120 局/档）：heal_bot_guard 0.505（胜负中性）但**平局 90→19**（解僵局）；
    # hold_filter 0.484（惰性）、cry_loop 0.414 / counter_scale 0.346（有害）→ 默认只留 heal_bot_guard。
    RULES = ("heal_bot_guard",)
    OPTIONAL_RULES = ("hold_filter", "cry_loop", "counter_scale")

    def __init__(self, *a, rules=None, **kw):
        super().__init__(*a, **kw)
        self.rules = set(self.RULES if rules is None else rules)
        self.last_rule = ""

    # ── 工具 ──────────────────────────────────────────────────────
    @property
    def _opp_team(self) -> str:
        return "B" if self.team == "A" else "A"

    def _legal(self, battle, i: int) -> bool:
        return battle.action_legality(self.team, Action(kind='skill', skill_index=i)).ok

    def _find(self, s, *names) -> int:
        for i, sk in enumerate(s.skills):
            if sk.name in names:
                return i
        return -1

    def _threatened(self, battle, s, opp) -> bool:
        dmg, _c, _i = self._best_hit(battle, opp, s)
        return dmg >= max(1, s.current_hp) * THREAT_RATIO

    def _enemy_has_cry(self, battle) -> bool:
        """敌方场上是否已带「聒噪」（泛音列给的干扰效果）。"""
        try:
            for eff in battle.get_opponent(self.team).active.active_effects:
                if getattr(eff, "name", "") == "聒噪":
                    return True
        except AttributeError:
            pass
        return False

    # ── 决策 ──────────────────────────────────────────────────────
    def _decide(self, battle):
        p = self.player
        s = p.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)

        # R1 应对喂船长：它要打的时候用"应对攻击"的防御技（顺带喂海豹船长的 +20%/次）
        if "counter_scale" in self.rules and self._threatened(battle, s, opp):
            i = self._find(s, *COUNTER_DEFENSE)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "counter_scale"
                return _skill_action(i)

        # R2 聒噪循环：圆号鱼 用状态技维持敌方的「聒噪」
        if "cry_loop" in self.rules and s.name.startswith(ROUND_FISH) \
                and not self._enemy_has_cry(battle):
            for i, sk in enumerate(s.skills):
                if not sk.is_attack and not sk.is_defense and self._legal(battle, i):
                    self.last_rule = "cry_loop"
                    return _skill_action(i)

        return super()._decide(battle)

    # ── 候选过滤：连击放大器别换走 / 治疗机器人别乱上 ─────────────
    def _plan_candidates(self, battle, s, opp, table, st):
        cands = super()._plan_candidates(battle, s, opp, table, st)
        return self._filter(battle, s, cands, attr="skill_index")

    def _ev_candidates(self, battle, s, opp, table, st):
        cands = super()._ev_candidates(battle, s, opp, table, st)
        return self._filter(battle, s, cands, attr="index")

    def _filter(self, battle, s, cands, *, attr: str):
        hold = "hold_filter" in self.rules and s.name.startswith(FILTER)
        guard = "heal_bot_guard" in self.rules
        hp_ok = (s.current_hp / max(1, s.max_hp)) >= HEAL_BOT_SAFE_HP
        out = []
        for cand in cands:
            if getattr(cand, "kind", "") != "switch":
                out.append(cand)
                continue
            idx = getattr(cand, "switch_index", None)
            if idx is None:
                idx = getattr(cand, "index", None)
            name = ""
            try:
                name = self.player.team[idx].name if isinstance(idx, int) else ""
            except (IndexError, TypeError):
                name = ""
            if hold and name:
                continue                       # 别把连击放大器换下去
            if guard and hp_ok and name.startswith(HEAL_BOT):
                continue                       # 血线还健康 → 不换上治疗机器人（它的攻击被封印）
            out.append(cand)
        return out
