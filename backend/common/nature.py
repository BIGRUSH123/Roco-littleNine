"""
scripts/common/nature.py — 性格修正系统

从 wiki/对战机制/宠物性格修正表.md 提取的 30 个性格。
"""


from .constants import STAT_KEYS

# value: (加项 stat_key, 减项 stat_key)；同项加减抵消时为 (None, None) 但官方不存在
NATURE_TABLE: dict[str, tuple[str, str]] = {
    '聪明': ('sp_atk', 'atk'),    '专注': ('sp_atk', 'def'),
    '偏执': ('sp_atk', 'sp_def'), '冷静': ('sp_atk', 'speed'),
    '理性': ('sp_atk', 'hp'),

    '固执': ('atk', 'sp_atk'),    '大胆': ('atk', 'def'),
    '调皮': ('atk', 'sp_def'),    '勇敢': ('atk', 'speed'),
    '逞强': ('atk', 'hp'),

    '警惕': ('sp_def', 'atk'),    '害羞': ('sp_def', 'sp_atk'),
    '温顺': ('sp_def', 'def'),    '慎重': ('sp_def', 'speed'),
    '焦虑': ('sp_def', 'hp'),

    '稳重': ('def', 'atk'),       '天真': ('def', 'sp_atk'),
    '悠闲': ('def', 'speed'),     '懒散': ('def', 'sp_def'),
    '坦率': ('def', 'hp'),

    '胆小': ('speed', 'atk'),     '开朗': ('speed', 'sp_atk'),
    '急躁': ('speed', 'def'),     '莽撞': ('speed', 'sp_def'),
    '热情': ('speed', 'hp'),

    '沉默': ('hp', 'atk'),        '平和': ('hp', 'sp_atk'),
    '忧郁': ('hp', 'def'),        '粗心': ('hp', 'sp_def'),
    '踏实': ('hp', 'speed'),
}

NATURE_PLUS_COEFF: float  = 1.20   # +20%
NATURE_MINUS_COEFF: float = 0.90   # -10%


def get_nature_coeff(nature: str | None) -> dict[str, float]:
    """返回每项 stat 的性格系数（1.20 / 0.90 / 1.00）。"""
    coeffs = {k: 1.0 for k in STAT_KEYS}
    if not nature:
        return coeffs
    if nature not in NATURE_TABLE:
        raise ValueError(f"未识别的性格：{nature}（共30种，详见 wiki/对战机制/宠物性格修正表.md）")
    plus, minus = NATURE_TABLE[nature]
    coeffs[plus] = NATURE_PLUS_COEFF
    coeffs[minus] = NATURE_MINUS_COEFF
    return coeffs
