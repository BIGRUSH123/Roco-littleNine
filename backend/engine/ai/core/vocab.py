"""backend/engine/ai/core/vocab.py — 战斗指令词表（序列化用）

基于 `data/IR_GUIDE.md` 的第 1-6 节构建硬编码静态词表，
覆盖所有结构标记、键名、操作码、枚举值和特殊占位符。

词表用途:
  - Tokenizer: 将 IR 指令 (dict) 序列化为 token_id 数组
  - Detokenizer: 将 token_id 数组还原为 IR 指令
  - 模型输入: 作为序列模型 (Transformer) 的词表嵌入
"""

from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════
# 1. 结构控制符 (Structural Tokens)
# ═══════════════════════════════════════════════════════════════════

STRUCTURAL_TOKENS = [
    "<PAD>",              # 0 — must be index 0 for padding
    "<UNK>",              # 1 — unknown token fallback
    "<SEP>",              # 2 — separator between fields / effects
    "<B_EFFECT>", "<E_EFFECT>",  # 3-4  — effect block boundaries
    "<B_WHEN>", "<E_WHEN>",      # 5-6  — when conditional block
    "<B_THEN>", "<E_THEN>",      # 7-8  — then block
    "<B_ELSE>", "<E_ELSE>",      # 9-10 — else block
    "<B_COND>", "<E_COND>",      # 11-12 — composite condition
    "<B_QUERY>", "<E_QUERY>",    # 13-14 — dynamic query
    # ── 技能槽位哨兵（10-slot 对齐） ──
    "<EMPTY_SKILL>",       # 15 — 空槽位（i >= len(skills)）
    "<SEALED_SKILL>",      # 16 — 被封印/冷却中
    "<ACTIVE_SKILL>",      # 17 — 正常可用
]


# ═══════════════════════════════════════════════════════════════════
# 2. 核心键名 (Keys) — 覆盖 IR_GUIDE 中所有指令字段
# ═══════════════════════════════════════════════════════════════════

KEY_TOKENS = [
    # ── 通用 ──
    "KEY_OP", "KEY_TARGET",
    # ── 值 / 变换 ──
    "KEY_VALUE", "KEY_STEPS", "KEY_RATIO", "KEY_DELTA",
    "KEY_SCALE", "KEY_OFFSET", "KEY_PER", "KEY_DEFAULT",
    # ── 属性 / 状态 ──
    "KEY_ATTR", "KEY_STAT", "KEY_FLAG", "KEY_NAME",
    "KEY_MARK",
    "KEY_SCOPE", "KEY_SOURCE",
    # ── 筛选 / 条件 ──
    "KEY_COND", "KEY_LISTEN", "KEY_WHEN",
    "KEY_SKILL_WHERE", "KEY_SKILL_FILTER", "KEY_ELEMENT",
    "KEY_PER_ELEMENT", "KEY_EXCLUDE_CARRIED",
    # ── 类型 / 模式 ──
    "KEY_TYPE", "KEY_MODE", "KEY_TAG",
    "KEY_SKILL_TYPE", "KEY_ENERGY_COST",
    "KEY_POSITION",
    # ── 查询 ──
    "KEY_Q", "KEY_OF",
    # ── 流控 ──
    "KEY_THEN", "KEY_ELSE", "KEY_ELSE_IF",
    "KEY_COUNTER", "KEY_RESET", "KEY_AT",
    "KEY_TURNS", "KEY_LIMIT", "KEY_TYPE_LIMIT",
    # ── 动作 ──
    "KEY_ACTION", "KEY_KEY", "KEY_TARGET_TEAM",
    # ── 特殊 ──
    "KEY_WHAT", "KEY_FROM", "KEY_AMOUNT",
    "KEY_SPECIES", "KEY_SKILLS", "KEY_EFFECTS",
    "KEY_INHERIT", "KEY_URGENT",
    "KEY_WEATHER", "KEY_STACKS",
    "KEY_POWER", "KEY_HP_RATIO",
    "KEY_COUNT",
    # ── trait_path / 路径 ──
    "KEY_PATH",
    # ── 内部注解 ──
    "KEY_FEEDS",
]


# ═══════════════════════════════════════════════════════════════════
# 3. 操作码 (Opcodes) — 覆盖 IR_GUIDE 第 3 节全部指令
# ═══════════════════════════════════════════════════════════════════

OP_TOKENS = [
    # ── 3A. 寄存器修改类 ──
    "OP_STAT_STAGE",
    "OP_POWER_MOD",
    "OP_MULT_MOD",
    "OP_FLAG_SET",
    "OP_HEAL",
    "OP_ENERGIZE",
    "OP_REVIVE",
    # ── 3B. 状态效果类 ──
    "OP_MARK",
    "OP_ABNORMAL",
    "OP_WEATHER",
    "OP_DISPEL",
    "OP_STEAL",
    "OP_TICK",
    "OP_DOUBLE",
    # ── 3C. 战斗流控类 ──
    "OP_HIT",
    "OP_CHARGE",
    "OP_ESCAPE",
    "OP_RETURN",
    "OP_LOCK",
    "OP_INTERRUPT",
    "OP_EXCHANGE",
    "OP_RESET",
    "OP_REDIRECT",
    "OP_REPLAY",
    "OP_BORROW",
    # ── 3D. 持久化/复合类 ──
    "OP_OBSERVER",
    "OP_DEFER",
    "OP_INHERIT",
    "OP_TRANSFORM",
    "OP_TEAM_COUNTER",
    "OP_LIVES",
    "OP_TRAIT_INTERACTION",
    "OP_COUNT",              # 旧版（编译器转换为 observer）
    "OP_SCHEDULE",           # 旧版（编译器转换为 defer）
    "OP_BURST_GRANT",
    "OP_GAIN_SKILLS",
    "OP_EFFECT_DELTA",
    "OP_MOD",                # 通用修改器
]


# ═══════════════════════════════════════════════════════════════════
# 4. 目标 (Targets) — 覆盖 IR_GUIDE 第 3 节 target 合法值
# ═══════════════════════════════════════════════════════════════════

TARGET_TOKENS = [
    "TGT_SPRITE_SELF",
    "TGT_SPRITE_OPP",
    "TGT_TEAM_OWN",
    "TGT_TEAM_OPP",
    "TGT_TEAM_BOTH",
    "TGT_TEAM_OWN_BENCHED",
    "TGT_TEAM_OPP_BENCHED",
    "TGT_SKILL_OFF_0",
    "TGT_SKILL_AT_1",
    "TGT_SKILL_AT_2",
    "TGT_SKILL_AT_3",
    "TGT_SKILL_AT_4",
    "TGT_SKILL_OPP_CURRENT",
    "TGT_BATTLE",
    "TGT_ALLY_NEW",           # inherit / 入场继承
    "TGT_ENEMY_NEW",          # inherit target
    "TGT_SPRITE_BENCH",       # bench random selection
]


# ═══════════════════════════════════════════════════════════════════
# 5. 条件 (Conditions) — 覆盖 IR_GUIDE 第 5 节全部 cond
# ═══════════════════════════════════════════════════════════════════

CONDITION_TOKENS = [
    # ── 逻辑组合 ──
    "COND_AND", "COND_OR", "COND_NOT",
    # ── 应对/响应类 ──
    "COND_COUNTER_SUCCEEDED",
    "COND_SELF_WAS_COUNTERED",
    "COND_PREV_COUNTER_SUCCEEDED",
    # ── 蓄力/行动状态 ──
    "COND_CHARGED",
    "COND_IS_CHARGING",
    "COND_BURST",
    "COND_FIRST_ACTION",
    "COND_FIRST_ACTION_BATTLE",
    # ── KO / 伤害 ──
    "COND_ON_KO",
    "COND_ON_SELF_KO",
    "COND_ON_DAMAGE_TAKEN",
    "COND_DAMAGE_RESTRAINT",
    "COND_PREV_DAMAGE_TAKEN",
    # ── 切换 ──
    "COND_OPP_SWITCHED",
    "COND_SELF_SWITCHED",
    "COND_SPRITE_LEFT",
    # ── 技能类型检查 ──
    "COND_OPP_IS_ATTACK",
    "COND_PREV_SKILL_IS",
    # ── 回合顺序 ──
    "COND_IS_FIRST",
    "COND_IS_SECOND",
    # ── HP / 能量阈值 ──
    "COND_HP_BELOW",
    "COND_ENERGY_LE",
    "COND_ENERGY_EQ",
    "COND_ENERGY_DEPLETED",
    # ── 天气 ──
    "COND_WEATHER_IS",
    # ── 技能位置 ──
    "COND_SKILL_AT",
    "COND_SKILL_POSITION_CHANGED",
    # ── 技能使用/元素 ──
    "COND_SKILL_USE",
    "COND_HAVE_SKILL_OF",
    # ── 入场/行动/状态变化事件 ──
    "COND_SPRITE_ENTERED",
    "COND_SPRITE_ACTED",
    "COND_ON_ABNORMAL_TICK",
    "COND_ON_ABNORMAL_CHANGED",
    "COND_ON_ABNORMAL_APPLIED",
    "COND_ON_SKILLS_ENERGY_CHANGED",
    "COND_ON_POSITIVE_CHANGED",
    "COND_ON_ENERGY_CHANGED",
    "COND_ON_HEAL",
    "COND_TURN_END",
    "COND_ROUND_END",
    # ── 队伍条件 ──
    "COND_TEAM_HAS_ELEMENT",
    # ── 通用比较 ──
    "COND_COMPARE",
    # ── 特性路径 ──
    "COND_TRAIT_PATH",
    # ── 回合边界 ──
    "COND_TURN_START",
    "COND_ALWAYS",
    # ── 奉献 ──
    "COND_DEVOTION_TRIGGERED",
    # ── 复合查询 ──
    "COND_HAVE",
]


# ═══════════════════════════════════════════════════════════════════
# 6. 属性 (Stats) — 六维 + 派生
# ═══════════════════════════════════════════════════════════════════

STAT_TOKENS = [
    "STAT_ATK",
    "STAT_DEF",
    "STAT_SP_ATK",
    "STAT_SP_DEF",
    "STAT_SPEED",
    "STAT_HP",
]


# ═══════════════════════════════════════════════════════════════════
# 7. 技能属性 (Attrs for power_mod / mult_mod)
# ═══════════════════════════════════════════════════════════════════

ATTR_TOKENS = [
    # power_mod attr
    "ATTR_POWER",
    "ATTR_ENERGY_COST",
    "ATTR_COMBO",
    "ATTR_PRIORITY",
    "ATTR_ENERGY_COST_MULT",
    "ATTR_COMBO_MULT",
    "ATTR_ENERGY_COST_DELTA_MULT",
    # mult_mod attr
    "ATTR_POWER_MULT",
    "ATTR_DAMAGE_MULT",
    "ATTR_DAMAGE_REDUCTION",
    "ATTR_LIFE_DRAIN",
]


# ═══════════════════════════════════════════════════════════════════
# 8. 作用域 (Scopes)
# ═══════════════════════════════════════════════════════════════════

SCOPE_TOKENS = [
    "SCOPE_BATTLEFIELD",
    "SCOPE_PERSISTENT",
    "SCOPE_PERMANENT",
    "SCOPE_TURN",
]


# ═══════════════════════════════════════════════════════════════════
# 9. 监听点 (Listen / Trigger Points)
# ═══════════════════════════════════════════════════════════════════

LISTEN_TOKENS = [
    "LISTEN_PRE_CALC",
    "LISTEN_POST_SKILL",
    "LISTEN_POST_HIT",
    "LISTEN_TURN_END",
    "LISTEN_ON_ENTRY",
    "LISTEN_ON_LEAVE",
    "LISTEN_POST_DAMAGE_TAKEN",
    "LISTEN_ON_ABNORMAL_TICK",
    "LISTEN_ON_ABNORMAL_CHANGED",
    "LISTEN_ON_ABNORMAL_APPLIED",
    "LISTEN_ON_HEAL",
    "LISTEN_ON_ENERGY_CHANGED",
    "LISTEN_ON_POSITIVE_CHANGED",
    "LISTEN_ON_SKILLS_ENERGY_CHANGED",
    "LISTEN_ON_KO",
    "LISTEN_ON_SELF_KO",
    "LISTEN_ON_SWITCH",
    "LISTEN_PRE_DEFEND",
]


# ═══════════════════════════════════════════════════════════════════
# 10. 模式 (Modes)
# ═══════════════════════════════════════════════════════════════════

MODE_TOKENS = [
    "MODE_ADD",
    "MODE_SET",
]


# ═══════════════════════════════════════════════════════════════════
# 11. 标记 (Flags) — 覆盖 IR_GUIDE 3A flag_set 合法值
# ═══════════════════════════════════════════════════════════════════

FLAG_TOKENS = [
    "FLAG_IMMUNE",
    "FLAG_FREEZE_IMMUNE",
    "FLAG_SURVIVE",
    "FLAG_CHARGED",
    "FLAG_PRE_CHARGED",
    "FLAG_DRIVE",
    "FLAG_SWIFT",
    "FLAG_EXTRA_ACTION",
    "FLAG_EXTRA_TURN_END",
    "FLAG_HEAL_REVERSE",
    "FLAG_LIFE_AS_ENERGY",
    "FLAG_IGNORE_MODS",
    "FLAG_IGNORE_RESISTANCE",
    "FLAG_COOLDOWN",
    "FLAG_NO_SELF_DAMAGE",
    "FLAG_TICK_REDUCE",
    "FLAG_ABNORMAL_TICK_INVERT",
    "FLAG_UNLIMITED_ABNORMAL",
    "FLAG_CHARGE_ANY_SKILL",
    "FLAG_USABLE_WHILE_CHARGING",
]


# ═══════════════════════════════════════════════════════════════════
# 12. 天气 (Weather)
# ═══════════════════════════════════════════════════════════════════

WEATHER_TOKENS = [
    "WTH_RAIN",
    "WTH_SAND",
    "WTH_SNOW",
    "WTH_BLIZZARD",
]


# ═══════════════════════════════════════════════════════════════════
# 13. 异常 (Abnormals)
# ═══════════════════════════════════════════════════════════════════

ABNORMAL_TOKENS = [
    "ABN_BURN",         # 灼烧
    "ABN_FREEZE",       # 冻结
    "ABN_POISON",       # 中毒
    "ABN_PARASITE",     # 寄生
    "ABN_MOE",          # 萌化
    "ABN_DIZZY",        # 晕眩
    "ABN_STUN",         # 眩晕
]


# ═══════════════════════════════════════════════════════════════════
# 14. 印记 (Marks) — 正/负
# ═══════════════════════════════════════════════════════════════════

MARK_TOKENS = [
    # 正印记
    "MARK_ATK_UP",          # 攻击印记
    "MARK_CHARGE_ELECTRIC", # 蓄电印记
    "MARK_MOISTURIZE",      # 润泽印记
    "MARK_WET",             # 湿润印记
    "MARK_WIND_UP",         # 风起
    "MARK_PHOTOSYNTHESIS",  # 光合印记
    "MARK_DRAGON_BITE",     # 龙噬印记
    # 负印记
    "MARK_SLOW",            # 减速
    "MARK_SLUGGISH",        # 迟缓
    "MARK_THORN",           # 棘刺
    "MARK_SPIRIT_DOWN",     # 降灵印记
    "MARK_POISON_SEAL",     # 中毒印记
    "MARK_STARFALL",        # 星陨印记
]


# ═══════════════════════════════════════════════════════════════════
# 15. 元素 / 系别 (Elements)
# ═══════════════════════════════════════════════════════════════════

ELEMENT_TOKENS = [
    "ELEM_LIGHT",      # 光
    "ELEM_ICE",        # 冰
    "ELEM_EARTH",      # 地
    "ELEM_ILLUSION",   # 幻
    "ELEM_GHOST",      # 幽
    "ELEM_DARK",       # 恶
    "ELEM_NORMAL",     # 普通
    "ELEM_MACHINE",    # 机械
    "ELEM_FIGHT",      # 武
    "ELEM_POISON",     # 毒
    "ELEM_WATER",      # 水
    "ELEM_FIRE",       # 火
    "ELEM_ELECTRIC",   # 电
    "ELEM_WING",       # 翼
    "ELEM_GRASS",      # 草
    "ELEM_CUTE",       # 萌
    "ELEM_BUG",        # 虫
    "ELEM_DRAGON",     # 龙
]


# ═══════════════════════════════════════════════════════════════════
# 16. 技能类型 (Skill Types)
# ═══════════════════════════════════════════════════════════════════

SKILL_TYPE_TOKENS = [
    "SKTYPE_PHYSICAL",     # 物攻
    "SKTYPE_SPECIAL",      # 魔攻
    "SKTYPE_DYNAMIC",      # 动态攻击
    "SKTYPE_DEFENSIVE",    # 防御
    "SKTYPE_STATUS",       # 状态
]


# ═══════════════════════════════════════════════════════════════════
# 17. 技能筛选器 (Skill Filters — power_mod 的 skill_filter)
# ═══════════════════════════════════════════════════════════════════

SKILL_FILTER_TOKENS = [
    "SKFILT_ATTACK",
    "SKFILT_DEFENSE",
    "SKFILT_STATUS",
    "SKFILT_ALL",
    "SKFILT_OTHERS",
    "SKFILT_ADJACENT",
    "SKFILT_BARE_ATTACK",
    "SKFILT_BARE_DEFENSE",
    "SKFILT_BARE_STATUS",
]


# ═══════════════════════════════════════════════════════════════════
# 18. What 类型 (dispel / steal / double / exchange)
# ═══════════════════════════════════════════════════════════════════

WHAT_TOKENS = [
    "WHAT_POSITIVE",
    "WHAT_NEGATIVE",
    "WHAT_MARK",
    "WHAT_ABNORMAL",
    "WHAT_ENERGY",
    "WHAT_HP_RATIO",
    "WHAT_EFFECTS",
    "WHAT_SKILLS",
    "WHAT_ADJACENT_SKILLS",
    "WHAT_BURST",
]


# ═══════════════════════════════════════════════════════════════════
# 19. of 值 (Query 来源)
# ═══════════════════════════════════════════════════════════════════

OF_TOKENS = [
    "OF_SPRITE_SELF",
    "OF_SPRITE_OPP",
    "OF_TEAM_OWN",
    "OF_TEAM_OPP",
    "OF_TEAM_BOTH",
    "OF_SKILL_OFF_0",
    "OF_SKILL_OPP_CURRENT",
    "OF_BATTLE",
]


# ═══════════════════════════════════════════════════════════════════
# 20. q 值 (Query 查询字段) — 覆盖 IR_GUIDE 1.3 ADDRESS_MAP
# ═══════════════════════════════════════════════════════════════════

QUERY_Q_TOKENS = [
    # ── 精灵属性 ──
    "Q_HP", "Q_HP_RATIO", "Q_HP_MAX", "Q_HP_MISSING_RATIO",
    "Q_ENERGY", "Q_ENERGY_COST", "Q_SKILLS_ENERGY_SUM",
    "Q_ATK", "Q_DEF", "Q_SP_ATK", "Q_SP_DEF", "Q_SPEED",
    "Q_PRIORITY", "Q_CHARGED", "Q_IS_CHARGING",
    "Q_FIRST_ACTION", "Q_FIRST_ACTION_BATTLE",
    "Q_BLOODLINE", "Q_ELEMENTS",
    # ── 异常 ──
    "Q_ABNORMAL_COUNT", "Q_ABNORMAL_STACKS",
    # ── 增益 ──
    "Q_POSITIVE_COUNT", "Q_ZERO_COST_SKILL_COUNT",
    # ── 入场/离场 ──
    "Q_TIMES_ENTERED", "Q_TIMES_LEFT",
    "Q_ELEMENTS_USED_COUNT",
    # ── 伤害 / 减免 ──
    "Q_DAMAGE_REDUCTION", "Q_DAMAGE_REDUCED",
    "Q_LAST_TICK_DAMAGE",
    "Q_ELEMENT_ADVANTAGE",
    # ── 倍率 ──
    "Q_POWER_MULT", "Q_DAMAGE_MULT",
    "Q_ENERGY_COST_MULT", "Q_COMBO_MULT",
    "Q_LIFE_DRAIN", "Q_MARK_BONUS",
    # ── 能耗合计 (子索引: skill_type/element/tag) ──
    "Q_ENERGY_COST_SUM",
    # ── 技能属性 ──
    "Q_POWER_BASE", "Q_COMBO_CURRENT",
    "Q_COUNTER_VALUE", "Q_ENERGY_COST_REDUCTION",
    "Q_ENERGY_TOTAL", "Q_ADJACENT_POWER_SUM",
    # ── 队伍属性 ──
    "Q_MARK_COUNT", "Q_MARK_STACKS",
    "Q_SKILL_COUNT", "Q_TEAM_COUNTER",
    "Q_DEVOTION", "Q_FAINTED",
    "Q_BURST_TRIGGERED_COUNT",
    "Q_MOE_STACKS", "Q_LIVES",
    "Q_TEAM_ELEMENTS",
    # ── 瞬时事件 ──
    "Q_ENERGY_DELTA", "Q_HEAL_DELTA",
    # ── 派生（resolve.py 动态计算） ──
    "Q_IS_FAINTED",
]


# ═══════════════════════════════════════════════════════════════════
# 21. from 值 (replay / borrow)
# ═══════════════════════════════════════════════════════════════════

FROM_TOKENS = [
    "FROM_SPRITE_SELF",       # replay: 从自己历史
    "FROM_TEAM_BURST",        # replay: 从队伍迸发历史
    "FROM_SKILL_OPP_CURRENT", # borrow: 从对方当前技能
]


# ═══════════════════════════════════════════════════════════════════
# 22. at 值 (defer 执行时机)
# ═══════════════════════════════════════════════════════════════════

AT_TOKENS = [
    "AT_TURN_START",
    "AT_TURN_END",
]


# ═══════════════════════════════════════════════════════════════════
# 23. 动作 (Action — trait_interaction 等)
# ═══════════════════════════════════════════════════════════════════

ACTION_TOKENS = [
    "ACT_SUPPRESS",           # trait_interaction: 压制特性
    "ACT_COPY",               # trait_interaction: 复制特性
    "ACT_REMOVE",             # trait_interaction: 移除特性
]


# ═══════════════════════════════════════════════════════════════════
# 24. 来源 (Source — gain_skills)
# ═══════════════════════════════════════════════════════════════════

SOURCE_TOKENS = [
    "SRC_LEARNSET",
    "SRC_GLOBAL",
]


# ═══════════════════════════════════════════════════════════════════
# 25. 应对类型 (Counter types)
# ═══════════════════════════════════════════════════════════════════

COUNTER_TOKENS = [
    "CNT_NONE",        # 无
    "CNT_ATTACK",      # 攻击
    "CNT_DEFENSE",     # 防御
    "CNT_STATUS",      # 状态
]


# ═══════════════════════════════════════════════════════════════════
# 26. 血脉 (Bloodlines)
# ═══════════════════════════════════════════════════════════════════

BLOODLINE_TOKENS = [
    "BLD_LEGENDARY",   # 首领
    "BLD_DEMON",       # 恶魔
    "BLD_FIELD",       # 场地
]


# ═══════════════════════════════════════════════════════════════════
# 27. 技能标签 (Skill Tags)
# ═══════════════════════════════════════════════════════════════════

TAG_TOKENS = [
    "TAG_SWIFT",       # 迅捷
    "TAG_DRIVE",       # 传动
]


# ═══════════════════════════════════════════════════════════════════
# 28. 比较运算符 (Comparison operators — cond.compare 用)
# ═══════════════════════════════════════════════════════════════════

CMP_TOKENS = [
    "CMP_LT", "CMP_LE", "CMP_LTE", "CMP_EQ",
    "CMP_NE", "CMP_NEQ", "CMP_GE", "CMP_GTE",
    "CMP_GT", "CMP_CONTAINS", "CMP_IN", "CMP_NOT_IN",
]


# ═══════════════════════════════════════════════════════════════════
# 29. 特殊数值 / 占位符
# ═══════════════════════════════════════════════════════════════════

VAL_NUMERIC = "VAL_NUMERIC"    # 任意数值占位符（具体值放 values 数组）
VAL_STRING = "VAL_STRING"      # 动态字符串占位符（如精灵名/技能名）
VAL_FLOAT = "VAL_FLOAT"        # 浮点占位符
VAL_BOOL = "VAL_BOOL"          # 布尔占位符


# ═══════════════════════════════════════════════════════════════════
# 29. 元素过滤哨兵 (element: "each")
# ═══════════════════════════════════════════════════════════════════

EACH = "EACH"


# ═══════════════════════════════════════════════════════════════════
# 组装完整词表
# ═══════════════════════════════════════════════════════════════════

ALL_TOKENS: list[str] = (
    STRUCTURAL_TOKENS
    + KEY_TOKENS
    + OP_TOKENS
    + TARGET_TOKENS
    + CONDITION_TOKENS
    + STAT_TOKENS
    + ATTR_TOKENS
    + SCOPE_TOKENS
    + LISTEN_TOKENS
    + MODE_TOKENS
    + FLAG_TOKENS
    + WEATHER_TOKENS
    + ABNORMAL_TOKENS
    + MARK_TOKENS
    + ELEMENT_TOKENS
    + SKILL_TYPE_TOKENS
    + SKILL_FILTER_TOKENS
    + WHAT_TOKENS
    + OF_TOKENS
    + QUERY_Q_TOKENS
    + FROM_TOKENS
    + AT_TOKENS
    + ACTION_TOKENS
    + SOURCE_TOKENS
    + COUNTER_TOKENS
    + BLOODLINE_TOKENS
    + TAG_TOKENS
    + CMP_TOKENS
    + [EACH, VAL_NUMERIC, VAL_STRING, VAL_FLOAT, VAL_BOOL]
)


# 去重校验
assert len(ALL_TOKENS) == len(set(ALL_TOKENS)), "词表存在重复 token!"

VOCAB_SIZE = len(ALL_TOKENS)

# 双向映射
VOCAB_TO_ID: dict[str, int] = {tok: idx for idx, tok in enumerate(ALL_TOKENS)}
ID_TO_VOCAB: dict[int, str] = {idx: tok for idx, tok in enumerate(ALL_TOKENS)}


# ═══════════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════════

def get_token_id(token_str: str) -> int:
    """返回 token 对应的 ID，未知 token 返回 <UNK> 的 ID。"""
    return VOCAB_TO_ID.get(token_str, VOCAB_TO_ID["<UNK>"])


def get_token(id_: int) -> str:
    """返回 ID 对应的 token 字符串。"""
    return ID_TO_VOCAB.get(id_, "<UNK>")


def is_special(token_str: str) -> bool:
    """判断 token 是否为结构控制符。"""
    return token_str in STRUCTURAL_TOKENS


def is_opcode(token_str: str) -> bool:
    """判断 token 是否为操作码。"""
    return token_str in OP_TOKENS


def is_condition(token_str: str) -> bool:
    """判断 token 是否为条件类型。"""
    return token_str in CONDITION_TOKENS


# ═══════════════════════════════════════════════════════════════════
# 子集索引（供 tokenizer 快速分类查询）
# ═══════════════════════════════════════════════════════════════════

ALL_TOKEN_SETS = {
    "structural": set(STRUCTURAL_TOKENS),
    "key": set(KEY_TOKENS),
    "opcode": set(OP_TOKENS),
    "target": set(TARGET_TOKENS),
    "condition": set(CONDITION_TOKENS),
    "stat": set(STAT_TOKENS),
    "attr": set(ATTR_TOKENS),
    "scope": set(SCOPE_TOKENS),
    "listen": set(LISTEN_TOKENS),
    "mode": set(MODE_TOKENS),
    "flag": set(FLAG_TOKENS),
    "weather": set(WEATHER_TOKENS),
    "abnormal": set(ABNORMAL_TOKENS),
    "mark": set(MARK_TOKENS),
    "element": set(ELEMENT_TOKENS),
    "skill_type": set(SKILL_TYPE_TOKENS),
    "skill_filter": set(SKILL_FILTER_TOKENS),
    "what": set(WHAT_TOKENS),
    "of": set(OF_TOKENS),
    "query_q": set(QUERY_Q_TOKENS),
    "from": set(FROM_TOKENS),
    "at": set(AT_TOKENS),
    "action": set(ACTION_TOKENS),
    "source": set(SOURCE_TOKENS),
    "counter": set(COUNTER_TOKENS),
    "bloodline": set(BLOODLINE_TOKENS),
    "tag": set(TAG_TOKENS),
    "cmp": set(CMP_TOKENS),
}
