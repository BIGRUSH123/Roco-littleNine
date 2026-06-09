"""backend/sim/globals.py — 全局效果（天气 + 双方印记 + 场地）"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.vm.effect import MarkEffect, ModifierEffect

if TYPE_CHECKING:
    from .skill import Skill
    from .sprite import Sprite


WEATHER_DURATION = 8  # 天气持续回合


@dataclass
class GlobalEffects:
    """全局战场效果：天气 + 双方印记 + 场地。"""

    # 天气
    weather: str = ''           # "" | "rain" | "sand" | "snow"
    weather_turns: int = 0

    # 双方印记（MarkEffect 对象列表）
    mark_effects: dict[str, list] = field(default_factory=dict)
    # mark_effects["A"] / mark_effects["B"] → list[MarkEffect]

    # ── 天气查询 ──

    def weather_damage_mult(self, element: str) -> float:
        """天气对技能伤害的倍率。"""
        if self.weather == 'rain' and '水' in element:
            return 1.5
        return 1.0

    def weather_energy_mod(self, element: str) -> float:
        """天气对技能耗能的倍率（沙暴地系 0.5）。"""
        if self.weather == 'sand' and '地' in element:
            return 0.5
        return 1.0

    def weather_turn_effects(self, sprites: list[Sprite]) -> list[str]:
        """回合末天气效果。"""
        events: list[str] = []
        if self.weather == 'snow':
            from copy import copy

            from backend.engine.abnormal_config import ABNORMAL_TEMPLATES
            for s in sprites:
                if not s.is_fainted:
                    template = ABNORMAL_TEMPLATES.get('冻结')
                    if template is not None:
                        ae = copy(template)
                        ae.stacks = 2
                        ae.source = '暴风雪'
                        s.add_effect(ae)
                        total = s.get_stacks('冻结')
                        events.append(f'{s.name} 暴风雪+2冻结(共{total}层)')
        return events

    def tick_weather(self) -> None:
        if self.weather_turns > 0:
            self.weather_turns -= 1
            if self.weather_turns == 0:
                self.weather = ''

    # ── 印记查询 ──

    def get_marks(self, team: str) -> tuple[list, list]:
        """返回 (pos_marks, neg_marks) as MarkEffect lists."""
        pos, neg = [], []
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect):
                if me.is_positive:
                    pos.append(me)
                else:
                    neg.append(me)
        return pos, neg

    def get_mark_by_name(self, team: str, name: str):
        """获取指定名称的印记（MarkEffect）。"""
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.name == name:
                return me
        return None

    def mark_power_bonus(self, team: str, skill: Skill) -> int:
        """印记威力加成。"""
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.is_positive and me.power_bonus:
                if me.condition == 'is_attack' and not skill.is_attack:
                    continue
                total += me.power_bonus * me.stacks
        return total

    def mark_damage_mult(self, team: str, is_first: bool) -> float:
        """印记伤害倍率。"""
        mult = 1.0
        for me in self.mark_effects.get(team, []):
            if not isinstance(me, MarkEffect) or not me.damage_mult:
                continue
            cond = me.condition
            if cond == '' or (cond == 'is_first' and is_first) or (cond == 'not_first' and not is_first):
                mult += me.damage_mult * me.stacks
        return mult

    def mark_speed_penalty(self, team: str) -> int:
        """减速印记速度惩罚。"""
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.speed_penalty:
                total += me.speed_penalty * me.stacks
        return total

    def mark_energy_mod(self, team: str) -> int:
        """印记能耗减免。"""
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.energy_mod:
                total += me.energy_mod * me.stacks
        return total

    def mark_switch_damage(self, team: str, sprite: Sprite) -> int:
        """印记进场伤害。"""
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.switch_damage_pct:
                total += max(0, round(sprite.max_hp * me.switch_damage_pct * me.stacks))
        return total

    def mark_switch_energy_loss(self, team: str) -> int:
        """印记进场扣能。"""
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.switch_energy_loss:
                total += me.switch_energy_loss * me.stacks
        return total

    def mark_turn_end_effects(self, sprites: dict[str, Sprite]) -> list[str]:
        """回合末印记效果。"""
        events: list[str] = []
        for team_key in ('A', 'B'):
            sprite = sprites.get(team_key)
            if not sprite or sprite.is_fainted:
                continue
            for me in self.mark_effects.get(team_key, []):
                if me.turn_end_energy:
                    gained = sprite.gain_energy(me.turn_end_energy * me.stacks)
                    if gained:
                        events.append(f'{sprite.name} {me.name}+{gained}E')
                if me.turn_end_damage_pct:
                    dmg = max(1, round(sprite.max_hp * me.turn_end_damage_pct * me.stacks))
                    sprite.take_damage(dmg)
                    events.append(f'{sprite.name} {me.name}-{dmg}HP')
        return events

    # ── 印记增删 ──

    def apply_mark(self, team: str, name: str, category: str, stacks: int = 1,
                   coexist: bool = False) -> list[str]:
        """应用印记。
        若 coexist=True → 共存（同名叠加，异名新增）。
        否则 → 替换（同类别清空后新增）。
        返回事件列表。"""
        from backend.engine.mark_config import MARK_TEMPLATES
        from backend.vm.effect import MarkEffect

        self.mark_effects.setdefault(team, [])
        me_list = self.mark_effects[team]

        existing = next((e for e in me_list if e.name == name), None)
        if existing is not None:
            existing.stacks += stacks
            return []

        template = MARK_TEMPLATES.get(name)
        if template is not None:
            new_me = MarkEffect(
                name=template.name,
                source=template.source or name,
                scope=template.scope,
                ttl=template.ttl,
                stacks=stacks,
                category=template.category,
                power_bonus=template.power_bonus,
                damage_mult=template.damage_mult,
                speed_penalty=template.speed_penalty,
                energy_mod=template.energy_mod,
                turn_end_energy=template.turn_end_energy,
                turn_end_damage_pct=template.turn_end_damage_pct,
                switch_damage_pct=template.switch_damage_pct,
                switch_energy_loss=template.switch_energy_loss,
                starfall_damage=template.starfall_damage,
                condition=template.condition,
            )
        else:
            new_me = MarkEffect(
                name=name, source="skill", scope="persistent",
                stacks=stacks, category=category,
            )

        if not coexist:
            me_list[:] = [e for e in me_list if e.category != category]
        me_list.append(new_me)
        return []

    def remove_mark(self, team: str, category: str) -> None:
        """Remove all marks of a category from a team."""
        me_list = self.mark_effects.get(team, [])
        me_list[:] = [e for e in me_list if e.category != category]

    def consume_starfall_stacks(self, team: str, amount: int, sprite: Sprite) -> int:
        """消耗星陨印记层数。若 sprite 有守望星 → 只消耗一半。
        返回实际消耗层数（用于伤害计算）。"""
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.name == '星陨印记' and me.stacks > 0:
                total = me.stacks
                consume = amount
                if sprite is not None:
                    for e in getattr(sprite, 'active_effects', []):
                        if isinstance(e, ModifierEffect) and e.attr == "starfall_consume_ratio":
                            consume = max(1, int(amount * e.value))
                            break
                consumed = min(consume, total)
                me.stacks -= consumed
                if me.stacks <= 0:
                    self.mark_effects[team].remove(me)
                return consumed
        return 0

    def trigger_starfall(
        self,
        team: str,
        attacker: Sprite,
        defender: Sprite,
        trigger_skill: Skill | None = None,
    ) -> int:
        """触发星陨印记：非幻攻击后消耗全部层数并追加幻系伤害。

        星陨威力 = X^2 + 24X - 24，其中 X 为触发前层数。
        攻防属性类型跟随触发技能：物攻→物攻/物防，魔攻→魔攻/魔防，
        动态攻击沿用技能的动态攻防判定。返回实际伤害值。
        """
        skill_type = getattr(trigger_skill, 'skill_type', '魔攻') if trigger_skill is not None else '魔攻'
        if skill_type not in ('物攻', '魔攻', '动态攻击'):
            return 0

        atk_key = 'sp_atk'
        def_key = 'sp_def'
        if trigger_skill is not None:
            get_keys = getattr(trigger_skill, 'get_atk_def_keys', None)
            if callable(get_keys):
                keys = get_keys(attacker)
                if keys is None:
                    return 0
                atk_key, def_key = keys
            elif skill_type == '物攻':
                atk_key, def_key = 'atk', 'def'
            elif skill_type == '动态攻击':
                if attacker.effective_stat('atk') >= attacker.effective_stat('sp_atk'):
                    atk_key, def_key = 'atk', 'def'

        for me in self.mark_effects.get(team, []):
            if not isinstance(me, MarkEffect) or me.name != '星陨印记' or me.stacks <= 0:
                continue

            total_stacks = me.stacks
            consume = total_stacks
            if attacker is not None:
                for e in getattr(attacker, 'active_effects', []):
                    if isinstance(e, ModifierEffect) and e.attr == "starfall_consume_ratio":
                        consume = max(1, int(total_stacks * e.value))
                        break
            consumed = min(consume, total_stacks)
            me.stacks -= consumed
            if me.stacks <= 0:
                self.mark_effects[team].remove(me)
            if consumed <= 0:
                return 0

            power = total_stacks * total_stacks + 24 * total_stacks - 24
            if power <= 0:
                return 0
            atk = attacker.effective_stat(atk_key)
            defense = max(1, defender.effective_stat(def_key))
            type_mult = self._element_mult('幻', defender)
            damage_reduction = min(1.0, max(0.0, defender._modifiers.get("damage_reduction", 0.0)))
            if damage_reduction >= 1.0:
                return 0
            raw = round(power * atk / defense * (37.0 / 41.0) * type_mult * (1.0 - damage_reduction))
            return defender.take_damage(max(1, raw))
        return 0

    @staticmethod
    def _element_mult(element: str, defender: Sprite) -> float:
        if not element:
            return 1.0
        from backend.sim.resolver import _TYPE_CHART

        attrs = getattr(defender.species, 'attributes', '')
        mult = 1.0
        for attr in (attrs.split(',') if attrs else []):
            attr = attr.strip()
            if attr:
                mult *= _TYPE_CHART.get(element, {}).get(attr, 1.0)
        return mult

    def set_weather(self, weather: str, turns: int = WEATHER_DURATION) -> None:
        self.weather = weather
        self.weather_turns = turns

    @staticmethod
    def classify_mark(name: str) -> str:
        """根据名称判断印记正负。"""
        from backend.engine.mark_config import NEGATIVE_MARK_NAMES, POSITIVE_MARK_NAMES
        if name in POSITIVE_MARK_NAMES:
            return 'positive'
        if name in NEGATIVE_MARK_NAMES:
            return 'negative'
        return 'negative'  # 安全默认
