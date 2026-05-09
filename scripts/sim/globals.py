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


WEATHER_DURATION = 8  # 天气持续回合


# ── 印记效果配置（集中管理，新增印记只需在此添加）──
# 每个印记条目定义其效果类型和参数。查询方法统一从此配置读取。
_MARK_EFFECTS: dict[str, dict] = {
    '攻击印记': {
        'category': 'positive',
        'power_bonus': 10,           # 每层 +10 威力
    },
    '蓄电印记': {
        'category': 'positive',
        'power_bonus': 10,           # 每层 +10 威力（仅攻击技能）
        'condition': 'is_attack',
    },
    '润泽印记': {
        'category': 'positive',
        'energy_mod': 1,             # 每层 -1 能耗
    },
    '风起': {
        'category': 'positive',
        'damage_mult': 0.20,         # 每层 +20% 伤害
        'condition': 'is_first',
    },
    '光合印记': {
        'category': 'positive',
        'turn_end_energy': 1,        # 每层回合末 +1 能量
    },
    '龙式印记': {
        'category': 'positive',
    },
    '蓄势印记': {
        'category': 'positive',
    },
    '减速': {
        'category': 'negative',
        'speed_penalty': 10,         # 每层 -10 速度
    },
    '迟缓': {
        'category': 'negative',
        'damage_mult': 0.30,         # 每层 +30% 伤害
        'condition': 'not_first',
    },
    '棘刺': {
        'category': 'negative',
        'switch_damage_pct': 0.06,   # 每层进场 6% 最大HP 伤害
    },
    '降临印记': {
        'category': 'negative',
        'switch_energy_loss': 1,     # 每层进场 -1 能量
    },
    '中毒印记': {
        'category': 'negative',
        'turn_end_damage_pct': 0.03, # 每层回合末 3% 最大HP 伤害
    },
    '星陨印记': {
        'category': 'negative',
    },
}

# 从配置派生正/负面印记集合
POSITIVE_MARKS = frozenset(
    name for name, cfg in _MARK_EFFECTS.items() if cfg['category'] == 'positive'
)
NEGATIVE_MARKS = frozenset(
    name for name, cfg in _MARK_EFFECTS.items() if cfg['category'] == 'negative'
)


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

    @staticmethod
    def _get_mark_config(name: str) -> dict:
        return _MARK_EFFECTS.get(name, {})

    def mark_power_bonus(self, team: str, skill: 'Skill') -> int:
        """印记威力加成。从配置读取 power_bonus 字段。"""
        pos, _ = self.get_marks(team)
        if not pos:
            return 0
        cfg = self._get_mark_config(pos.name)
        bonus = cfg.get('power_bonus', 0)
        if bonus and cfg.get('condition') == 'is_attack' and not skill.is_attack:
            return 0
        return bonus * pos.stacks

    def mark_damage_mult(self, team: str, is_first: bool) -> float:
        """印记伤害倍率。从配置读取 damage_mult 字段。"""
        pos, neg = self.get_marks(team)
        mult = 1.0
        for mark, cond_key in [(pos, 'is_first'), (neg, 'not_first')]:
            if not mark:
                continue
            cfg = self._get_mark_config(mark.name)
            dmg_mult = cfg.get('damage_mult', 0)
            condition = cfg.get('condition', '')
            if dmg_mult and (
                condition == ''
                or (condition == 'is_first' and is_first)
                or (condition == 'not_first' and not is_first)
            ):
                mult += dmg_mult * mark.stacks
        return mult

    def mark_speed_penalty(self, team: str) -> int:
        """减速印记速度惩罚。从配置读取 speed_penalty 字段。"""
        _, neg = self.get_marks(team)
        if not neg:
            return 0
        cfg = self._get_mark_config(neg.name)
        penalty = cfg.get('speed_penalty', 0)
        return penalty * neg.stacks

    def mark_energy_mod(self, team: str) -> int:
        """印记能耗减免。从配置读取 energy_mod 字段。"""
        pos, _ = self.get_marks(team)
        if not pos:
            return 0
        cfg = self._get_mark_config(pos.name)
        mod = cfg.get('energy_mod', 0)
        return mod * pos.stacks

    def mark_switch_damage(self, team: str, sprite: 'Sprite') -> int:
        """印记进场伤害。从配置读取 switch_damage_pct 字段。"""
        _, neg = self.get_marks(team)
        if not neg:
            return 0
        cfg = self._get_mark_config(neg.name)
        pct = cfg.get('switch_damage_pct', 0)
        return max(0, round(sprite.max_hp * pct * neg.stacks)) if pct else 0

    def mark_switch_energy_loss(self, team: str) -> int:
        """印记进场扣能。从配置读取 switch_energy_loss 字段。"""
        _, neg = self.get_marks(team)
        if not neg:
            return 0
        cfg = self._get_mark_config(neg.name)
        loss = cfg.get('switch_energy_loss', 0)
        return loss * neg.stacks

    def mark_turn_end_effects(self, sprites: dict[str, 'Sprite']) -> list[str]:
        """回合末印记效果。从配置读取 turn_end_energy / turn_end_damage_pct 字段。"""
        events: list[str] = []
        for team_key in ('A', 'B'):
            pos, neg = self.get_marks(team_key)
            sprite = sprites.get(team_key)
            if not sprite or sprite.is_fainted:
                continue
            # 回合末回能
            if pos:
                cfg = self._get_mark_config(pos.name)
                gain = cfg.get('turn_end_energy', 0)
                if gain:
                    gained = sprite.gain_energy(gain * pos.stacks)
                    if gained:
                        events.append(f'{sprite.name} {pos.name}+{gained}E')
            # 回合末扣血
            if neg:
                cfg = self._get_mark_config(neg.name)
                dmg_pct = cfg.get('turn_end_damage_pct', 0)
                if dmg_pct:
                    dmg = max(1, round(sprite.max_hp * dmg_pct * neg.stacks))
                    sprite.take_damage(dmg)
                    events.append(f'{sprite.name} {neg.name}-{dmg}HP')
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
