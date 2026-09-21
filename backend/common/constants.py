"""
scripts/common/constants.py — 游戏常量

包含属性名、印记名、异常状态名等所有脚本共享的常量与辅助函数。
"""

import re

# ═══════════════════════════════════════════════
# 六维属性（种族值 / 能力修正用）
# ═══════════════════════════════════════════════

STAT_KEYS = ('hp', 'atk', 'sp_atk', 'def', 'sp_def', 'speed')

STAT_LABELS: dict[str, str] = {
    'hp': '生命', 'atk': '物攻', 'sp_atk': '魔攻',
    'def': '物防', 'sp_def': '魔防', 'speed': '速度',
}

# 含复合标签（双攻/双防）的完整类型列表，用于正则匹配
STAT_TYPES: list[str] = list(STAT_LABELS.values()) + ['双攻', '双防']
STAT_PAT: str = '|'.join(STAT_TYPES)

LABEL_TO_KEY: dict[str, str | tuple[str, str]] = {v: k for k, v in STAT_LABELS.items()}
LABEL_TO_KEY['双攻'] = ('atk', 'sp_atk')
LABEL_TO_KEY['双防'] = ('def', 'sp_def')


# ═══════════════════════════════════════════════
# 印记（队伍级 / 精灵级）
# ═══════════════════════════════════════════════

MARK_TYPES: list[str] = [
    '星陨印记', '光合印记', '降灵印记', '润泽印记', '蓄势印记', '蓄电印记',
    '龙噬印记', '中毒印记', '减速印记', '棘刺', '迟缓', '风起', '攻击印记',
]
MARK_PAT: str = '|'.join(sorted(MARK_TYPES, key=len, reverse=True))

# 队伍级别印记：换宠后不消失
TEAM_MARK_NAMES: frozenset[str] = frozenset(MARK_TYPES)


def is_team_mark_effect(effect: str) -> bool:
    """判断效果字符串是否为队伍级别印记（如 '星陨印记×3' 或 '蓄势印记'）。"""
    m = re.match(r'^(.+?)×\d+$', effect)
    name = m.group(1) if m else effect
    return name in TEAM_MARK_NAMES


# ═══════════════════════════════════════════════
# 天气（内部键用英文；数据/条件里写中文别名时归一）
# ═══════════════════════════════════════════════

WEATHER_ALIASES: dict[str, str] = {
    'rain': 'rain', '雨天': 'rain',
    'sand': 'sand', '沙暴': 'sand',
    'snow': 'snow', '暴风雪': 'snow',
    'thunder': 'thunder', '雷鸣': 'thunder',
}

#: 天气 → (系别, 伤害倍率)。游戏描述 3008：雨天双方水系技能威力 +75%
WEATHER_DAMAGE_MULT: dict[str, tuple[str, float]] = {
    'rain': ('水', 1.75),
}

#: 天气 → (系别, 能耗倍率)。游戏描述 3006：沙暴时双方地系技能能耗减半
WEATHER_ENERGY_MOD: dict[str, tuple[str, float]] = {
    'sand': ('地', 0.5),
}


def normalize_weather(name: str | None) -> str:
    """天气名归一：中文别名与英文键都映射到内部键；未知名字原样返回。"""
    if not name:
        return ''
    return WEATHER_ALIASES.get(str(name).strip(), str(name).strip())


def weather_damage_mult(weather: str | None, element: str | None) -> float:
    """当前天气下该系别技能的伤害倍率。"""
    spec = WEATHER_DAMAGE_MULT.get(normalize_weather(weather))
    if spec is None or not element:
        return 1.0
    return spec[1] if spec[0] in element else 1.0


def weather_energy_mod(weather: str | None, element: str | None) -> float:
    """当前天气下该系别技能的能耗倍率。"""
    spec = WEATHER_ENERGY_MOD.get(normalize_weather(weather))
    if spec is None or not element:
        return 1.0
    return spec[1] if spec[0] in element else 1.0


# ═══════════════════════════════════════════════
# 异常状态
# ═══════════════════════════════════════════════

ABNORMAL_TYPES: list[str] = ['萌化', '中毒', '寄生', '冻结', '灼烧', '晕眩', '眩晕']
ABNORMAL_PAT: str = '|'.join(sorted(ABNORMAL_TYPES, key=len, reverse=True))

# 换宠后依然保留在精灵身上的异常状态
PERSISTENT_ABNORMALS: frozenset[str] = frozenset(ABNORMAL_TYPES)


def is_persistent_abnormal(effect: str) -> bool:
    """判断是否为换宠后依然持续的异常状态。"""
    return effect in PERSISTENT_ABNORMALS


# ═══════════════════════════════════════════════
# 血脉（18 系 + 首领 + 污染 + 奇异 = 21 种）
# ═══════════════════════════════════════════════

ELEMENTAL_BLOODLINES: list[str] = [
    '普通', '火', '水', '草', '电', '冰', '地', '石',
    '武', '虫', '翼', '萌', '毒', '幽', '恶', '幻',
    '光', '龙', '机械',
]

SPECIAL_BLOODLINES: list[str] = ['首领', '污染', '奇异']

BLOODLINES: list[str] = ELEMENTAL_BLOODLINES + SPECIAL_BLOODLINES

# 不可变更的血脉
LOCKED_BLOODLINES: frozenset[str] = frozenset(['污染', '奇异'])


# ═══════════════════════════════════════════════
# AI 动作空间布局（NUM_ACTIONS = 22）
#   0-9  技能    10-14 换宠    15 聚能
#   16   愿力
#   17-21 进化之力的首领形态槽位（玩家选择目标形态，如圣光/圣水/圣火/圣草迪莫）
# ═══════════════════════════════════════════════

ITEM_VARIANT_SLOTS: int = 5
ITEM_VARIANT_ACTION_BASE: int = 17
