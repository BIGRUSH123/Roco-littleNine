"""scripts/sim/player.py — 玩家 + 操作习惯画像 + 道具"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sprite import Sprite


@dataclass
class Item:
    """玩家携带的道具。决定特殊操作类型与使用次数上限。"""

    name: str                   # "进化之力" | "愿力"
    max_uses: int               # 全场最大使用次数
    cooldown_turns: int = 0     # 两次使用间的最短回合间隔（0=无冷却）
    uses: int = 0               # 已使用次数
    last_use_turn: int = 0      # 最后使用的回合号

    @property
    def is_exhausted(self) -> bool:
        return self.uses >= self.max_uses

    def can_use(self, current_turn: int) -> bool:
        """当前回合是否可使用。"""
        if self.is_exhausted:
            return False
        if self.cooldown_turns and self.last_use_turn > 0:
            if current_turn - self.last_use_turn < self.cooldown_turns:
                return False
        return True

    def use(self, turn: int) -> None:
        self.uses += 1
        self.last_use_turn = turn

    @classmethod
    def leader(cls) -> 'Item':
        """进化之力：首领化，全场 1 次。"""
        return cls(name='进化之力', max_uses=1)

    @classmethod
    def wish(cls) -> 'Item':
        """愿力：全场 2 次，间隔≥4 回合（含使用回合间隔3回合）。"""
        return cls(name='愿力', max_uses=2, cooldown_turns=4)


@dataclass
class PlayStyle:
    """操作习惯画像。权重用于基于规则的 AI 决策。"""

    aggression: float = 0.5
    switch_hp_threshold: float = 0.3
    switch_type_threshold: float = 0.5
    gather_energy_threshold: int = 2
    risk_tolerance: float = 0.5
    prediction_accuracy: float = 0.5
    skill_weights: dict[str, float] = field(default_factory=dict)
    prefer_first_strike: float = 0.5


@dataclass
class Player:
    """对局中的玩家。"""

    name: str
    team: list['Sprite'] = field(default_factory=list)
    style: PlayStyle = field(default_factory=PlayStyle)
    lives: int = 4
    active_index: int = 0
    item: Item | None = None
    devotion: dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0, 5: 0})

    @property
    def active(self) -> 'Sprite':
        return self.team[self.active_index]

    @property
    def alive_sprites(self) -> list[int]:
        """返回未力竭精灵的索引列表。"""
        return [i for i, s in enumerate(self.team) if not s.is_fainted]

    def find_replacement(self) -> int | None:
        """找到第一个存活的可替换精灵索引。"""
        alive = self.alive_sprites
        for i in alive:
            if i != self.active_index:
                return i
        return None
