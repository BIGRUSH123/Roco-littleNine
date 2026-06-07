"""backend/engine/ai/core/encoder.py — Battle 状态 → 实体矩阵 + AST 序列，供神经网络输入。

架构: 实体化 (Entity-based) + 瓶颈编码 (Bottleneck)
  放弃扁平向量拼接，改为输出结构化实体矩阵，交由 PyTorch 模型做
  log1p 归一化 / nn.Embedding 离散映射 / 局域瓶颈压缩 / 延迟融合。

输出: dict with keys:
  - "sprite_stats":    (12, 7)  float32  原始六维面板 + 当前血
  - "sprite_elements": (12, 2) int32    双元素 ID (主/副, 0=PAD)
  - "sprite_states":   (12, 105) float32  能量/异常/buff(25) + 场下技能摘要(80)
  - "skill_stats":     (10, 2)  float32  原始威力 + 能耗
  - "skill_elements":  (10, 2) int32    双元素 ID (主/PAD, 与精灵对齐)
  - "skill_states":    (10, 9) float32  [sealed, cooldown, 类型OneHot(5), combo, transmission] raw
  - "global_stats":    (15,)    float32  回合/印记/魔力/道具
  - "global_elements": (1,)     int32    天气 ID
  - "ast_tokens":      (384,)   int32    token ID 序列 (PAD=0)
  - "ast_values":      (384,)   float32  对应值序列

约定:
  - 空槽位 / 已力竭 → 照常编码（让网络学习推断"场上已死必须换宠"）
  - 对方板凳不可见 → mask_opp_bench 闭锁对方 bench 实体为 0
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from backend.common.constants import STAT_KEYS
from backend.vm.effect import ObserverEffect, StateEffect

if TYPE_CHECKING:
    from backend.sim.battle import Battle
    from backend.sim.battleskill import BattleSkill
    from backend.sim.player import Player
    from backend.sim.sprite import Sprite

# ═══════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════

ELEMENT_ORDER: tuple[str, ...] = (
    '光', '冰', '地', '幻', '幽', '恶', '普通', '机械',
    '武', '毒', '水', '火', '电', '翼', '草', '萌', '虫', '龙',
)

ABNORMAL_ORDER: tuple[str, ...] = (
    '灼烧', '冻结', '中毒', '寄生', '萌化', '晕眩', '眩晕',
)

WEATHER_ORDER: tuple[str, ...] = ('none', 'rain', 'sand', 'snow')

MARKS_POS: tuple[str, ...] = (
    '攻击印记', '蓄电印记', '润泽印记', '湿润印记',
    '风起', '光合印记', '龙噬印记',
)
MARKS_NEG: tuple[str, ...] = (
    '减速', '迟缓', '棘刺', '降灵印记', '中毒印记', '星陨印记',
)

DEVOTION_ORDER: tuple[str, ...] = (
    '奉献1', '奉献2', '奉献3', '奉献4', '奉献5',
)

# BUFF_KEYS for sprite_states: stat buffs (atk/def/spa/spd/spe) + power/energy_cost
BUFF_KEYS: tuple[str, ...] = (
    'atk', 'def', 'sp_atk', 'sp_def', 'speed',
    'power', 'energy_cost',
)

# 系别克制表 (18 系) — 来源: resolver._TYPE_CHART
_TYPE_CHART: dict[str, dict[str, float]] = {
    '光': {'冰': 0.5, '幽': 2.0, '恶': 2.0, '翼': 0.5},
    '冰': {'冰': 0.5, '地': 2.0, '机械': 0.5, '火': 0.5, '翼': 2.0, '草': 2.0, '龙': 2.0},
    '地': {'冰': 2.0, '武': 0.5, '毒': 2.0, '火': 2.0, '电': 2.0, '草': 0.5},
    '幻': {'光': 0.5, '幻': 0.5, '机械': 0.5, '武': 2.0, '毒': 2.0},
    '幽': {'光': 2.0, '幻': 2.0, '幽': 2.0, '恶': 0.5, '普通': 0.5},
    '恶': {'光': 0.5, '幽': 2.0, '恶': 0.5, '武': 0.5, '毒': 2.0, '萌': 2.0},
    '普通': {'地': 0.5, '幽': 0.5, '机械': 0.5},
    '机械': {'冰': 2.0, '地': 2.0, '机械': 0.5, '水': 0.5, '火': 0.5, '电': 0.5, '萌': 2.0},
    '武': {'冰': 2.0, '地': 2.0, '幻': 0.5, '幽': 0.5, '恶': 2.0, '普通': 2.0, '机械': 2.0, '毒': 0.5, '翼': 0.5, '萌': 0.5, '虫': 0.5},
    '毒': {'地': 0.5, '幽': 0.5, '机械': 0.5, '毒': 0.5, '草': 2.0, '萌': 2.0},
    '水': {'冰': 0.5, '地': 2.0, '机械': 2.0, '火': 2.0, '草': 0.5, '龙': 0.5},
    '火': {'冰': 2.0, '地': 0.5, '机械': 2.0, '水': 0.5, '草': 2.0, '虫': 2.0, '龙': 0.5},
    '电': {'地': 0.5, '水': 2.0, '电': 0.5, '翼': 2.0, '草': 0.5, '龙': 0.5},
    '翼': {'地': 0.5, '机械': 0.5, '武': 2.0, '电': 0.5, '草': 2.0, '虫': 2.0, '龙': 0.5},
    '草': {'光': 2.0, '地': 2.0, '机械': 0.5, '毒': 0.5, '水': 2.0, '火': 0.5, '翼': 0.5, '萌': 0.5, '虫': 0.5, '龙': 0.5},
    '萌': {'恶': 2.0, '机械': 0.5, '武': 2.0, '毒': 0.5, '火': 0.5, '龙': 2.0},
    '虫': {'幻': 2.0, '幽': 0.5, '恶': 2.0, '机械': 0.5, '武': 0.5, '毒': 0.5, '火': 0.5, '翼': 0.5, '草': 2.0, '萌': 0.5},
    '龙': {'机械': 0.5, '龙': 2.0},
}

# sprite_states 维度常量
SPRITE_STATES_BASE = 25   # 基础状态：能量/异常/buff/标记
SKILL_SUMMARY_PER_SKILL = 8   # 每技能摘要：威力(1) + 能耗(1) + 类型OneHot(5) + 克制(1) = 8
SPRITE_STATES_DIM = SPRITE_STATES_BASE + 10 * SKILL_SUMMARY_PER_SKILL  # 25 + 80 = 105

# 技能类型 One-Hot 映射
_SKILL_TYPE_MAP: dict[str, int] = {'物攻': 0, '魔攻': 1, '动态攻击': 2, '防御': 3, '状态': 4}
_SKILL_TYPE_NUM = 5

# 类别 ID 映射
_ELEMENT_TO_ID: dict[str, int] = {el: i + 1 for i, el in enumerate(ELEMENT_ORDER)}
_WEATHER_TO_ID: dict[str, int] = {w: i for i, w in enumerate(WEATHER_ORDER)}


def _get_cat_id(value: str, order_tuple: tuple[str, ...]) -> int:
    """统一类别 ID 查询：在序列中找到索引 +1，未找到返回 0。

    ID=0 始终是 PAD/unknown 占位符，1..N 是有效类别。
    """
    if value in order_tuple:
        return order_tuple.index(value) + 1
    return 0

# 异常最大层数（归一化除数）
_ABNORMAL_MAX: dict[str, int] = {
    '灼烧': 50, '冻结': 20, '中毒': 10, '寄生': 10,
    '萌化': 3, '晕眩': 1, '眩晕': 1,
}

# 技能效果惰性缓存
_skills_dir: Path | None = None
_skill_effects_cache: dict[str, list] = {}
_skill_flat_effects_cache: dict[str, list] = {}

# AST 序列截断上限
MAX_SEQ_LEN = 384


# ═══════════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════════

def encode_battle_state(
    battle: Battle,
    *,
    mask_opp_bench: bool = False,
    perspective: str = "A",
) -> dict[str, np.ndarray]:
    """将 Battle 状态编码为实体矩阵字典。

    Returns:
        dict with keys:
          - "sprite_stats":    (12, 7)  float32  [hp, max_hp, atk, def, spa, spd, spe]
          - "sprite_elements": (12, 2) int32    双元素 ID (主/副, 0=PAD)
          - "sprite_states":   (12, 105) float32  能量/异常/buff/标记(25) + 场下技能摘要(80)
          - "skill_stats":     (10, 2)  float32  [power, energy_cost]
          - "skill_elements":  (10, 2) int32    双元素 ID (主/PAD)
          - "skill_states":    (10, 9) float32  [sealed, cooldown, 类型OneHot(5), combo, transmission] raw
          - "global_stats":    (15,)    float32  回合/印记/魔力/道具
          - "global_elements": (1,)     int32    天气 ID
          - "ast_tokens":      (384,)   int32    token ID (PAD=0)
          - "ast_values":      (384,)   float32  对应值
    """
    own: Player = battle.player_a if perspective == "A" else battle.player_b
    opp: Player = battle.player_b if perspective == "A" else battle.player_a
    own_team = "A" if perspective == "A" else "B"

    own_active: Sprite | None = _active_at_index(own)
    opp_active: Sprite | None = _active_at_index(opp)

    # ====== 1. 精灵实体 (12 只) ======
    # 己方占前 6 槽 (0-5)、对方占后 6 槽 (6-11)，与 model.py 的前/后半切片对齐。
    sprite_stats = np.zeros((12, 7), dtype=np.float32)
    sprite_elements = np.zeros((12, 2), dtype=np.int32)
    sprite_states = np.zeros((12, SPRITE_STATES_DIM), dtype=np.float32)

    _fill_sprite_entity(battle, 0, own_active, sprite_stats, sprite_elements, sprite_states)
    _fill_bench_entities(battle, 1, own, sprite_stats, sprite_elements, sprite_states, mask_unknown=False, opp_active=opp_active)
    _fill_sprite_entity(battle, 6, opp_active, sprite_stats, sprite_elements, sprite_states)
    _fill_bench_entities(battle, 7, opp, sprite_stats, sprite_elements, sprite_states, mask_unknown=mask_opp_bench, opp_active=own_active)

    # ====== 2. 技能实体 (10 个己方技能槽) ======
    skill_stats = np.zeros((10, 2), dtype=np.float32)
    skill_elements = np.zeros((10, 2), dtype=np.int32)
    skill_states = np.zeros((10, 9), dtype=np.float32)

    if own_active is not None:
        _fill_skill_entities(own_active.skills or [], skill_stats, skill_elements, skill_states)

    # ====== 3. 全局实体 ======
    global_stats = np.zeros(15, dtype=np.float32)
    global_elements = np.zeros(1, dtype=np.int32)
    _fill_global_entity(battle, own_team, own, opp, global_stats, global_elements)

    # ====== 4. AST 序列 ======
    all_tokens: list[str] = []
    all_values: list[float] = []
    _collect_ast_tokens(own, opp, all_tokens, all_values, mask_opp_bench=mask_opp_bench)
    # 安全截断已在 _collect_ast_tokens 内部按实体边界完成
    # 这里做硬截断兜底：极端嵌套技能（多层 when-then-else）单次可能
    # 产生 >SAFE_MARGIN 个 token，导致 i >= MAX_SEQ_LEN → IndexError

    ast_tokens = np.zeros(MAX_SEQ_LEN, dtype=np.int32)
    ast_values = np.zeros(MAX_SEQ_LEN, dtype=np.float32)
    for i, (tok_str, val) in enumerate(zip(all_tokens, all_values)):
        if i >= MAX_SEQ_LEN:
            break
        ast_tokens[i] = VOCAB_TO_ID.get(tok_str, VOCAB_TO_ID["<UNK>"])
        ast_values[i] = val

    return {
        "sprite_stats": sprite_stats,
        "sprite_elements": sprite_elements,
        "sprite_states": sprite_states,
        "skill_stats": skill_stats,
        "skill_elements": skill_elements,
        "skill_states": skill_states,
        "global_stats": global_stats,
        "global_elements": global_elements,
        "ast_tokens": ast_tokens,
        "ast_values": ast_values,
    }


# ═══════════════════════════════════════════════════════════════════
# 实体填充辅助函数
# ═══════════════════════════════════════════════════════════════════

def _fill_sprite_entity(
    battle: Battle,
    idx: int,
    sprite: Sprite | None,
    stats: np.ndarray,
    elements: np.ndarray,
    states: np.ndarray,
) -> None:
    """填充单个精灵实体。sprite=None 时保持全零。"""
    if sprite is None:
        return

    # ── stats (7): 绝对数值 — 交由模型做 log1p 归一化 ──
    stats[idx, 0] = float(sprite.current_hp)
    stats[idx, 1] = float(max(sprite.max_hp, 1))
    stats[idx, 2] = float(sprite.effective_stat('atk'))
    stats[idx, 3] = float(sprite.effective_stat('def'))
    stats[idx, 4] = float(sprite.effective_stat('sp_atk'))
    stats[idx, 5] = float(sprite.effective_stat('sp_def'))
    stats[idx, 6] = float(sprite.effective_stat('speed'))

    # ── element: 双属性 ID (主/副, 0=PAD) ──
    species_elements = getattr(sprite.species, 'elements', [])
    elements[idx, 0] = _get_cat_id(species_elements[0], ELEMENT_ORDER) if species_elements else _get_cat_id('普通', ELEMENT_ORDER)
    elements[idx, 1] = _get_cat_id(species_elements[1], ELEMENT_ORDER) if len(species_elements) > 1 else 0

    # ── states (前 25 维) ──
    s = states[idx]

    # 1. energy / max_energy
    s[0] = float(sprite.energy) / max(float(sprite.max_energy), 1.0)
    # 2. is_fainted
    s[1] = 1.0 if sprite.is_fainted else 0.0
    # 3. hp_ratio
    s[2] = float(sprite.current_hp) / max(float(sprite.max_hp), 1.0)
    # 4. locked_turns / 3
    s[3] = min(float(getattr(sprite, 'locked_turns', 0)) / 3.0, 1.0)
    # 5. charging
    s[4] = 1.0 if _find_effect_of_type(sprite, 'charging') else 0.0
    # 6. pending_return
    s[5] = 1.0 if getattr(sprite, 'pending_return', False) else 0.0
    # 7. first_action
    s[6] = 1.0 if getattr(sprite, 'first_action', False) else 0.0
    # 8. extra_skill_use
    s[7] = 1.0 if getattr(sprite, 'extra_skill_use', False) else 0.0
    # 9. interrupted
    s[8] = 1.0 if getattr(sprite, 'interrupted', False) else 0.0
    # 10. entry_age / 20
    entry_turn = getattr(sprite, 'entry_turn', 0)
    s[9] = min(float(battle.turn - entry_turn) / 20.0, 1.0)
    # 11. trait_suppressed
    s[10] = 1.0 if getattr(sprite, '_trait_suppressed', False) else 0.0

    # 12-18. abnormal stacks (7)
    for i, an in enumerate(ABNORMAL_ORDER):
        stacks = sprite.get_stacks(an)
        max_s = _ABNORMAL_MAX.get(an, 1)
        s[11 + i] = min(float(stacks) / float(max_s), 1.0)

    # 19-25. buff steps (7): atk/def/spa/spd/spe/power/energy_cost
    for i, bk in enumerate(BUFF_KEYS):
        steps = _sprite_buff_steps(sprite, bk)
        s[18 + i] = max(-1.0, min(1.0, float(steps) / 10.0))


def _sprite_buff_steps(sprite: Sprite, key: str) -> int:
    """返回精灵某项修正的步数（正=强化，负=削弱）。"""
    if key in STAT_KEYS:
        base = sprite.initial_stats.get(key, 0)
        eff = sprite.effective_stat(key)
        if base > 0:
            if key == 'speed':
                return (eff - base) // 10
            else:
                return round((eff / base - 1.0) * 10)
        return 0
    # power / energy_cost
    if key == 'power':
        return int(sprite._modifiers.get('power', 0))
    if key == 'energy_cost':
        return int(sprite._modifiers.get('energy_cost', 0))
    return sprite._sum_steps(key)


def _fill_bench_entities(
    battle: Battle,
    start_idx: int,
    player: Player,
    stats: np.ndarray,
    elements: np.ndarray,
    states: np.ndarray,
    *,
    mask_unknown: bool = False,
    opp_active: Sprite | None = None,
) -> None:
    """填充 5 个板凳槽位（固定索引，不跳力竭）。

    mask_unknown=True → 对方板凳不可见，保持全零。
    额外填入场下精灵技能摘要（states 后 40 维）。
    """
    team: list = player.team or []
    active_idx: int = getattr(player, 'active_index', 0)
    bench = [s for i, s in enumerate(team) if i != active_idx]

    for slot in range(5):
        if mask_unknown or slot >= len(bench):
            continue  # 保持全零
        sprite = bench[slot]
        _fill_sprite_entity(battle, start_idx + slot, sprite, stats, elements, states)
        _fill_bench_skill_summary(sprite, opp_active, states[start_idx + slot])


def _type_advantage(atk_element: str, def_element: str) -> float:
    """返回主动方元素对防守方元素系的克制倍率 (0.5 ~ 2.0)。"""
    chart = _TYPE_CHART.get(atk_element, {})
    return chart.get(def_element, 1.0)


def _fill_bench_skill_summary(
    sprite: Sprite,
    opp_active: Sprite | None,
    state_row: np.ndarray,
) -> None:
    """填充场下精灵的技能摘要到 state_row 的后 80 维 (25-104)。

    每槽 8 维: [power/300, energy_cost/15, 类型OneHot(5), type_adv_norm]。
    场上精灵不填此区域（保持全零），空槽/封印也全零。
    """
    skills = sprite.skills or []
    def_element = _sprite_primary_element(opp_active) if opp_active else ''

    for i in range(10):
        offset = SPRITE_STATES_BASE + i * SKILL_SUMMARY_PER_SKILL
        if i >= len(skills) or skills[i] is None:
            continue  # 空槽位全零
        sk = skills[i]
        if sk.sealed:
            continue  # 封印的技能全零

        # 1. 威力 / 300
        state_row[offset] = float(sk.power) / 300.0
        # 2. 能耗 / 15
        state_row[offset + 1] = float(sk.energy_cost) / 15.0
        # 3-7. 技能类型 One-Hot (5)
        type_idx = _SKILL_TYPE_MAP.get(sk.skill_type, 0)
        for t_i in range(_SKILL_TYPE_NUM):
            state_row[offset + 2 + t_i] = 1.0 if t_i == type_idx else 0.0
        # 8. 技能元素 vs 对手场上元素的克制关系 → 归一化到 [0, 1]
        if def_element and sk.element:
            adv = _type_advantage(sk.element, def_element)
            state_row[offset + 7] = (adv - 0.5) / 1.5  # 0.5→0, 1.0→0.333, 2.0→1.0
        else:
            state_row[offset + 7] = 0.0


def _fill_skill_entities(
    skills: list,
    stats: np.ndarray,
    elements: np.ndarray,
    states: np.ndarray,
) -> None:
    """填充 10 个技能槽位实体 (raw 值, 由模型 Log1pNorm 归一化)。

    elements: (10, 2) 双ID, 技能仅填主属性, 副属性恒为 PAD(0)。
    states: (10, 9) [sealed, cooldown, 类型OneHot(5), combo, transmission]。
    """
    for i in range(10):
        if i >= len(skills) or skills[i] is None:
            continue  # 空槽位保持全零

        sk = skills[i]
        # ── stats (2): 绝对威力与能耗 ──
        stats[i, 0] = float(sk.power)
        stats[i, 1] = float(sk.energy_cost)
        # ── element: (10, 2) 双ID, 副属性恒为 PAD ──
        elements[i, 0] = _get_cat_id(sk.element, ELEMENT_ORDER)
        elements[i, 1] = 0
        # ── states (9): raw 值 ──
        s = states[i]
        s[0] = 1.0 if sk.sealed else 0.0
        s[1] = float(sk.cooldown)
        # 2-6. 技能类型 One-Hot (5)
        type_idx = _SKILL_TYPE_MAP.get(sk.skill_type, 0)
        for t_i in range(_SKILL_TYPE_NUM):
            s[2 + t_i] = 1.0 if t_i == type_idx else 0.0
        s[7] = float(getattr(sk, 'combo', 1))
        s[8] = float(getattr(sk, '_transmission', 0))


def _fill_global_entity(
    battle: Battle,
    own_team: str,
    own: Player,
    opp: Player,
    stats: np.ndarray,
    weather_id: np.ndarray,
) -> None:
    """填充全局实体 (15 stats + 1 element)。"""
    g = battle.globals
    opp_team = "B" if own_team == "A" else "A"

    stats[0] = float(battle.turn) / 150.0                              # 1. turn
    stats[1] = min(float(g.weather_turns) / 8.0, 1.0)                  # 2. weather_turns
    stats[2] = float(own.lives) / 6.0                                   # 3. own lives
    stats[3] = float(opp.lives) / 6.0                                   # 4. opp lives

    own_pos, own_neg = _classify_marks_global(g, own_team)
    opp_pos, opp_neg = _classify_marks_global(g, opp_team)
    stats[4] = _sum_mark_stacks(own_pos) / 50.0                         # 5.
    stats[5] = _sum_mark_stacks(own_neg) / 50.0                         # 6.
    stats[6] = _sum_mark_stacks(opp_pos) / 50.0                         # 7.
    stats[7] = _sum_mark_stacks(opp_neg) / 50.0                         # 8.

    item_own = own.item
    stats[8] = 1.0 if (item_own is not None) else 0.0                  # 9.
    stats[9] = float(item_own.uses) / max(float(item_own.max_uses), 1.0) if item_own else 0.0  # 10.
    stats[10] = 1.0 if (item_own and item_own.is_exhausted) else 0.0   # 11.

    item_opp = opp.item
    stats[11] = 1.0 if (item_opp is not None) else 0.0                 # 12.
    stats[12] = float(item_opp.uses) / max(float(item_opp.max_uses), 1.0) if item_opp else 0.0  # 13.
    stats[13] = 1.0 if (item_opp and item_opp.is_exhausted) else 0.0   # 14.

    dev_own = getattr(own, 'devotion', {}) or {}
    max_dev = max((dev_own.get(dn, 0) for dn in DEVOTION_ORDER), default=0)
    stats[14] = float(max_dev) / 5.0                                    # 15.

    w = g.weather or 'none'
    weather_id[0] = _WEATHER_TO_ID.get(w, 0)


# ═══════════════════════════════════════════════════════════════════
# 共享辅助函数
# ═══════════════════════════════════════════════════════════════════

def _active_at_index(player: Player) -> Sprite | None:
    """返回 active_index 对应的精灵，无论是否力竭。"""
    idx = getattr(player, 'active_index', 0)
    team = player.team or []
    if idx < len(team):
        return team[idx]
    return None


def _sprite_primary_element(sprite: Sprite) -> str:
    elements = getattr(sprite.species, 'elements', [])
    return elements[0] if elements else '普通'


def _find_effect_of_type(sprite: Sprite, state_type: str) -> bool:
    """查找 sprite.active_effects 中是否存在指定 state_type 的 StateEffect。"""
    for e in (getattr(sprite, 'active_effects', []) or []):
        if isinstance(e, StateEffect):
            if getattr(e, 'state_type', '') == state_type or getattr(e, 'name', '') == state_type:
                return True
    return False


def _classify_marks_global(g, team: str) -> tuple[list, list]:
    marks = getattr(g, 'marks', {}) or {}
    pos = marks.get(team, {}).get('positive', [])
    neg = marks.get(team, {}).get('negative', [])
    return pos, neg


def _sum_mark_stacks(marks: list) -> float:
    return float(sum(getattr(m, 'stacks', 0) for m in marks))


# ═══════════════════════════════════════════════════════════════════
# 技能效果 JSON 缓存（AST 收集用）
# ═══════════════════════════════════════════════════════════════════

def _get_skills_dir() -> Path:
    global _skills_dir
    if _skills_dir is None:
        _skills_dir = Path(__file__).parent.parent.parent.parent.parent / "data" / "skills"
    return _skills_dir


def _get_skill_effects(name: str) -> list[dict]:
    """加载技能 effect 原始 dict 列表（惰性缓存）。保留完整 when-then-else 树。"""
    cached = _skill_effects_cache.get(name)
    if cached is not None:
        return cached
    path = _get_skills_dir() / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        effects = data.get('effects', [])
        _skill_effects_cache[name] = effects
        return effects
    except (FileNotFoundError, json.JSONDecodeError):
        _skill_effects_cache[name] = []
        return []


# ═══════════════════════════════════════════════════════════════════
# AST 序列收集
# ═══════════════════════════════════════════════════════════════════

def _collect_ast_tokens(
    own: Player, opp: Player,
    all_tokens: list[str], all_values: list[float],
    *,
    mask_opp_bench: bool = False,
) -> None:
    """收集己方场上精灵的技能效果和特性 observer 为 AST token 序列。

    严格按 10 个技能槽位对齐动作空间 0-9。
    使用 SAFE_MARGIN 确保不会在技能/特性中间截断 AST 树。
    """
    # 单个中等技能约需 30 token，留 50 安全余量
    SAFE_MARGIN = 50

    active: Sprite | None = _active_at_index(own)
    if active is None:
        return

    skills: list = active.skills or []

    for i in range(10):
        # 实体级安全截断：剩余空间不够，直接停止后续所有 AST 展平
        if len(all_tokens) > MAX_SEQ_LEN - SAFE_MARGIN:
            break

        all_tokens.append("<SEP>")
        all_values.append(0.0)

        if i >= len(skills) or skills[i] is None:
            all_tokens.append("<EMPTY_SKILL>")
            all_values.append(0.0)
            continue

        sk = skills[i]
        effects = _get_skill_effects(sk.name)

        if sk.sealed or sk.cooldown > 0:
            all_tokens.append("<SEALED_SKILL>")
            all_values.append(1.0 if sk.sealed else 0.5)
        else:
            all_tokens.append("<ACTIVE_SKILL>")
            all_values.append(1.0)

        for eff in effects:
            t, v = tokenize_effect_dfs(eff)
            all_tokens.extend(t)
            all_values.extend(v)

    for eff in getattr(active, 'active_effects', []) or []:
        # 实体级安全截断
        if len(all_tokens) > MAX_SEQ_LEN - SAFE_MARGIN:
            break

        if not isinstance(eff, ObserverEffect):
            continue
        observer_dict: dict = {"op": "observer"}
        if eff.cond:
            observer_dict["cond"] = eff.cond
        if eff.then:
            observer_dict["then"] = list(eff.then)
        if eff.listen:
            observer_dict["listen"] = list(eff.listen)
        if eff.scope:
            observer_dict["scope"] = eff.scope
        all_tokens.append("<SEP>")
        all_values.append(0.0)
        t, v = tokenize_effect_dfs(observer_dict)
        all_tokens.extend(t)
        all_values.extend(v)


# ═══════════════════════════════════════════════════════════════════
# IR 展平解析器 — 将 effect dict 树序列化为 token + value 双数组
# ═══════════════════════════════════════════════════════════════════

from backend.engine.ai.core.vocab import VOCAB_TO_ID, VAL_NUMERIC, VAL_STRING


def _add_tok(tokens: list[str], values: list[float], tok_str: str, val: float = 0.0) -> None:
    """同步增长 tokens / values 两个数组，保证长度始终一致。"""
    tokens.append(tok_str)
    values.append(float(val))


def _try_enum_token(v: str, prefixes: tuple[str, ...]) -> str | None:
    """尝试将字符串值匹配到枚举 token（如 TGT_SPRITE_SELF, ATTR_POWER）。
    
    会先检查 _ALIAS_MAP 中的别名映射，再尝试直接大写拼接。
    """
    # 1. 精确别名
    alias = _ALIAS_MAP.get(v)
    if alias is not None:
        return alias
    # 2. 大小写不敏感别名
    alias_upper = _ALIAS_UPPER.get(v.upper())
    if alias_upper is not None:
        return alias_upper
    # 3. 前缀 + 大写值
    v_upper = v.upper().replace(' ', '_').replace('-', '_')
    for prefix in prefixes:
        candidate = f"{prefix}{v_upper}"
        if candidate in VOCAB_TO_ID:
            return candidate
    return None


# ── 别名映射：JSON 数据结构到 vocab 枚举 token ──
_ALIAS_MAP: dict[str, str] = {
    # scope / target
    "self": "TGT_SPRITE_SELF",
    "sprite_self": "TGT_SPRITE_SELF",
    "opp": "TGT_SPRITE_OPP",
    "sprite_opp": "TGT_SPRITE_OPP",
    "own_team": "TGT_TEAM_OWN",
    "opp_team": "TGT_TEAM_OPP",
    "owner": "TGT_SPRITE_OWNER",
    "battlefield": "SCOPE_BATTLEFIELD",
    "turn": "SCOPE_TURN",
    "persistent": "SCOPE_PERSISTENT",
    "permanent": "SCOPE_PERMANENT",
    # skill types
    "物攻": "TYPE_PHYSICAL",
    "魔攻": "TYPE_SPECIAL",
    "动态攻击": "TYPE_DYNAMIC",
    "防御": "TYPE_DEFENSE",
    "状态": "TYPE_STATUS",
    # counter
    "无": "CTR_NONE",
    "攻击": "CTR_ATTACK",
    # attr
    "atk": "ATTR_ATK",
    "def": "ATTR_DEF",
    "sp_atk": "ATTR_SP_ATK",
    "sp_def": "ATTR_SP_DEF",
    "speed": "ATTR_SPEED",
    "hp": "ATTR_HP",
    "energy": "ATTR_ENERGY",
    "power": "ATTR_POWER",
    "energy_cost": "ATTR_ENERGY_COST",
    "priority": "ATTR_PRIORITY",
    "combo": "ATTR_COMBO",
    "stacks": "ATTR_STACKS",
    "ratio": "ATTR_RATIO",
    "cooldown": "ATTR_COOLDOWN",
    "value": "ATTR_VALUE",
    "accuracy": "ATTR_ACCURACY",
    # weather
    "rain": "WTH_RAIN",
    "sand": "WTH_SAND",
    "snow": "WTH_SNOW",
    # abnormal
    "灼烧": "ABN_BURN",
    "冻结": "ABN_FREEZE",
    "中毒": "ABN_POISON",
    "寄生": "ABN_PARASITE",
    "萌化": "ABN_MOE",
    "晕眩": "ABN_DIZZY",
    "眩晕": "ABN_STUN",
    # element
    "光": "ELEM_LIGHT",
    "冰": "ELEM_ICE",
    "地": "ELEM_EARTH",
    "幻": "ELEM_ILLUSION",
    "幽": "ELEM_GHOST",
    "恶": "ELEM_DARK",
    "普通": "ELEM_NORMAL",
    "机械": "ELEM_MACHINE",
    "武": "ELEM_FIGHT",
    "毒": "ELEM_POISON",
    "水": "ELEM_WATER",
    "火": "ELEM_FIRE",
    "电": "ELEM_ELECTRIC",
    "翼": "ELEM_WING",
    "草": "ELEM_GRASS",
    "萌": "ELEM_CUTE",
    "虫": "ELEM_BUG",
    "龙": "ELEM_DRAGON",
}

_ALIAS_UPPER: dict[str, str] = {k.upper(): v for k, v in _ALIAS_MAP.items()}


def _encode_value(v, add_tok) -> None:
    """递归编码任意 IR 值节点。"""
    if v is None:
        add_tok("<PAD>")
    elif isinstance(v, bool):
        add_tok("VAL_BOOL", 1.0 if v else 0.0)
    elif isinstance(v, (int, float)):
        add_tok("VAL_NUMERIC", float(v))
    elif isinstance(v, str):
        # 1. 别名
        alias = _ALIAS_MAP.get(v)
        if alias:
            add_tok(alias)
            return
        alias_upper = _ALIAS_UPPER.get(v.upper())
        if alias_upper:
            add_tok(alias_upper)
            return
        # 2. 枚举前缀匹配（所有 vocab 中的领域前缀，顺序大致按使用频率）
        for prefix_tokens in (
            ("TGT_",), ("ATTR_",), ("ELEM_",), ("WTH_",), ("ABN_",),
            ("MARK_",), ("SCOPE_",), ("SKTYPE_", "TYPE_"), ("CTR_",),
            ("COND_",), ("CMP_",), ("WHAT_",), ("OF_",), ("Q_",),
            ("FROM_",), ("AT_",), ("ACT_",), ("SRC_",), ("BLD_",), ("TAG_",),
        ):
            tok = _try_enum_token(v, prefix_tokens)
            if tok:
                add_tok(tok)
                return
        # 3. fallback
        add_tok("VAL_STRING")
    elif isinstance(v, dict):
        if "q" in v:
            _parse_query(v, add_tok)
        elif "cond" in v:
            if v.get("cond") in ("and", "or", "not"):
                _parse_cond(v, add_tok)
            else:
                # 简单内联条件（如 {"cond": "weather_is", "weather": "rain"}）
                sub_effect: dict = {}
                if "cond" in v:
                    sub_effect["cond"] = v["cond"]
                if "op" in v:
                    sub_effect["op"] = v["op"]
                if "then" in v:
                    sub_effect["then"] = v["then"]
                if "else" in v:
                    sub_effect["else"] = v["else"]
                t, vals = tokenize_effect_dfs(sub_effect)
                for i, tok in enumerate(t):
                    add_tok(tok, vals[i])
        else:
            for k, sv in v.items():
                key_str = f"KEY_{k.upper()}"
                add_tok(key_str if key_str in VOCAB_TO_ID else "<UNK>")
                _encode_value(sv, add_tok)
    elif isinstance(v, (list, tuple)):
        pass


def _parse_query(query: dict, add_tok) -> None:
    """解析 Query 寄存器查询 (IR_GUIDE 第二节值表达式)。"""
    add_tok("<B_QUERY>")
    for k, v in query.items():
        key_str = f"KEY_{k.upper()}"
        add_tok(key_str if key_str in VOCAB_TO_ID else "<UNK>")
        _encode_value(v, add_tok)
    add_tok("<E_QUERY>")


def _parse_cond(cond, add_tok) -> None:
    """递归解析条件表达式 (IR_GUIDE 第五节条件系统)。

    支持两种形式：
      - str:  直接条件名，如 "before_damage_calc"
      - dict: 嵌套条件，如 {"cond": "and", "conditions": [...]}
    """
    if isinstance(cond, str):
        cond_token = f"COND_{cond.upper()}"
        add_tok(cond_token if cond_token in VOCAB_TO_ID else "<UNK>")
        return

    add_tok("<B_COND>")

    cond_type = cond.get("cond", "")
    cond_token = f"COND_{cond_type.upper()}"
    add_tok("KEY_COND")
    add_tok(cond_token if cond_token in VOCAB_TO_ID else "<UNK>")

    if cond_type in ("and", "or"):
        for sub_cond in cond.get("conditions", []):
            _parse_cond(sub_cond, add_tok)
    elif cond_type == "not":
        sub = cond.get("condition")
        if isinstance(sub, dict):
            _parse_cond(sub, add_tok)
    else:
        for k, v in cond.items():
            if k == "cond":
                continue
            key_str = f"KEY_{k.upper()}"
            add_tok(key_str if key_str in VOCAB_TO_ID else "<UNK>")
            _encode_value(v, add_tok)

    add_tok("<E_COND>")


def tokenize_effect_dfs(effect: dict) -> tuple[list[str], list[float]]:
    """深度优先遍历单个 effect 字典，展平为 Token/Value 双序列。

    返回: (token_str_list, value_float_list) — 两数组长度严格一致。
    """
    tokens: list[str] = []
    values: list[float] = []

    def add_tok(tok_str: str, val: float = 0.0):
        _add_tok(tokens, values, tok_str, val)

    add_tok("<B_EFFECT>")

    # ── 1. 控制流 when-then-else ──
    if "when" in effect:
        when_block = effect["when"]
        add_tok("<B_WHEN>")
        _parse_cond(when_block, add_tok)
        add_tok("<E_WHEN>")

        if "then" in effect:
            add_tok("<B_THEN>")
            for sub_eff in effect["then"]:
                if isinstance(sub_eff, dict):
                    t, v = tokenize_effect_dfs(sub_eff)
                    tokens.extend(t)
                    values.extend(v)
            add_tok("<E_THEN>")

        if "else_if" in effect:
            add_tok("<B_ELSE>")
            for sub_eff in effect["else_if"]:
                if isinstance(sub_eff, dict):
                    t, v = tokenize_effect_dfs(sub_eff)
                    tokens.extend(t)
                    values.extend(v)
            add_tok("<E_ELSE>")

        if "else" in effect:
            add_tok("<B_ELSE>")
            for sub_eff in effect["else"]:
                if isinstance(sub_eff, dict):
                    t, v = tokenize_effect_dfs(sub_eff)
                    tokens.extend(t)
                    values.extend(v)
            add_tok("<E_ELSE>")

        add_tok("<E_EFFECT>")
        return tokens, values

    # ── 2. 标准操作 op ──
    if "op" in effect:
        op_str = f"OP_{effect['op'].upper()}"
        add_tok("KEY_OP")
        add_tok(op_str if op_str in VOCAB_TO_ID else "<UNK>")

        is_observer = (effect["op"] == "observer")

        for k, v in effect.items():
            if k == "op":
                continue
            if is_observer and k in ("cond", "then"):
                continue

            key_str = f"KEY_{k.upper()}"
            add_tok(key_str if key_str in VOCAB_TO_ID else "<UNK>")

            if isinstance(v, list):
                for item in v:
                    _encode_value(item, add_tok)
            else:
                _encode_value(v, add_tok)

        if is_observer:
            if "cond" in effect:
                _parse_cond(effect["cond"], add_tok)
            if "then" in effect:
                add_tok("<B_THEN>")
                for sub_eff in effect["then"]:
                    if isinstance(sub_eff, dict):
                        t, v = tokenize_effect_dfs(sub_eff)
                        tokens.extend(t)
                        values.extend(v)
                add_tok("<E_THEN>")

    add_tok("<E_EFFECT>")
    return tokens, values


def tokenize_effects(effects: list[dict]) -> tuple[list[str], list[float]]:
    """将技能 effects[] 列表展平为 token/值双数组。效果间用 <SEP> 分隔。"""
    all_tokens: list[str] = []
    all_values: list[float] = []

    for i, eff in enumerate(effects):
        if i > 0:
            all_tokens.append("<SEP>")
            all_values.append(0.0)
        if isinstance(eff, dict):
            t, v = tokenize_effect_dfs(eff)
            all_tokens.extend(t)
            all_values.extend(v)

    return all_tokens, all_values


def tokenize_effects_to_ids(effects: list[dict]) -> tuple[list[int], list[float]]:
    """同 tokenize_effects，但 tokens 映射为 int ID。"""
    tokens_str, values = tokenize_effects(effects)
    ids = [VOCAB_TO_ID.get(t, VOCAB_TO_ID["<UNK>"]) for t in tokens_str]
    return ids, values
