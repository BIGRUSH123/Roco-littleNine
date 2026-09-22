# -*- coding: utf-8 -*-
"""羽刃翼王铁头队专用专家 —— 与"铁头海豹平衡队"共用 5 只精灵，处方也共用一条 + 迅捷羽刃。

机制读法：
- **圣剑-X「正位宝剑」**：入场封 2 号位起 → 只剩 1 号位 `休息回复` → **治疗机器人**
  （与铁头海豹同一条：血线健康时别换它上场；实测那条把平局 75%→15%）。
- **圣羽翼王「飓风」**：对本精灵的技能，**若其他翼系精灵携带相同技能，则获得迅捷** ——
  它和泥吼牙都带 `羽刃`（翼/物攻 75，应对状态：**回合结束使敌方紧急脱离**）→ 圣羽翼王的羽刃是**先手**，
  且"应对状态"命中就能把对面逼下场（对我方是节奏优势）。
- **泥吼牙「无差别过滤」**：全体连击固定为 2（双向，守它没意义，铁头海豹已实测惰性）。
- **圆号鱼「泛音列」**／**海豹船长「身经百练」**／**荆棘电环「防过载保护」**：同上批。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action
from .common import heal_bot_guard

HEAL_BOT = "圣剑-X"            # 正位宝剑 → 治疗机器人
WING_KING = "圣羽翼王"          # 飓风 → 与队友共有的翼系技能获得迅捷
WING_EDGE = "羽刃"              # 迅捷 + 应对状态：敌方紧急脱离
HEAL_BOT_SAFE_HP = 0.45


class WingKingExpert(RuleAgentV2):
    """羽刃翼王铁头队：别浪费回合在治疗机器人上 + 用迅捷羽刃压节奏。"""

    # 实测（120 局/档，镜像）：heal_bot_guard 与对照完全一致（26-20-74）→ 本队零效果；
    # wing_edge 0.323（有害：强用羽刃挤掉了更好的手）→ **默认集清空**（= 与通用专家一致），
    # 两条规则保留供复测。读法：与铁头海豹虽共用 5/6 只精灵，但僵局成因不同（本队是聚能僵持 21%），
    # "守治疗机器人"那条处方**不可跨队迁移**。
    RULES = ()

    def __init__(self, *a, rules=None, **kw):
        super().__init__(*a, **kw)
        self.rules = set(self.RULES if rules is None else rules)
        self.last_rule = ""

    def _legal(self, battle, i: int) -> bool:
        return battle.action_legality(self.team, Action(kind='skill', skill_index=i)).ok

    def _decide(self, battle):
        s = self.player.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)

        # R2 迅捷羽刃：圣羽翼王的羽刃是"先手 + 应对状态逼对面脱离"，价值高于同威力的普通手
        if "wing_edge" in self.rules and s.name.startswith(WING_KING):
            for i, sk in enumerate(s.skills):
                if sk.name == WING_EDGE and self._legal(battle, i):
                    self.last_rule = "wing_edge"
                    return _skill_action(i)

        return super()._decide(battle)

    # R1 治疗机器人别乱上（与铁头海豹共用实现）
    def _plan_candidates(self, battle, s, opp, table, st):
        cands = super()._plan_candidates(battle, s, opp, table, st)
        if "heal_bot_guard" not in self.rules:
            return cands
        return heal_bot_guard(self.player, s, cands,
                              bot_prefix=HEAL_BOT, hp_threshold=HEAL_BOT_SAFE_HP)

    def _ev_candidates(self, battle, s, opp, table, st):
        cands = super()._ev_candidates(battle, s, opp, table, st)
        if "heal_bot_guard" not in self.rules:
            return cands
        return heal_bot_guard(self.player, s, cands,
                              bot_prefix=HEAL_BOT, hp_threshold=HEAL_BOT_SAFE_HP)
