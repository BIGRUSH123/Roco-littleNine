"""scripts/sim/globals.py — 全局效果（天气 + 双方印记 + 场地）"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sprite import Sprite
    from .skill import Skill


@dataclass
class Mark:
    """单方印记。每方最多 1 正面 + 1 负面。"""
    name: str
    category: str           # "positive" | "negative"
    stacks: int = 1

    @property
    def is_positive(self) -> bool:
        return self.category == 'positive'

    @property
    def is_negative(self) -> bool:
        return self.category == 'negative'


# 正面印记
POSITIVE_MARKS = frozenset({
    '光合印记', '攻击印记', '蓄电印记', '润泽印记',
    '龙式印记', '风起', '蓄势印记',
})

# 负面印记
NEGATIVE_MARKS = frozenset({
    '减速', '迟缓', '降临印记', '棘刺', '中毒印记', '星陨印记',
})

WEATHER_DURATION = 8  # 天气持续回合


@dataclass
class GlobalEffects:
    """全局战场效果：天气 + 双方印记 + 场地。"""

    # 天气
    weather: str = ''           # "" | "rain" | "sand" | "snow"
    weather_turns: int = 0

    # 双方印记（每方最多 1 正 1 负）
    pos_mark_a: Mark | None = None
    neg_mark_a: Mark | None = None
    pos_mark_b: Mark | None = None
    neg_mark_b: Mark | None = None

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

    def weather_turn_effects(self, sprites: list['Sprite']) -> list[str]:
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

    def get_marks(self, team: str) -> tuple[Mark | None, Mark | None]:
        """返回 (pos_mark, neg_mark)。"""
        if team == 'A':
            return self.pos_mark_a, self.neg_mark_a
        return self.pos_mark_b, self.neg_mark_b

    def mark_power_bonus(self, team: str, skill: 'Skill') -> int:
        """印记威力加成（攻击印记、蓄电印记）。"""
        pos, _ = self.get_marks(team)
        if not pos:
            return 0
        if pos.name == '攻击印记':
            return 10 * pos.stacks
        if pos.name == '蓄电印记' and skill.is_attack:
            return 10 * pos.stacks
        return 0

    def mark_damage_mult(self, team: str, is_first: bool) -> float:
        """印记伤害倍率（迟缓、风起）。"""
        pos, neg = self.get_marks(team)
        mult = 1.0
        if pos and pos.name == '风起' and is_first:
            mult += 0.20 * pos.stacks
        if neg and neg.name == '迟缓' and not is_first:
            mult += 0.30 * neg.stacks
        return mult

    def mark_speed_penalty(self, team: str) -> int:
        """减速印记：每层 -10 速度。"""
        _, neg = self.get_marks(team)
        if neg and neg.name == '减速':
            return 10 * neg.stacks
        return 0

    def mark_energy_mod(self, team: str) -> int:
        """润泽印记：每层 -1 能耗。"""
        pos, _ = self.get_marks(team)
        if pos and pos.name == '润泽印记':
            return pos.stacks
        return 0

    def mark_switch_damage(self, team: str, sprite: 'Sprite') -> int:
        """棘刺印记进场伤害（6% 最大 HP/层）。"""
        _, neg = self.get_marks(team)
        if neg and neg.name == '棘刺':
            return max(0, round(sprite.max_hp * 0.06 * neg.stacks))
        return 0

    def mark_switch_energy_loss(self, team: str) -> int:
        """降临印记进场扣能（1/层）。"""
        _, neg = self.get_marks(team)
        if neg and neg.name == '降临印记':
            return neg.stacks
        return 0

    def mark_turn_end_effects(self, sprites: dict[str, 'Sprite']) -> list[str]:
        """回合末印记效果（光合回能、中毒扣血、星陨结算）。"""
        events: list[str] = []
        for team_key in ('A', 'B'):
            pos, neg = self.get_marks(team_key)
            sprite = sprites.get(team_key)
            if not sprite or sprite.is_fainted:
                continue
            # 光合回能
            if pos and pos.name == '光合印记':
                gained = sprite.gain_energy(pos.stacks)
                if gained:
                    events.append(f'{sprite.name} 光合+{gained}E')
            # 中毒扣血
            if neg and neg.name == '中毒印记':
                dmg = max(1, round(sprite.max_hp * 0.03 * neg.stacks))
                sprite.take_damage(dmg)
                events.append(f'{sprite.name} 中毒-{dmg}HP')
        return events

    # ── 印记增删 ──

    def apply_mark(self, team: str, name: str, category: str, stacks: int = 1) -> None:
        """应用印记（同类覆盖）。"""
        mark = Mark(name=name, category=category, stacks=stacks)
        if team == 'A':
            if category == 'positive':
                self.pos_mark_a = mark
            else:
                self.neg_mark_a = mark
        else:
            if category == 'positive':
                self.pos_mark_b = mark
            else:
                self.neg_mark_b = mark

    def remove_mark(self, team: str, category: str) -> None:
        if team == 'A':
            if category == 'positive':
                self.pos_mark_a = None
            else:
                self.neg_mark_a = None
        else:
            if category == 'positive':
                self.pos_mark_b = None
            else:
                self.neg_mark_b = None

    def set_weather(self, weather: str, turns: int = WEATHER_DURATION) -> None:
        self.weather = weather
        self.weather_turns = turns

    @staticmethod
    def classify_mark(name: str) -> str:
        """根据名称判断印记正负。"""
        if name in POSITIVE_MARKS:
            return 'positive'
        if name in NEGATIVE_MARKS:
            return 'negative'
        return 'negative'  # 安全默认
