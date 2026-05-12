"""scripts/sim/globals.py — 全局效果（天气 + 双方印记 + 场地）"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sprite import Sprite
    from .skill import Skill


@dataclass
class Mark:
    """单方印记。"""
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

    # 双方印记（list 支持吟游之弦多印记共存）
    pos_marks_a: list[Mark] = field(default_factory=list)
    neg_marks_a: list[Mark] = field(default_factory=list)
    pos_marks_b: list[Mark] = field(default_factory=list)
    neg_marks_b: list[Mark] = field(default_factory=list)

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

    def get_marks(self, team: str) -> tuple[list[Mark], list[Mark]]:
        """返回 (pos_marks, neg_marks)。"""
        if team == 'A':
            return self.pos_marks_a, self.neg_marks_a
        return self.pos_marks_b, self.neg_marks_b

    def get_mark_by_name(self, team: str, name: str) -> Mark | None:
        """获取指定名称的印记。"""
        pos, neg = self.get_marks(team)
        for m in pos + neg:
            if m.name == name:
                return m
        return None

    @staticmethod
    def _get_mark_config(name: str) -> dict:
        return _MARK_EFFECTS.get(name, {})

    def mark_power_bonus(self, team: str, skill: 'Skill') -> int:
        """印记威力加成。从配置读取 power_bonus 字段。"""
        pos, _ = self.get_marks(team)
        total = 0
        for mark in pos:
            cfg = self._get_mark_config(mark.name)
            bonus = cfg.get('power_bonus', 0)
            if bonus and cfg.get('condition') == 'is_attack' and not skill.is_attack:
                continue
            total += bonus * mark.stacks
        return total

    def mark_damage_mult(self, team: str, is_first: bool) -> float:
        """印记伤害倍率。从配置读取 damage_mult 字段。"""
        pos, neg = self.get_marks(team)
        mult = 1.0
        for mark in pos:
            cfg = self._get_mark_config(mark.name)
            dmg_mult = cfg.get('damage_mult', 0)
            condition = cfg.get('condition', '')
            if not dmg_mult:
                continue
            if condition == '' or (condition == 'is_first' and is_first):
                mult += dmg_mult * mark.stacks
        for mark in neg:
            cfg = self._get_mark_config(mark.name)
            dmg_mult = cfg.get('damage_mult', 0)
            condition = cfg.get('condition', '')
            if not dmg_mult:
                continue
            if condition == '' or (condition == 'not_first' and not is_first):
                mult += dmg_mult * mark.stacks
        return mult

    def mark_speed_penalty(self, team: str) -> int:
        """减速印记速度惩罚。从配置读取 speed_penalty 字段。"""
        _, neg = self.get_marks(team)
        total = 0
        for mark in neg:
            cfg = self._get_mark_config(mark.name)
            penalty = cfg.get('speed_penalty', 0)
            total += penalty * mark.stacks
        return total

    def mark_energy_mod(self, team: str) -> int:
        """印记能耗减免。从配置读取 energy_mod 字段。"""
        pos, _ = self.get_marks(team)
        total = 0
        for mark in pos:
            cfg = self._get_mark_config(mark.name)
            mod = cfg.get('energy_mod', 0)
            total += mod * mark.stacks
        return total

    def mark_switch_damage(self, team: str, sprite: 'Sprite') -> int:
        """印记进场伤害。从配置读取 switch_damage_pct 字段。"""
        _, neg = self.get_marks(team)
        total = 0
        for mark in neg:
            cfg = self._get_mark_config(mark.name)
            pct = cfg.get('switch_damage_pct', 0)
            if pct:
                total += max(0, round(sprite.max_hp * pct * mark.stacks))
        return total

    def mark_switch_energy_loss(self, team: str) -> int:
        """印记进场扣能。从配置读取 switch_energy_loss 字段。"""
        _, neg = self.get_marks(team)
        total = 0
        for mark in neg:
            cfg = self._get_mark_config(mark.name)
            loss = cfg.get('switch_energy_loss', 0)
            total += loss * mark.stacks
        return total

    def mark_turn_end_effects(self, sprites: dict[str, 'Sprite']) -> list[str]:
        """回合末印记效果。从配置读取 turn_end_energy / turn_end_damage_pct 字段。"""
        events: list[str] = []
        for team_key in ('A', 'B'):
            pos, neg = self.get_marks(team_key)
            sprite = sprites.get(team_key)
            if not sprite or sprite.is_fainted:
                continue
            # 回合末回能
            for mark in pos:
                cfg = self._get_mark_config(mark.name)
                gain = cfg.get('turn_end_energy', 0)
                if gain:
                    gained = sprite.gain_energy(gain * mark.stacks)
                    if gained:
                        events.append(f'{sprite.name} {mark.name}+{gained}E')
            # 回合末扣血
            for mark in neg:
                cfg = self._get_mark_config(mark.name)
                dmg_pct = cfg.get('turn_end_damage_pct', 0)
                if dmg_pct:
                    dmg = max(1, round(sprite.max_hp * dmg_pct * mark.stacks))
                    sprite.take_damage(dmg)
                    events.append(f'{sprite.name} {mark.name}-{dmg}HP')
        return events

    # ── 印记增删 ──

    def apply_mark(self, team: str, name: str, category: str, stacks: int = 1,
                   user: 'Sprite | None' = None) -> list[str]:
        """应用印记。
        若 user 有吟游之弦特性 → 共存（同名叠加，异名新增）。
        否则 → 替换（同类别清空后新增）。
        返回事件列表。"""
        mark = Mark(name=name, category=category, stacks=stacks)
        if team == 'A':
            pos_list, neg_list = self.pos_marks_a, self.neg_marks_a
        else:
            pos_list, neg_list = self.pos_marks_b, self.neg_marks_b

        target_list = pos_list if category == 'positive' else neg_list
        events: list[str] = []

        # Hook: before_apply_mark — 返回 'coexist' 启用共存模式
        from scripts.sim.traits.trait_engine import fire_hook_first
        hook_mode = fire_hook_first('before_apply_mark', team, name, category, stacks, user)
        has_bard = (hook_mode == 'coexist')
        if not has_bard and user is not None:
            from .traits import get_trait
            h = get_trait(user)
            has_bard = h and h.name == '吟游之弦'

        if has_bard:
            # 共存模式：同名叠加，异名新增
            existing = next((m for m in target_list if m.name == name), None)
            if existing:
                existing.stacks += stacks
            else:
                target_list.append(mark)
        else:
            # 替换模式：同名叠加，异名替换（每类最多1种印记）
            existing = next((m for m in target_list if m.name == name), None)
            if existing:
                existing.stacks += stacks
            else:
                target_list.clear()
                target_list.append(mark)

        return events

    def remove_mark(self, team: str, category: str) -> None:
        if team == 'A':
            if category == 'positive':
                self.pos_marks_a.clear()
            else:
                self.neg_marks_a.clear()
        else:
            if category == 'positive':
                self.pos_marks_b.clear()
            else:
                self.neg_marks_b.clear()

    def consume_starfall_stacks(self, team: str, amount: int, sprite: 'Sprite') -> int:
        """消耗星陨印记层数。若 sprite 有守望星 → 只消耗一半。
        返回实际消耗层数（用于伤害计算）。"""
        _, neg = self.get_marks(team)
        starfall = next((m for m in neg if m.name == '星陨印记'), None)
        if not starfall or starfall.stacks <= 0:
            return 0

        total = starfall.stacks
        consume = amount

        # Hook: before_consume_starfall — 返回调整后的消耗量
        from scripts.sim.traits.trait_engine import fire_hook_first
        hook_consume = fire_hook_first('before_consume_starfall', team, amount, sprite, starfall)
        if hook_consume is not None:
            consume = hook_consume
        elif sprite is not None:
            from .traits import get_trait
            h = get_trait(sprite)
            if h and h.name == '守望星':
                consume = max(1, amount // 2)

        consumed = min(consume, total)
        starfall.stacks -= consumed
        if starfall.stacks <= 0:
            neg.remove(starfall)
        return consumed

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
