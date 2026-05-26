"""backend/sim/globals.py — 全局效果（天气 + 双方印记 + 场地）"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING




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
            from .sprite import StatusEffect
            for s in sprites:
                if not s.is_fainted:
                    s.add_effect(StatusEffect(
                        name='冻结', category='abnormal',
                        stacks=2, scope='persistent', source='暴风雪',
                    ))
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
        from backend.vm.effect import MarkEffect
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
        from backend.vm.effect import MarkEffect
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.name == name:
                return me
        return None

    def mark_power_bonus(self, team: str, skill: Skill) -> int:
        """印记威力加成。"""
        from backend.vm.effect import MarkEffect
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.is_positive and me.power_bonus:
                if me.condition == 'is_attack' and not skill.is_attack:
                    continue
                total += me.power_bonus * me.stacks
        return total

    def mark_damage_mult(self, team: str, is_first: bool) -> float:
        """印记伤害倍率。"""
        from backend.vm.effect import MarkEffect
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
        from backend.vm.effect import MarkEffect
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.speed_penalty:
                total += me.speed_penalty * me.stacks
        return total

    def mark_energy_mod(self, team: str) -> int:
        """印记能耗减免。"""
        from backend.vm.effect import MarkEffect
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.energy_mod:
                total += me.energy_mod * me.stacks
        return total

    def mark_switch_damage(self, team: str, sprite: Sprite) -> int:
        """印记进场伤害。"""
        from backend.vm.effect import MarkEffect
        total = 0
        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.switch_damage_pct:
                total += max(0, round(sprite.max_hp * me.switch_damage_pct * me.stacks))
        return total

    def mark_switch_energy_loss(self, team: str) -> int:
        """印记进场扣能。"""
        from backend.vm.effect import MarkEffect
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
        from backend.vm.effect import MarkEffect

        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.name == '星陨印记' and me.stacks > 0:
                total = me.stacks
                consume = amount
                if sprite is not None:
                    from backend.vm.effect import ModifierEffect
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

    def trigger_starfall(self, team: str, attacker: Sprite, defender: Sprite) -> int:
        """触发星陨印记：消耗全部层数，造成幻系魔法伤害。
        返回实际伤害值（0=无星陨或非攻击技能）。
        """
        from backend.vm.effect import MarkEffect

        for me in self.mark_effects.get(team, []):
            if isinstance(me, MarkEffect) and me.name == '星陨印记' and me.stacks > 0:
                total_stacks = me.stacks
                dmg_per_stack = me.starfall_damage or 30
                consumed = self.consume_starfall_stacks(team, total_stacks, defender)
                if consumed <= 0:
                    return 0
                raw = round(attacker.effective_stat('sp_atk') * (total_stacks * dmg_per_stack)
                            / max(1, defender.effective_stat('sp_def')))
                return defender.take_damage(raw)
        return 0

    def set_weather(self, weather: str, turns: int = WEATHER_DURATION) -> None:
        self.weather = weather
        self.weather_turns = turns

    @staticmethod
    def classify_mark(name: str) -> str:
        """根据名称判断印记正负。"""
        from backend.engine.mark_config import POSITIVE_MARK_NAMES, NEGATIVE_MARK_NAMES
        if name in POSITIVE_MARK_NAMES:
            return 'positive'
        if name in NEGATIVE_MARK_NAMES:
            return 'negative'
        return 'negative'  # 安全默认
