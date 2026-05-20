"""
scripts/common/ — 属性计算与游戏常量公共模块

从 calc/stats.py / calc/state.py 中提取的共享代码。
"""

from .constants import (
    ABNORMAL_PAT,
    ABNORMAL_TYPES,
    LABEL_TO_KEY,
    MARK_PAT,
    MARK_TYPES,
    PERSISTENT_ABNORMALS,
    STAT_KEYS,
    STAT_LABELS,
    STAT_PAT,
    STAT_TYPES,
    TEAM_MARK_NAMES,
    is_persistent_abnormal,
    is_team_mark_effect,
)
from .formulas import (
    RE_MOD,
    StatsCalc,
    apply_mods,
    calc_final_stats,
    calc_initial_stats,
    half_round,
)
from .models import SpeciesStats, StatsResult
from .nature import NATURE_MINUS_COEFF, NATURE_PLUS_COEFF, NATURE_TABLE, get_nature_coeff
from .sprite_db import SpriteDB
