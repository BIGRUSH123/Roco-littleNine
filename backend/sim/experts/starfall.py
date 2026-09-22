# -*- coding: utf-8 -*-
"""星陨队专用专家 —— 以「印记层数 → 引爆伤害」为核心。

来源（社区攻略 + 引擎数据）：
- 队伍=怖哭菇/暮星辰/小皮球/仪式巨像/龙息帕尔/锤头鹳，道具进化之力（`meta_teams.json`）。
- **吸积盘**（怖哭菇）：回合结束敌方 +2 层星陨印记 → 它是"叠层手"，站场就涨层。
- **守望星**（暮星辰）：触发印记只消耗一半层数、仍按满层结算 → 最好的"反复引爆手"。
- **观星**（仪式巨像）：敌方每层印记，自己的**地系**技能威力 +20% → `陨石`(地/100) 同时吃层数与引爆。
- **快锤**（锤头鹳）：能耗 <3 的技能获得迅捷 → `水弹枪`(水/80) 是快的引爆手。
- 引爆规则（引擎 `globals.trigger_starfall`）：**非幻系攻击**命中后消耗层数，追加威力 `X²+24X−24`
  （X=3 → 96 威力，X=5 → 146，X=8 → 272）。所以 `错乱`/`四维降解`（幻）**不引爆**；
  `倾泻` 会**驱散双方所有印记**（清掉自己的投资）。

通用专家的实测病灶：叶子对印记只按线性 0.030/层给分（真实收益是二次曲线），
于是它在对手 26 层时也不动手（星陨镜像 120/120 平）。本专家按机制补 5 条规则，其余回落通用。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action
from backend.sim.tactics import starfall_bonus

STARFALL = "星陨印记"
GENERATOR = "怖哭菇"           # 吸积盘持有者（叠层手）
DISPEL_SKILL = "倾泻"           # 未被我方防御应对时驱散双方所有印记
DETONATE_STACKS = 3            # "有层就别空过"的层数门槛（X=3 时引爆已 96 威力）
PROTECT_STACKS = 3             # 层数 ≥ 该值时不主动清印记
GENERATOR_HOLD_STACKS = 8      # 层数 < 该值时别把叠层手换下场


def starfall_stacks(battle, team: str) -> int:
    """该侧队伍头上的星陨印记层数。"""
    try:
        for mark in battle.globals.mark_effects.get(team, []) or []:
            if getattr(mark, "name", "") == STARFALL:
                return int(getattr(mark, "stacks", 0) or 0)
    except AttributeError:
        pass
    return 0


class StarfallExpert(RuleAgentV2):
    """星陨队：叠层 → 引爆；别把层数（自己的投资）白扔。"""

    RULES = ("detonate_lethal", "attack_over_wait", "insight", "protect_dispel",
             "hold_generator")

    def __init__(self, *a, rules=None, **kw):
        super().__init__(*a, **kw)
        self.rules = set(self.RULES if rules is None else rules)
        self.last_rule = ""          # 最近一次命中的专精规则（诊断用）

    # ── 工具 ──────────────────────────────────────────────────────

    @property
    def _opp_team(self) -> str:
        return "B" if self.team == "A" else "A"

    def _opp_stacks(self, battle) -> int:
        """对手头上的层数（我方施加、可被我方引爆）。"""
        return starfall_stacks(battle, self._opp_team)

    def _legal(self, battle, i: int) -> bool:
        return battle.action_legality(self.team, Action(kind='skill', skill_index=i)).ok

    def _detonate_table(self, battle, s, table, opp):
        """(idx, 直伤, 引爆追加, 合计) —— 只有**非幻**攻击能引爆。"""
        rows = []
        for i, dmg, _cost in table:
            skill = s.skills[i]
            add = starfall_bonus(battle, s, opp, skill, self._opp_team)
            if add <= 0 and getattr(skill, "element", "") == "幻":
                continue                       # 幻系攻击：永远不引爆，不进引爆表
            rows.append((i, dmg, add, dmg + add))
        return rows

    # ── 决策 ──────────────────────────────────────────────────────

    def _decide(self, battle):
        p = self.player
        s = p.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)

        stacks = self._opp_stacks(battle)
        table = self._attack_table(battle, s, opp)
        det = self._detonate_table(battle, s, table, opp)

        # R1 引爆斩杀：这一击 + 印记追加 ≥ 对手血量
        if "detonate_lethal" in self.rules:
            lethal = [row for row in det if row[3] >= opp.current_hp > 0]
            if lethal:
                best = max(lethal, key=lambda r: r[3])
                self.last_rule = "detonate_lethal"
                return _skill_action(best[0])

        # R2 层数在手就别空过：≥3 层且（直伤+引爆）够有效 → 打出去
        #    （通用专家的叶子对印记只给线性分，宁可聚能/防御 —— 26 层不动手就是这么来的）
        if stacks >= DETONATE_STACKS and "attack_over_wait" in self.rules:
            worth = [row for row in det if row[3] >= max(1, opp.current_hp) * 0.12]
            if worth:
                best = max(worth, key=lambda r: r[3])
                self.last_rule = "attack_over_wait"
                return _skill_action(best[0])

        # R3 心灵洞悉：层数 ≥4 时翻倍（+stacks，收益随层数放大）
        if stacks >= 4 and "insight" in self.rules:
            for i, sk in enumerate(s.skills):
                if sk.name == "心灵洞悉" and self._legal(battle, i):
                    self.last_rule = "insight"
                    return _skill_action(i)

        return super()._decide(battle)

    # ── 候选过滤：不做"清自己投资 / 把叠层手换走"的事 ─────────────

    def _plan_candidates(self, battle, s, opp, table, st):
        cands = super()._plan_candidates(battle, s, opp, table, st)
        return self._filter(battle, s, cands, attr="skill_index")

    def _ev_candidates(self, battle, s, opp, table, st):
        cands = super()._ev_candidates(battle, s, opp, table, st)
        return self._filter(battle, s, cands, attr="index")

    def _filter(self, battle, s, cands, *, attr: str):
        """按两条队内规则过滤候选（规划层用 Action、EV 层用 ev.Candidate）。"""
        stacks = self._opp_stacks(battle)
        hold = ("hold_generator" in self.rules and stacks < GENERATOR_HOLD_STACKS
                and s.name.startswith(GENERATOR))
        protect = "protect_dispel" in self.rules and stacks >= PROTECT_STACKS
        out = []
        for cand in cands:
            kind = getattr(cand, "kind", "")
            if kind == "switch" and hold:
                continue
            if kind == "skill" and protect:
                idx = getattr(cand, attr, None)
                if isinstance(idx, int) and 0 <= idx < len(s.skills) \
                        and s.skills[idx].name == DISPEL_SKILL:
                    continue
            out.append(cand)
        return out
