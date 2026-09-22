# -*- coding: utf-8 -*-
"""首领毒专用专家 —— 围绕"中毒层数"这条换算链。

来源（社区攻略 + 引擎数据）：
- 队伍=千棘盔(首领)/琉璃水母/裘卡/绒光优优/瞌睡王/彩蝶鲨，道具进化之力（`meta_teams.json`）。
- **中毒**（异常）：每层每回合末 3% 生命（`abnormal_config`），**中毒印记**同口径
  （`mark_config: turn_end_damage_pct=0.03`）——所以"层数"就是这支队的主要输出来源。
- **千棘盔 · 毒液渗透**（毒/120/5费）：敌方每有 1 层中毒，**能耗 -1**，并再 +1 层中毒
  → 中毒铺起来以后这是一门**近乎免费的大招**。
- **琉璃水母 · 扩散侵蚀**：使用水系技能后，敌方获得中毒 = **中毒印记层数 × 2**
  → 先有印记（疫病吐息）再用 `甩水`(0 费, 回 1 能量) 就能白嫖大量中毒层。
- **裘卡 · 蚀刻**：回合结束敌方每 2 层中毒 → 1 层中毒印记（把会溢出的层"存"成印记）。
- **绒光优优 · 哨兵**：被威胁时加速并行动后脱离；**瞌睡王 · 慢热型**：入场前每次成功应对回 5 能量。

通用专家的问题：它按"单发伤害 + 线性层数分"选招，于是
`甩水`(30 威力) 这种"0 费铺毒手"永远排不上，毒层到不了能白嫖 `毒液渗透` 的量级。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action

POISON = "中毒"
POISON_MARK = "中毒印记"
SPREADERS = ("琉璃水母",)          # 扩散侵蚀持有者
VENOM_SKILL = "毒液渗透"
FREE_WATER = "甩水"
POISON_SKILLS = ("毒孢子", "疫病吐息", "毒囊")   # 能加中毒/印记的技
CONVERTER = "裘卡"                  # 蚀刻：中毒 → 中毒印记
SPREAD_MIN_MARKS = 1               # 印记 ≥1 时水系技能才开始"扩散"
SPREAD_MIN_POISON = 3              # 中毒 < 该值时优先铺毒
HOLD_POISON = 4                    # 中毒 ≥ 该值时别把转换器换下去


def mark_stacks(battle, team: str, name: str) -> int:
    """队伍级印记层数（`battle.globals.mark_effects` 按队伍存）。"""
    try:
        for mark in battle.globals.mark_effects.get(team, []) or []:
            if getattr(mark, "name", "") == name:
                return int(getattr(mark, "stacks", 0) or 0)
    except AttributeError:
        pass
    return 0


def abnormal_stacks(sprite, name: str) -> int:
    """精灵身上的异常层数（`Sprite.get_stacks` 走缓存，O(1)）。"""
    try:
        return int(sprite.get_stacks(name))
    except Exception:  # noqa: BLE001 — 没有该异常/接口变动时按 0 处理
        return 0


class PoisonExpert(RuleAgentV2):
    """首领毒：铺中毒 → 白嫖毒液渗透；用扩散侵蚀把印记变成中毒层。"""

    RULES = ("venom_cheap", "water_spreads", "spread_poison", "hold_converter")

    def __init__(self, *a, rules=None, **kw):
        super().__init__(*a, **kw)
        self.rules = set(self.RULES if rules is None else rules)
        self.last_rule = ""

    @property
    def _opp_team(self) -> str:
        return "B" if self.team == "A" else "A"

    def _opp_poison(self, battle) -> int:
        """对手**场上**精灵的中毒层数（毒液渗透的减费与蚀刻的转换都按它算）。"""
        return abnormal_stacks(battle.get_opponent(self.team).active, POISON)

    def _opp_marks(self, battle) -> int:
        """对手队伍的中毒印记层数（队伍级）。"""
        return mark_stacks(battle, self._opp_team, POISON_MARK)

    def _legal(self, battle, i: int) -> bool:
        return battle.action_legality(self.team, Action(kind='skill', skill_index=i)).ok

    def _find(self, s, *names) -> int:
        for i, sk in enumerate(s.skills):
            if sk.name in names:
                return i
        return -1

    def _decide(self, battle):
        p = self.player
        s = p.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)

        poison = self._opp_poison(battle)
        marks = self._opp_marks(battle)

        # R1 白嫖大招：毒液渗透在中毒层数够时几乎免费（敌方每层 −1 费）
        if "venom_cheap" in self.rules and poison >= 3:
            i = self._find(s, VENOM_SKILL)
            if i >= 0 and self._legal(battle, i) \
                    and battle.skill_energy_cost(self.team, s, s.skills[i], i) <= 2:
                self.last_rule = "venom_cheap"
                return _skill_action(i)

        # R2 扩散侵蚀：印记 ≥1 时用 0 费水系手（甩水）白嫖"中毒 = 印记×2"
        if "water_spreads" in self.rules and s.name.startswith(SPREADERS) \
                and marks >= SPREAD_MIN_MARKS:
            i = self._find(s, FREE_WATER)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "water_spreads"
                return _skill_action(i)

        # R3 铺毒优先：中毒太低时先把层数铺起来（层数 = 主要输出）
        if "spread_poison" in self.rules and poison < SPREAD_MIN_POISON:
            i = self._find(s, *POISON_SKILLS)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "spread_poison"
                return _skill_action(i)

        return super()._decide(battle)

    # R4 守住转换器：裘卡的蚀刻每回合把中毒转成印记，层数够时别主动换走它
    def _plan_candidates(self, battle, s, opp, table, st):
        cands = super()._plan_candidates(battle, s, opp, table, st)
        return self._hold_converter(battle, s, cands, attr="skill_index")

    def _ev_candidates(self, battle, s, opp, table, st):
        cands = super()._ev_candidates(battle, s, opp, table, st)
        return self._hold_converter(battle, s, cands, attr="index")

    def _hold_converter(self, battle, s, cands, *, attr: str):
        if "hold_converter" not in self.rules:
            return cands
        if not s.name.startswith(CONVERTER) or self._opp_poison(battle) < HOLD_POISON:
            return cands
        return [c for c in cands if getattr(c, "kind", "") != "switch"]
