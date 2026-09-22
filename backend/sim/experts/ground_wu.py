# -*- coding: utf-8 -*-
"""新地武专用专家 —— "防御 → 应对成功 → 永久滚雪球"。

机制链（`data/skills` + `data/traits`）：
- **叠势**（游蛇魔使/棋绮后）：2 连击，**每成功应对 1 次，连击数永久 +2** → 越打越强的滚雪球技能。
- **游蛇魔使「思维之盾」**：应对成功后**下次行动技能能耗 −5** → 一次成功应对 = 一张近乎免费的大牌。
- **波多西「定向精炼」**：己方**每用 1 次防御技能**，它入场时机械/地系威力 **+10%**（叠加）。
- **能量守恒**（防御）：应对攻击 → **两侧技能能耗永久 −1**；**轴承支撑**：被动两侧 −1 费。
- **流浪鼠「奔波命」**：用防御技能后**回合结束自动脱离**（免费换位）。
- **齿轮扭矩**（波多西）：**每回合位置发生变化，威力永久 +15**。
- 卷胡巨獭「保守派」：总技能能耗 <4 → 双防 +80%（它的 3 个技能合计 3 费，恒生效）。

通用专家的病灶：它把防御技只当"被打时的应急"，于是
① 永远拿不到"应对成功"带来的永久收益（叠势不成长、−5 费拿不到、波多西不涨威力）；
② 这支队在 `--ab shipped` 里 **WR 0.000**（32 局决出全负）——说明出厂全局规则（交换价值/状态反制）对它还是负作用。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

import dataclasses

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action

# 有"应对收益"的防御技（成功应对才发动的永久增益/反击）
COUNTER_PAYOFF = ("能量守恒", "听桥", "有效预防", "硬化")
# 纯投资型铺垫（无即时伤害，换永久减费）
INVEST_SKILLS = ("轴承支撑", "能量守恒")
THREAT_RATIO = 0.25      # 对手一击 ≥ 我血 × 该值 = "它大概率要打"（防御才不白放）
INVEST_TURNS = 4         # 前几回合做永久减费投资


class GroundWuExpert(RuleAgentV2):
    """新地武：把防御技当铺垫用，滚"应对成功"的永久收益。"""

    # 实测（120 局/档，同队镜像）：off_status_counter 14-0（106 平），其余三条惰性或有害
    # （counter_setup 命中 2827 次把胜率压到 0.110）→ 默认只开这一条，其余留作可测规则。
    RULES = ("off_status_counter",)
    OPTIONAL_RULES = ("counter_setup", "invest_cost", "off_trade")

    def __init__(self, *a, rules=None, **kw):
        super().__init__(*a, **kw)
        self.rules = set(self.RULES if rules is None else rules)
        self.last_rule = ""
        self._apply_team_overrides()

    # ── 队级策略覆盖：出厂全局规则里有对这条队负作用的项 ──
    def _apply_team_overrides(self) -> None:
        st = getattr(self, "strategy", None)
        if st is None:
            return
        overrides: dict[str, object] = {}
        if "off_status_counter" in self.rules:
            overrides["status_counter"] = False
        if "off_trade" in self.rules:
            overrides["trade_margin"] = 1e9        # 永不换命（旧口径）
        if not overrides:
            return
        try:
            st.sprites = {k: dataclasses.replace(v, **overrides)
                          for k, v in (st.sprites or {}).items()}
            st.default = dataclasses.replace(st.default, **overrides)
        except Exception:  # noqa: BLE001 — 策略对象形态变化时不影响决策
            pass

    # ── 工具 ──────────────────────────────────────────────────────
    def _legal(self, battle, i: int) -> bool:
        return battle.action_legality(self.team, Action(kind='skill', skill_index=i)).ok

    def _find(self, s, *names) -> int:
        for i, sk in enumerate(s.skills):
            if sk.name in names:
                return i
        return -1

    def _threatened(self, battle, s, opp) -> bool:
        """对手这一击够疼（≥我血 × THREAT_RATIO）→ 这时放防御技才大概率吃到"应对成功"。"""
        dmg, _c, _i = self._best_hit(battle, opp, s)
        return dmg >= max(1, s.current_hp) * THREAT_RATIO

    # ── 决策 ──────────────────────────────────────────────────────
    def _decide(self, battle):
        s = self.player.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)

        # R1 永久减费投资：开局用 轴承支撑/能量守恒 把费用曲线压下去
        if "invest_cost" in self.rules and battle.turn <= INVEST_TURNS:
            i = self._find(s, *INVEST_SKILLS)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "invest_cost"
                return _skill_action(i)

        # R2 应对铺垫：它要打的时候，用"有应对收益"的防御技去吃永久增益/反击
        #    （通用专家只在"自己快被秒"时才防御，永远吃不到 叠势/思维之盾/定向精炼 的成长）
        if "counter_setup" in self.rules and self._threatened(battle, s, opp):
            i = self._find(s, *COUNTER_PAYOFF)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "counter_setup"
                return _skill_action(i)

        return super()._decide(battle)
