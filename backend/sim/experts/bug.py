# -*- coding: utf-8 -*-
"""虫队专用专家 —— 围绕"奉献（devotion）"资源。

机制读法（`data/skills` 的 `use_devotion` / 描述里带"奉献"的条目）：
- **奉献积累**：`花衣蝶「花精灵」`（回合结束 +1 随机奉献）、`铠甲虫「坚韧铠甲」`（**每受到 1 次攻击伤害** +1 奉献）、
  `恶魔红钻「振奋虫心」`（击败敌方后 +5 奉献）、`虫结阵`（防御，应对攻击 +1）、
  `飞断`（虫/物攻 1 费 20 威力：+1 奉献 威力+20）、`虫群过境`（虫/物攻：+1 奉献 连击+1）、
  `假寐`（虫/状态：回 2 能量 + 1 奉献 能耗−2）。
- **奉献消耗/收益**：`虫群`（虫/物攻 7 费 20 威力，**本技能会受奉献影响**）——威力随奉献池放大。
- 道具 = **愿力**（与血脉技能联动）。

通用专家的病灶：它按"即时伤害"选招，`飞断`(20 威力)/`假寐`/`虫结阵` 这类"铺奉献"的手永远排不上，
`虫群` 也就永远吃不到奉献加成；于是整队拖成 39/120 平局、30% 的回合在换人。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action

FARMER = ("花衣蝶", "铠甲虫")        # 被动刷奉献的两只
# 主动刷奉献的手：最便宜的先看（飞断 1 费 / 假寐 2 费且回 2 能量）
FARM_SKILLS = ("飞断", "假寐", "虫群过境", "虫结阵")
CHEAP_FARM_COST = 2                 # 只有 ≤ 该费用的刷子值得在常规回合占位
FARM_WINDOW_TURNS = 8               # "开局投资窗口"：前几回合值得垫复利


class BugExpert(RuleAgentV2):
    """虫队：把"奉献"当资源铺满再用虫群收。"""

    # 实测（120 局/档）：v1 的"池浅就刷"（devotion_farm 命中 6399 次）0.310、hold_farmer 0.360、
    # devotion_spend 0.438，全开 0.144 —— 无差别刷奉献有害。v2 改成"便宜才刷 + 有斩杀就不刷"。
    # 三版都实测为负（120 局/档，镜像）：v1"池浅就刷" 0.310、v2"便宜才刷" 0.027（命中 6189 次）、
    # v3"替换空过" 0.244（命中 4756 次）——**默认不开任何规则**（= 与通用专家一致），规则保留供复测。
    # 读法：本队的聚能本身有价值（+5 能量，`虫群` 要 7 费），一份奉献换不回一次蓄能；devotion 的复利
    # 只好留给学习式专精去发掘。
    RULES = ()

    def __init__(self, *a, rules=None, **kw):
        super().__init__(*a, **kw)
        self.rules = set(self.RULES if rules is None else rules)
        self.last_rule = ""

    # ── 工具 ──────────────────────────────────────────────────────

    def _pool(self, battle) -> int:
        """奉献池规模：`Player.devotion` = {奉献名: 份数}，每份都是**永久团队增益**
        （威力+20 / 连击数+1 / 能耗-2 / 中毒两层 / 10%吸血），越攒越强 —— 复利型资源。"""
        try:
            dev = getattr(self.player, "devotion", None) or {}
            return sum(int(v) for v in dev.values()) if isinstance(dev, dict) else len(dev)
        except Exception:  # noqa: BLE001
            return 0

    def _best_damage(self, battle, s, opp) -> int:
        return max((d for _i, d, _c in self._attack_table(battle, s, opp)), default=0)

    def _legal(self, battle, i: int) -> bool:
        return battle.action_legality(self.team, Action(kind='skill', skill_index=i)).ok

    def _find(self, s, *names) -> int:
        for i, sk in enumerate(s.skills):
            if sk.name in names:
                return i
        return -1

    # ── 决策 ──────────────────────────────────────────────────────
    def _decide(self, battle):
        s = self.player.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)
        pool = self._pool(battle)

        base = super()._decide(battle)

        # 只在"通用逻辑选择空过（聚能）"时替换：刷子是投资，不能挤掉真正的出招
        if "farm_over_gather" in self.rules and getattr(base, "kind", "") == "gather":
            for name in FARM_SKILLS:
                i = self._find(s, name)
                if i < 0 or not self._legal(battle, i):
                    continue
                cost = battle.skill_energy_cost(self.team, s, s.skills[i], i)
                if cost <= CHEAP_FARM_COST:
                    self.last_rule = "farm_over_gather"
                    return _skill_action(i)
        return base

    # （v1 的 hold_farmer 实测有害 0.360，已删除：被动刷子不值得为它放弃换人）
    def _unused_hold_farmer(self, battle, s, cands):
        if "hold_farmer" not in self.rules:
            return cands
        if not s.name.startswith(FARMER) or self._pool(battle) >= POOL_SPEND_AT:
            return cands
        return [c for c in cands if getattr(c, "kind", "") != "switch"]
