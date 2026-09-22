# -*- coding: utf-8 -*-
"""电羊松鼠平衡队专用专家 —— 围绕"蓄力减费 / 火系灼烧 / 连击堆叠"。

机制链（`data/skills` + `data/traits`）：
- **龙鱼「洄游」**：**每次进入蓄力状态 → 全技能能耗永久 −2**（叠加）→ `升龙咆哮`（龙/200，蓄力）
  反复蓄力后能压到 0 费，变成"免费 200 威力"。通用专家把"蓄力"当浪费回合，永远吃不到这条。
- **尖嘴狐仙「灵魂灼伤」**：**冰系技能 → 敌方 4 层灼烧；火系技能 → 敌方 2 层冻结**；
  `火焰护盾`（火/防御）：应对攻击 → **敌方 6 层灼烧**；`焚烧烙印`：驱散双方印记，每层给敌方 5 层灼烧。
- **蹦床松鼠「囤积」**：每 1 能量 → 双防 +10%；`热身运动`：**自己连击数 +3**（配 `落石`(1连击) → 4 段）。
- **电球咩咩「快充」**：**离场时回 10 能量**（`加大功率`/`闪击折返` 都是自己脱离）。
- **绒光优优「哨兵」**：被威胁时加速、行动后脱离。

逐条量测：构造时传 `rules=` 只开子集；`rules=[]` = 完全等同通用专家（对照组）。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action

DRAGON = "龙鱼"                 # 洄游：蓄力 → 永久 −2 费
CHARGE_SKILL = "升龙咆哮"
FOX = "尖嘴狐仙"                # 灵魂灼伤：火/冰 → 灼烧/冻结
BURN_SHIELD = "火焰护盾"        # 应对攻击 → 敌方 6 层灼烧
BURN_MARK = "焚烧烙印"          # 驱散印记 → 每层 5 层灼烧
SQUIRREL = "蹦床松鼠"           # 囤积 + 热身运动（连击 +3）
WARMUP = "热身运动"
THREAT_RATIO = 0.25
MARK_BURN_MIN = 3               # 敌方印记 ≥ 该值时值得用焚烧烙印换灼烧


class SquirrelExpert(RuleAgentV2):
    """电羊松鼠：把"蓄力/灼烧/连击"当资源滚起来。"""

    # 实测（120 局/档，同队镜像）：charge_ramp 0.871 ±0.065（88-13）✓；全开 0.635（被其它三条拖低）；
    # burn_shield 0.415（有害）、burn_marks / combo_warmup 0.500（惰性）→ 默认只留 charge_ramp。
    RULES = ("charge_ramp",)
    OPTIONAL_RULES = ("burn_shield", "burn_marks", "combo_warmup")

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

    def _opp_marks(self, battle) -> int:
        total = 0
        try:
            for mark in battle.globals.mark_effects.get(self._opp_team, []) or []:
                total += int(getattr(mark, "stacks", 0) or 0)
        except AttributeError:
            pass
        return total

    def _threatened(self, battle, s, opp) -> bool:
        dmg, _c, _i = self._best_hit(battle, opp, s)
        return dmg >= max(1, s.current_hp) * THREAT_RATIO

    def _combo_bonus(self, s) -> float:
        try:
            return float(getattr(s, "_modifiers", {}).get("combo", 0) or 0)
        except Exception:  # noqa: BLE001
            return 0.0

    # ── 决策 ──────────────────────────────────────────────────────
    def _decide(self, battle):
        p = self.player
        s = p.active
        opp = battle.get_opponent(self.team).active
        if s is None or opp is None or s.is_fainted:
            return super()._decide(battle)

        # R1 蓄力滚雪球：龙鱼每次蓄力永久−2 费（升龙咆哮 3 费 → 最终 0 费）
        if "charge_ramp" in self.rules and s.name.startswith(DRAGON):
            i = self._find(s, CHARGE_SKILL)
            if i >= 0 and self._legal(battle, i):
                cost = battle.skill_energy_cost(self.team, s, s.skills[i], i)
                if cost >= 1:                  # 还能再降 → 继续蓄
                    self.last_rule = "charge_ramp"
                    return _skill_action(i)

        # R2 灼烧护盾：被威胁时用 火焰护盾（应对攻击 → 敌方 6 层灼烧）
        if "burn_shield" in self.rules and s.name.startswith(FOX) \
                and self._threatened(battle, s, opp):
            i = self._find(s, BURN_SHIELD)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "burn_shield"
                return _skill_action(i)

        # R3 印记换灼烧：敌方印记多时用 焚烧烙印（每层印记 → 5 层灼烧）
        if "burn_marks" in self.rules and self._opp_marks(battle) >= MARK_BURN_MIN:
            i = self._find(s, BURN_MARK)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "burn_marks"
                return _skill_action(i)

        # R4 连击铺垫：蹦床松鼠先开 热身运动（连击 +3 → 落石变 4 段）
        if "combo_warmup" in self.rules and s.name.startswith(SQUIRREL) \
                and self._combo_bonus(s) < 3:
            i = self._find(s, WARMUP)
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "combo_warmup"
                return _skill_action(i)

        return super()._decide(battle)
