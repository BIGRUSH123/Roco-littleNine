"""backend/sim/battle_context.py — Battle 最小接口协议

定义 traits 系统和 SkillPipeline 实际需要的 11 项 Battle 接口。
Battle 类隐式满足此协议；当前作为文档和未来解耦路径使用。
"""

from __future__ import annotations
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .player import Player
    from .globals import GlobalEffects


class BattleContext(Protocol):
    """traits / 管线所需的最小 Battle 接口。

    Battle 已满足全部项目，无需显式继承。
    """

    # ── 队伍查询 ──
    def get_player(self, team: str) -> 'Player': ...
    def get_opponent(self, team: str) -> 'Player': ...

    # ── 全局状态 ──
    globals: 'GlobalEffects'
    scheduled_effects: list[dict]
    pending_effects: dict[str, list]
    team_counters: dict[str, dict[str, int]]

    # ── 队伍计数器 ──
    def inc_team_counter(self, team: str, key: str, amount: int = 1) -> None: ...
    def get_team_counter(self, team: str, key: str) -> int: ...

    # ── 回合 ──
    turn: int

    # ── 图鉴/技能构造（形态变换用）──
    def lookup_species(self, name: str, form: str = '') -> object | None: ...
    def lookup_species_by_number(self, number: str, form: str = '') -> object | None: ...
    def build_skills(self, skill_names: list[str]) -> list: ...
