"""
scripts/common/ — 属性计算与游戏常量公共模块

从 calc_stats.py / calc_state.py / build_skill_effects.py 中提取的共享代码。
"""

from .constants import (
    STAT_KEYS, STAT_LABELS, STAT_TYPES, LABEL_TO_KEY, STAT_PAT,
    MARK_TYPES, MARK_PAT, TEAM_MARK_NAMES,
    ABNORMAL_TYPES, ABNORMAL_PAT, PERSISTENT_ABNORMALS,
    is_team_mark_effect, is_persistent_abnormal,
)
from .nature import NATURE_TABLE, NATURE_PLUS_COEFF, NATURE_MINUS_COEFF, get_nature_coeff
from .formulas import half_round, RE_MOD, calc_initial_stats, calc_final_stats, apply_mods, StatsCalc
from .models import SpeciesStats, StatsResult
