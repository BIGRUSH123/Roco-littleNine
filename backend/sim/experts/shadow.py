# -*- coding: utf-8 -*-
"""黑影平衡毒专用专家 —— "惩罚对面对位轮转"。

机制链（`data/skills` + `data/traits`）：
- **影狸「下黑手」**：**敌方精灵离场后，换上场的精灵获得 5 层中毒** → 对手一换人/被逼脱离就白送毒层。
- **黑羽夫人「孤傲」**：敌方精灵离场后，其增益和减益**由换上场的继承**（把减益"传"给新上场的）。
- **毒液渗透**（影狸）：敌方**每有 1 层中毒，能耗 −1**，并再 +1 层 → 对刚上场（5 层）的精灵近乎免费的大招。
- **感染病**（影狸）：击败敌方时**把中毒转化为中毒印记**（永久 tick）。
- **裘卡「蚀刻」**：回合末每 2 层中毒 → 1 层印记；**琉璃水母「扩散侵蚀」**：水系技能 → 中毒 = 印记×2。
- **嘲弄**（影狸）：自己魔攻 +90%，**若敌方本回合更换精灵再加速度 +70**。
- 泡沫幻影/幽灵爆发等提供脱离与输出。

通用专家的病灶：`--ab shipped` 里这队 **120/120 平、动作 98% 是换人** —— 它把"轮转"当成万能解，
既不去惩罚对手的轮转（新上场者身上的 5 层毒白送），也不肯停下来把毒铺成伤害。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action
from .poison import POISON, abnormal_stacks, mark_stacks

VENOM_SKILL = "毒液渗透"
FINISHER = "感染病"                 # 击败 → 中毒转印记
POISON_SKILLS = ("毒孢子", "疫病吐息", "毒囊")
TAUNT = "嘲弄"
PUNISH_MIN_POISON = 2              # 刚上场的对手至少有这么多毒才值得"惩罚"
SPREAD_BELOW = 3                   # 中毒低于该值时先铺毒
HOLD_ENGINE_BELOW = 6              # 中毒低于该值时别把自己的铺毒手换下去


class ShadowPoisonExpert(RuleAgentV2):
    """黑影平衡毒：惩罚对手轮转 + 把毒层换成伤害。"""

    # 实测（120 局/档）：spread_poison 0.683 ✓；punish_rotation 修好判据后重测；
    # no_idle_rotate 0.442（有害）、infect_finisher 0.513（惰性）→ 都不进默认集。
    RULES = ("spread_poison", "punish_rotation")
    OPTIONAL_RULES = ("no_idle_rotate", "infect_finisher")

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

    def _poison(self, sprite) -> int:
        return abnormal_stacks(sprite, POISON)

    def _just_entered(self, battle, sprite) -> bool:
        """对手这只是**刚换上场的**（吃到下黑手 5 层毒的那一只）。

        注意：决策发生在入场之后的下一回合，所以 `entry_turn == battle.turn` 永远不成立——
        与 `agent_v2` 的"刚换上来的"同一口径：`battle.turn - entry_turn <= 1`。
        """
        return battle.turn - getattr(sprite, "entry_turn", -99) <= 1

    # ── 决策 ──────────────────────────────────────────────────────
    def _decide(self, battle):
        s = self.player.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)

        poison = self._poison(opp)

        # R1 惩罚轮转：对面刚换上来的精灵（带着下黑手的 5 层毒）→ 用近乎免费的毒系大招
        if "punish_rotation" in self.rules and self._just_entered(battle, opp) \
                and poison >= PUNISH_MIN_POISON:
            i = self._find(s, VENOM_SKILL, FINISHER)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "punish_rotation"
                return _skill_action(i)

        # R2 收割：感染病能一击杀死 → 中毒转印记（永久 tick）
        if "infect_finisher" in self.rules:
            i = self._find(s, FINISHER)
            if i >= 0 and self._legal(battle, i):
                table = {idx: dmg for idx, dmg, _c in self._attack_table(battle, s, opp)}
                if table.get(i, 0) >= opp.current_hp > 0:
                    self.last_rule = "infect_finisher"
                    return _skill_action(i)

        # R3 铺毒：毒层不够时先把层数铺起来（这队的主要输出）
        if "spread_poison" in self.rules and poison < SPREAD_BELOW:
            i = self._find(s, *POISON_SKILLS)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "spread_poison"
                return _skill_action(i)

        return super()._decide(battle)

    # R4 别空转：毒层还没起来、自己手上还有铺毒手时，不要主动换人（本队 98% 换人就是这么来的）
    def _plan_candidates(self, battle, s, opp, table, st):
        cands = super()._plan_candidates(battle, s, opp, table, st)
        return self._no_idle_rotate(battle, s, cands)

    def _ev_candidates(self, battle, s, opp, table, st):
        cands = super()._ev_candidates(battle, s, opp, table, st)
        return self._no_idle_rotate(battle, s, cands)

    def _no_idle_rotate(self, battle, s, cands):
        if "no_idle_rotate" not in self.rules:
            return cands
        opp = battle.get_opponent(self.team).active
        if opp is None or self._poison(opp) >= HOLD_ENGINE_BELOW:
            return cands
        if self._find(s, *POISON_SKILLS, VENOM_SKILL) < 0:
            return cands                      # 自己手上没有铺毒手 → 换人不算空转
        return [c for c in cands if getattr(c, "kind", "") != "switch"]
