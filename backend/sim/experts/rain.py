# -*- coding: utf-8 -*-
"""魔偶雨天队专用专家 —— 先起雨吃「浸润」，再用魔偶的借用位与喷喷的蓄力联动输出。

来源（社区攻略 + 引擎数据）：
- 队伍=帅帅魔偶/水灵/彩蝶鲨/爆焰喷喷/冰钻布鲁斯/荆棘电环（`meta_teams.json`）。
- **盲从**（帅帅魔偶）：可带多个复写/借用/取念，**非幻系技能能耗-2** → 借来的技能常常 0~1 费。
  借用/取念每回合重掷（引擎 2026-09-22 落地的「变身」），所以理想打法是"每回合用借来的手"。
- **浸润**（水灵）：用一次水系技能 → 全技能能耗-1；**落雨**（水/状态）把天气改成雨天 8 回合。
- **水翼推进**（彩蝶鲨）：队友用过水系技能越多，它入场越便宜。
- **大火球**（爆焰喷喷）：用过 2 个**不同**火系技能后，下一次技能无需蓄力 →
  `龙吟`（蓄力，双攻+150%、速度+80）可以当回合直接开，再接 `龙炮`(龙/100)。
- **冰钻**（冰钻布鲁斯）：对手携带技能总能耗越高，自己攻击越疼（对手越"重"越强）。
- **防过载保护**（荆棘电环）：每次行动后自动脱离 → 天然的一次性干扰手。

通用专家的问题：它不认识"雨天/浸润/蓄力联动"，落雨只当普通状态技；
魔偶的借用位在它眼里和普通技能同价（值不到"每回合一张 0~1 费的牌"）。

本专家只加 4 条队内规则，其余回落到 `RuleAgentV2`。
"""
from __future__ import annotations

from backend.sim.action import Action
from backend.sim.agent_v2 import RuleAgentV2, _skill_action
from backend.sim.battleskill import SkillUse

RAIN = "rain"   # 引擎里天气取值是英文（"" | "rain" | "sand" | "snow"）
BORROW_SLOTS = ("借用", "取念")
WATER_SKILLS = ("天洪", "水波术", "水弹枪", "水花四溅")


class RainExpert(RuleAgentV2):
    """魔偶雨天队：起雨 → 吃浸润折扣 → 借用手 + 蓄力联动。

    `rules` 可只开子集：`rain_first` / `borrow_slot` / `charge_combo` / `rain_water`。
    """

    RULES = ("rain_first", "borrow_slot", "charge_combo", "rain_water")

    def __init__(self, *a, rules=None, **kw):
        super().__init__(*a, **kw)
        self.rules = set(self.RULES if rules is None else rules)
        self.last_rule = ""

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
        if s is None or s.is_fainted:
            return super()._decide(battle)
        opp = battle.get_opponent(self.team).active

        # ── R1 起雨：天气不是雨天，手上带落雨就开（8 回合够整场）──
        if (getattr(battle.globals, "weather", "") != RAIN
                and battle.turn <= 3 and "rain_first" in self.rules):
            i = self._find(s, "落雨")
            if i >= 0 and self._legal(battle, i):
                self.last_rule = "rain_first"
                return _skill_action(i)

        # ── R2 魔偶：借用位现在是"借来的手"，盲从 -2 后常常 0~1 费 → 优先用它 ──
        for i, sk in enumerate(s.skills if "borrow_slot" in self.rules else ()):
            if sk.base.name not in BORROW_SLOTS or sk.replaced_by is None:
                continue
            if not getattr(sk.replaced_by, "is_attack", False):
                continue
            if not self._legal(battle, i):
                continue
            cost = battle.skill_energy_cost(self.team, s, sk, i)
            if cost > 2:
                continue
            dmg, _ = battle._resolver.calc_damage(
                s, opp, SkillUse(battle_skill=sk, skill_index=i), battle.globals,
                attacker_team=self.team)
            if dmg < max(1, opp.current_hp) * 0.12:
                continue                       # 借来的弱手不值得占优先位
            self.last_rule = "borrow_slot"
            return _skill_action(i)

        # ── R3 喷喷：免蓄力窗口开着就先开龙吟，否则用火系技能铺第 2 个火系 ──
        if s.name.startswith("爆焰喷喷") and "charge_combo" in self.rules:
            i = self._find(s, "龙吟")
            if i >= 0 and self._legal(battle, i):          # legality 已含"无需蓄力"
                self.last_rule = "charge_combo"
                return _skill_action(i)

        # ── R4 雨天里优先水系（吃浸润与雨天加成）──
        if getattr(battle.globals, "weather", "") == RAIN and "rain_water" in self.rules:
            water = [(i, dmg) for i, dmg, _c in self._attack_table(battle, s, opp)
                     if s.skills[i].name in WATER_SKILLS]
            if water:
                i, dmg = max(water, key=lambda x: x[1])
                best_any = max((d for _i, d, _c in self._attack_table(battle, s, opp)),
                               default=0)
                if (dmg >= max(1, opp.current_hp) * 0.12 and dmg >= best_any
                        and self._legal(battle, i)):
                    self.last_rule = "rain_water"
                    return _skill_action(i)

        return super()._decide(battle)
