"""
scripts/common/formulas.py — 属性计算公式

精灵六维属性计算：
  洛克系数 L = (种族值 + IV × 3) / 100
  初始HP   = 170L + 70
  初始其他 = 110L + 10
  最终HP   = 初始HP × 性格系数 + 100
  最终其他 = 初始其他 × 性格系数 + 50
"""

import math
import re
from typing import Optional

from .constants import STAT_KEYS, STAT_LABELS, LABEL_TO_KEY
from .nature import get_nature_coeff
from .models import SpeciesStats, StatsResult


def half_round(x: float) -> int:
    """正向四舍五入（避免 Python 银行家舍入对 .5 取偶）。"""
    if x >= 0:
        return int(x + 0.5)
    return -int(-x + 0.5)


def calc_initial_stats(base: dict[str, int], iv: dict[str, int]) -> dict[str, int]:
    """计算初始六维。HP 用四舍五入，其他属性向下取整。"""
    result: dict[str, int] = {}
    for k in STAT_KEYS:
        L = (base[k] + iv.get(k, 0) * 3) / 100.0
        if k == 'hp':
            result[k] = half_round(170 * L + 70)
        else:
            result[k] = math.floor(110 * L + 10)
    return result


def calc_final_stats(initial: dict[str, int], coeff: dict[str, float]) -> dict[str, int]:
    """应用性格系数和最终常数（HP +100，其他 +50）。"""
    result: dict[str, int] = {}
    for k in STAT_KEYS:
        if k == 'hp':
            result[k] = half_round(initial[k] * coeff[k] + 100)
        else:
            result[k] = half_round(initial[k] * coeff[k] + 50)
    return result


# ═══════════════════════════════════════════════
# 能力修正解析与应用
# ═══════════════════════════════════════════════

RE_MOD = re.compile(
    r'^\s*(双攻|双防|生命|物攻|魔攻|物防|魔防|速度)\s*'
    r'([+\-])\s*(\d+(?:\.\d+)?)\s*(%?)\s*$'
)


def apply_mods(stats: dict[str, int], mods: list[str]) -> dict[str, int]:
    """
    应用能力修正列表到属性上。
    mods 形如 ["物攻+100%", "速度-30", "双攻+50%"]。
    返回新 dict，不修改输入。
    """
    pct  = {k: 0.0 for k in STAT_KEYS}
    flat = {k: 0   for k in STAT_KEYS}

    for raw in mods:
        m = RE_MOD.match(raw)
        if not m:
            continue
        label, sign, num_s, percent = m.group(1), m.group(2), m.group(3), m.group(4)
        num = float(num_s) * (1 if sign == '+' else -1)
        keys = LABEL_TO_KEY.get(label)
        if keys is None:
            continue
        target_keys = keys if isinstance(keys, tuple) else (keys,)
        for k in target_keys:
            if percent == '%':
                pct[k] += num / 100.0
            else:
                flat[k] += int(num)

    result: dict[str, int] = {}
    for k, v in stats.items():
        scaled = v * (1.0 + pct[k]) + flat[k]
        result[k] = max(0, half_round(scaled))
    return result


# ═══════════════════════════════════════════════
# StatsCalc — 属性计算器
# ═══════════════════════════════════════════════

class StatsCalc:
    """精灵六维属性完整计算管线。"""

    @staticmethod
    def compute(
        species: SpeciesStats,
        nature: Optional[str] = None,
        iv:     Optional[dict[str, int]] = None,
        mods:   Optional[list[str]] = None,
        ability: str = '',
    ) -> StatsResult:
        iv   = iv   or {k: 0 for k in STAT_KEYS}
        mods = mods or []

        base       = species.base_dict()
        coeff      = get_nature_coeff(nature)
        initial    = calc_initial_stats(base, iv)
        nature_stats = calc_final_stats(initial, coeff)
        final_stats = apply_mods(nature_stats, mods)

        return StatsResult(
            species=species, nature=nature, iv=dict(iv),
            mods=list(mods), ability=ability or species.ability,
            base_stats=dict(base),
            raw_stats=initial,
            nature_stats=nature_stats,
            final_stats=final_stats,
        )
