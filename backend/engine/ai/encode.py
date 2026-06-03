"""backend/engine/ai/encode.py — Battle 状态 → 固定长度向量，供神经网络输入。

总维度: 446

约定:
- 空槽位（阵亡 / 不存在）→ 全 0
- 不完全信息（对方板凳特征不可见）→ -1
- 特性不直接编码——其效果已反映在 buff/debuff/异常/印记等状态中
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

WEATHER_ORDER: tuple[str, ...] = ('rain', 'sand', 'snow', '暴风雪')

MARKS_POS: tuple[str, ...] = (
    '攻击印记', '蓄电印记', '润泽印记', '湿润印记',
    '风起', '光合印记', '龙噬印记',
)
MARKS_NEG: tuple[str, ...] = (
    '减速', '迟缓', '棘刺', '降临印记', '中毒印记', '星陨印记',
)

DEVOTION_ORDER: tuple[str, ...] = (
    '奉献1', '奉献2', '奉献3', '奉献4', '奉献5',
)

ITEM_TYPES: tuple[str, ...] = ('进化之力', '愿力')

BUFF_KEYS: tuple[str, ...] = (
    'atk', 'def', 'sp_atk', 'sp_def', 'speed',
    'power_mult', 'damage_mult', 'damage_reduction', 'life_drain', 'combo',
)

COUNTER_ORDER: tuple[str, ...] = ('无', '攻击', '防御', '状态')

# 技能效果惰性缓存（工厂创建技能时 effects=[]，需从 JSON 加载）
_skills_dir: Path | None = None
_skill_effects_cache: dict[str, list] = {}
# 缓存展平后的效果列表，避免每步 MCTS 编码都递归展平同一技能的效果树
_skill_flat_effects_cache: dict[str, list] = {}

STAT_MAP: dict[str, int] = {k: i for i, k in enumerate(STAT_KEYS)}


# ═══════════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════════

def encode_battle_state(
    battle: Battle,
    *,
    mask_opp_bench: bool = False,
    perspective: str = "A",
    global_cache: dict[str, np.ndarray] | None = None,
) -> np.ndarray:
    """将 Battle 状态编码为 (446,) float32 向量。

    Args:
        battle: 对局对象。
        mask_opp_bench: 若 True，对方板凳特征以 -1 填充（模拟不完全信息）。
        perspective: "A"（默认）从 player_a 视角编码，"B" 从 player_b 视角编码。
        global_cache: 可选 {"A": (56,), "B": (56,)} 预编码全局状态，跳过 _encode_global。
    """
    pieces: list[np.ndarray] = []

    # 解析视角：己方/对方
    own: Player = battle.player_a if perspective == "A" else battle.player_b
    opp: Player = battle.player_b if perspective == "A" else battle.player_a

    # A: 全局状态 (56)
    if global_cache and perspective in global_cache:
        pieces.append(global_cache[perspective])
    else:
        own_team = "A" if perspective == "A" else "B"
        pieces.append(_encode_global(battle, own_team=own_team))

    # B: 己方场上精灵 (115)
    own_active: Sprite | None = _active_or_none(own)
    opp_active: Sprite | None = _active_or_none(opp)
    pieces.append(_encode_active_sprite(own_active, own, battle, opp_active))

    # C: 己方板凳 ×5 (80)
    pieces.append(_encode_bench_all(own, opp_active, mask_unknown=False))

    # D: 对方场上精灵 (115)
    pieces.append(_encode_active_sprite(opp_active, opp, battle, own_active))

    # E: 对方板凳 ×5 (80)
    pieces.append(_encode_bench_all(opp, own_active, mask_unknown=mask_opp_bench))

    result = np.concatenate(pieces)
    assert result.shape == (446,), f"维度错误: {result.shape}"
    return result.astype(np.float32)


# ═══════════════════════════════════════════════════════════════════
# 模块 A: 全局状态 (56)
# ═══════════════════════════════════════════════════════════════════

def _encode_global(battle: Battle, *, own_team: str = "A") -> np.ndarray:
    g = battle.globals
    own: Player = battle.player_a if own_team == "A" else battle.player_b
    opp: Player = battle.player_b if own_team == "A" else battle.player_a
    opp_team = "B" if own_team == "A" else "A"
    out: list[float] = []

    # 回合 (2)
    t = battle.turn
    out.append(t / 150.0)
    out.append(1.0 - t / 150.0)

    # 天气 (3)
    w = g.weather or ""
    for wt in WEATHER_ORDER[:3]:
        out.append(1.0 if w == wt else 0.0)

    # 天气剩余 (1)
    out.append(g.weather_turns / 8.0)

    # 己方正印记 (7)
    own_pos, own_neg = _classify_marks(g, own_team)
    for mn in MARKS_POS:
        m = _find_mark(own_pos, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 己方负印记 (6)
    for mn in MARKS_NEG:
        m = _find_mark(own_neg, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 对方正印记 (7)
    opp_pos, opp_neg = _classify_marks(g, opp_team)
    for mn in MARKS_POS:
        m = _find_mark(opp_pos, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 对方负印记 (6)
    for mn in MARKS_NEG:
        m = _find_mark(opp_neg, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 魔力 (2)
    out.append(own.lives / 6.0)
    out.append(opp.lives / 6.0)

    # 己方道具 (6)
    out.extend(_encode_item(own.item))

    # 对方道具 (6)
    out.extend(_encode_item(opp.item))

    # 己方奉献 (5)
    dev_own = getattr(own, 'devotion', {}) or {}
    for dn in DEVOTION_ORDER:
        out.append(dev_own.get(dn, 0) / 10.0)

    # 对方奉献 (5)
    dev_opp = getattr(opp, 'devotion', {}) or {}
    for dn in DEVOTION_ORDER:
        out.append(dev_opp.get(dn, 0) / 10.0)

    return np.array(out, dtype=np.float32)


def _encode_item(item) -> list[float]:
    if item is None:
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    out = [
        1.0,                                           # has
        item.uses / max(item.max_uses, 1),             # uses ratio
        item.last_use_turn / max(item.cooldown_turns, 1) if item.cooldown_turns > 0 else 0.0,
        1.0 if item.is_exhausted else 0.0,
    ]
    # 道具类型 one-hot (2)
    for it in ITEM_TYPES:
        out.append(1.0 if item.name == it else 0.0)
    return out


# ═══════════════════════════════════════════════════════════════════
# 模块 B / D: 场上精灵 (115)
# ═══════════════════════════════════════════════════════════════════

def _encode_active_sprite(
    sprite: Sprite | None, player: Player,
    battle: Battle | None, opp_active: Sprite | None,
) -> np.ndarray:
    if sprite is None:
        return np.zeros(115, dtype=np.float32)

    out: list[float] = []

    # ── 本体 (51) ──

    # HP / 能量 (4)
    out.append(sprite.current_hp / max(sprite.max_hp, 1))
    out.append(sprite.energy / max(sprite.max_energy, 1))
    out.append(sprite.max_energy / 15.0)
    out.append(1.0 if sprite.is_fainted else 0.0)

    # 蓄力 (2)
    out.append(1.0 if _has_state(sprite, 'charging') else 0.0)
    out.append(1.0 if _has_state(sprite, 'charge_any_skill') else 0.0)

    # 修正值 (3)
    out.append(sprite.energy_cost_mod / 10.0)
    out.append(sprite.power_mod / 10.0)
    out.append(sprite._modifiers.get('blood_price', 0.0))

    # 速度 (1)
    out.append(sprite.effective_stat('speed') / 500.0)

    # 元素 one-hot (18)
    _one_hot(out, _sprite_primary_element(sprite), ELEMENT_ORDER)

    # 异常状态 (7)
    for an in ABNORMAL_ORDER:
        stacks = _get_abnormal_stacks(sprite, an)
        max_s = _abnormal_max(an)
        out.append(stacks / max(max_s, 1))

    # Buff/Debuff (10)
    for bk in BUFF_KEYS:
        out.append(_encode_buff(sprite, bk))

    # 未知掩码 (1) — 自对弈恒为 0
    out.append(0.0)

    # 有效先制度 (1)
    out.append(_effective_priority(sprite) / 3.0)

    # 锁换宠 (1)
    out.append(1.0 if getattr(sprite, 'locked_turns', 0) > 0 else 0.0)

    # 迸发 / 额外行动 / 待返场 (3)
    out.append(1.0 if sprite.first_action else 0.0)
    out.append(1.0 if sprite.extra_skill_use else 0.0)
    out.append(1.0 if sprite.pending_return else 0.0)

    # ── 技能 ×4 (64) ──
    skills: list = sprite.skills or []
    for i in range(4):
        sk = skills[i] if i < len(skills) else None
        out.extend(_encode_skill(sk, sprite, opp_active))

    return np.array(out, dtype=np.float32)


def _encode_skill(sk, sprite: Sprite, opp_sprite: Sprite | None) -> list[float]:
    """每技能 16 维。sk 为 None 时返回全零。"""
    if sk is None:
        return [0.0] * 16

    out: list[float] = []

    # 1. 有效威力（sk.power 属性已包含 _modifiers）
    eff_power = sk.power + sprite.power_mod * 10
    out.append(eff_power / 300.0)

    # 2. 有效能耗（sk.energy_cost 属性已包含 _modifiers + _mech_energy_reduction）
    out.append(sk.energy_cost / 15.0)

    # 3. 对对手克制倍率
    if opp_sprite is not None:
        opp_el = _sprite_primary_element(opp_sprite)
        adv = _type_advantage(sk.element, opp_el)
    else:
        adv = 1.0
    out.append((adv - 0.5) / 1.5)

    # 4. 本系加成
    out.append(1.0 if sk.element in getattr(sprite.species, 'elements', []) else 0.0)

    # 5. 冷却 / 封印
    if sk.sealed:
        out.append(1.0)
    elif sk.cooldown > 0:
        out.append(0.5)
    else:
        out.append(0.0)

    # 6. 传动等级
    out.append(getattr(sk, '_transmission', 0) / 3.0)

    # 7. 技能类型
    st = sk.skill_type
    if st == '物攻' or st == '动态攻击':
        out.append(0.0)
    elif st == '魔攻':
        out.append(0.33)
    elif st == '防御':
        out.append(0.67)
    elif st == '状态':
        out.append(1.0)
    else:
        out.append(0.5)

    # 8-12: 效果摘要 (5) — 从 JSON 加载（工厂创建的 BattleSkill 不含 effects）
    name = sk.name
    if name not in _skill_flat_effects_cache:
        _skill_flat_effects_cache[name] = _flatten_effects(_get_skill_effects(name))
    flat = _skill_flat_effects_cache[name]
    out.append(_summary_abnormal(flat) if flat else 0.0)
    out.append(_summary_stat_stage(flat) if flat else 0.0)
    out.append(_summary_heal_energy(flat) if flat else 0.0)
    out.append(_summary_weather(flat) if flat else 0.0)
    out.append(_summary_special_tag(flat) if flat else 0.0)

    # 13. 应对类型
    counter = getattr(sk, 'counter', '无') or '无'
    for i, ct in enumerate(COUNTER_ORDER):
        if ct == counter:
            out.append(i / 3.0)
            break
    else:
        out.append(0.0)

    # 14. 连击数
    combo = getattr(sk, 'combo', 1) or 1
    out.append(max(0, combo) / 5.0)

    # 15. 迸发技能
    out.append(1.0 if getattr(sk, 'has_burst', False) else 0.0)

    # 16. 下次攻击倍率
    out.append((getattr(sk, 'next_attack_mult', 1.0) - 1.0) / 1.0)

    return out


# ═══════════════════════════════════════════════════════════════════
# 模块 C / E: 板凳精灵 (每只 16, ×5 = 80)
# ═══════════════════════════════════════════════════════════════════

def _encode_bench_all(
    player: Player, opp_active: Sprite | None,
    *, mask_unknown: bool = False,
) -> np.ndarray:
    pieces: list[np.ndarray] = []
    team: list = player.team or []
    active_idx: int = getattr(player, 'active_index', 0)
    bench = [s for i, s in enumerate(team) if i != active_idx and not s.is_fainted]

    for slot in range(5):
        if slot < len(bench):
            if mask_unknown:
                pieces.append(np.full(16, -1.0, dtype=np.float32))
            else:
                pieces.append(_encode_bench_sprite(bench[slot], player, opp_active))
        else:
            pieces.append(np.zeros(16, dtype=np.float32))

    result = np.concatenate(pieces) if pieces else np.zeros(80, dtype=np.float32)
    return result


def _encode_bench_sprite(
    sprite: Sprite, player: Player, opp_active: Sprite | None,
) -> np.ndarray:
    out: list[float] = []

    # 1-3. HP / 能量 / 晕厥
    out.append(sprite.current_hp / max(sprite.max_hp, 1))
    out.append(sprite.energy / max(sprite.max_energy, 1))
    out.append(1.0 if sprite.is_fainted else 0.0)

    # 4. 对对方全队平均克制倍率
    own_el = _sprite_primary_element(sprite)
    type_advs: list[float] = []
    for s in (player.team or []):
        if s is sprite:
            continue
        adv = _type_advantage(own_el, _sprite_primary_element(s))
        type_advs.append(adv)
    avg_adv = sum(type_advs) / len(type_advs) if type_advs else 1.0
    out.append((avg_adv - 0.5) / 1.5)

    # 5-6. 对对方场上精灵克制 / 被克
    if opp_active is not None:
        opp_el = _sprite_primary_element(opp_active)
        out.append((_type_advantage(own_el, opp_el) - 0.5) / 1.5)
        out.append((_type_advantage(opp_el, own_el) - 0.5) / 1.5)
    else:
        out.append(0.0)
        out.append(0.0)

    # 7-8. 最高技能有效威力 / 最低技能有效能耗
    skills = sprite.skills or []
    max_pow = 0
    min_cost = 15
    for sk in skills:
        if sk.sealed:
            continue
        p = sk.power + sprite.power_mod * 10
        c = sk.energy_cost
        max_pow = max(max_pow, p)
        min_cost = min(min_cost, c)
    out.append(max_pow / 300.0)
    out.append(min_cost / 15.0)

    # 9. 可用技能数
    usable = 0
    for sk in skills:
        if not sk.sealed and sk.cooldown <= 0 and sk.energy_cost <= sprite.energy:
            usable += 1
    out.append(usable / 4.0)

    # 10. 有异常
    has_abnormal = False
    for an in ABNORMAL_ORDER:
        if _get_abnormal_stacks(sprite, an) > 0:
            has_abnormal = True
            break
    out.append(1.0 if has_abnormal else 0.0)

    # 11. 蓄力中
    out.append(1.0 if _has_state(sprite, 'charging') else 0.0)

    # 12-13. 攻击/防御 buff 均值
    atk_buffs = max(_encode_buff(sprite, 'atk'), _encode_buff(sprite, 'sp_atk'))
    def_buffs = max(_encode_buff(sprite, 'def'), _encode_buff(sprite, 'sp_def'))
    out.append(atk_buffs)
    out.append(def_buffs)

    # 14-15. 特性触发时机
    trait_listeners: set[str] = _get_trait_listeners(sprite)
    out.append(1.0 if 'post_entry' in trait_listeners else 0.0)
    out.append(1.0 if 'post_leave' in trait_listeners else 0.0)

    # 16. 迸发就绪（入场后首次行动）
    out.append(1.0 if sprite.first_action else 0.0)

    return np.array(out, dtype=np.float32)


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def _has_state(sprite: Sprite, state_type: str) -> bool:
    """检查精灵是否处于指定状态（如 charging / locked 等）。"""
    for eff in getattr(sprite, 'active_effects', []) or []:
        if isinstance(eff, StateEffect) and getattr(eff, 'state_type', '') == state_type:
            return True
    return False


def _active_or_none(player: Player) -> Sprite | None:
    idx = getattr(player, 'active_index', 0)
    team = player.team or []
    if idx < len(team) and not team[idx].is_fainted:
        return team[idx]
    for s in team:
        if not s.is_fainted:
            return s
    return None


def _sprite_primary_element(sprite: Sprite) -> str:
    elements = getattr(sprite.species, 'elements', [])
    return elements[0] if elements else '普通'


# Lazy-loaded type chart (cached after first access to avoid circular import at module level)
_TYPE_CHART_CACHE: dict | None = None


def _type_advantage(atk_el: str, def_el: str) -> float:
    global _TYPE_CHART_CACHE
    if _TYPE_CHART_CACHE is None:
        from backend.sim.resolver import _TYPE_CHART as _tc
        _TYPE_CHART_CACHE = _tc
    if atk_el not in _TYPE_CHART_CACHE:
        return 1.0
    return _TYPE_CHART_CACHE[atk_el].get(def_el, 1.0)


def _get_abnormal_stacks(sprite: Sprite, name: str) -> int:
    return sprite.get_stacks(name)


def _abnormal_max(name: str) -> int:
    return {
        '灼烧': 50,
        '冻结': 20,
        '中毒': 10,
        '寄生': 10,
        '萌化': 1,
        '晕眩': 1,
        '眩晕': 1,
    }.get(name, 10)


def _encode_buff(sprite: Sprite, key: str) -> float:
    if key in STAT_KEYS:
        steps = sprite._sum_steps(key)
        return np.clip(steps / 6.0, -1.0, 1.0)
    if key in ('power_mult', 'damage_mult', 'damage_reduction', 'life_drain'):
        val = sprite._modifiers.get(key, 0.0)
        return val
    if key in ('combo',):
        return sprite._modifiers.get(key, 0.0) / 6.0
    if key == 'power':
        return sprite.power_mod / 10.0
    return 0.0


def _effective_priority(sprite: Sprite) -> float:
    return float(np.clip(sprite.priority_mod, -3.0, 3.0))


def _classify_marks(g, team: str) -> tuple[list, list]:
    marks: list = (getattr(g, 'mark_effects', {}) or {}).get(team, [])
    pos: list = []
    neg: list = []
    for m in marks:
        cat = getattr(m, 'category', 'positive')
        if cat == 'positive':
            pos.append(m)
        else:
            neg.append(m)
    return pos, neg


def _find_mark(marks: list, name: str):
    for m in marks:
        if getattr(m, 'name', '') == name:
            return m
    return None


def _one_hot(out: list[float], value: str, order: tuple[str, ...]) -> None:
    idx = order.index(value) if value in order else -1
    for i in range(len(order)):
        out.append(1.0 if i == idx else 0.0)


# ═══════════════════════════════════════════════════════════════════
# 技能效果惰性加载（绕过工厂的 effects=[], 直接读 JSON op 格式）
# ═══════════════════════════════════════════════════════════════════

def _get_skills_dir() -> Path:
    global _skills_dir
    if _skills_dir is None:
        _skills_dir = Path(__file__).resolve().parent.parent.parent / 'data' / 'skills'
    return _skills_dir


def _get_skill_effects(name: str) -> list[dict]:
    """从 JSON 惰性加载技能的完整效果列表（原始 dict 格式）。"""
    if not name:
        return []
    if name not in _skill_effects_cache:
        path = _get_skills_dir() / f'{name}.json'
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            _skill_effects_cache[name] = data.get('effects', [])
        else:
            _skill_effects_cache[name] = []
    return _skill_effects_cache[name]


# ═══════════════════════════════════════════════════════════════════
# 技能效果摘要（展平 JSON op 格式 + 分类）
# ═══════════════════════════════════════════════════════════════════

def _flatten_effects(effects: list) -> list[dict]:
    """递归展平技能效果列表（处理 when/then/else + observer then 嵌套）。"""
    result: list[dict] = []
    for e in effects:
        if not isinstance(e, dict):
            continue
        # 递归展平 then/else 分支（when 条件块 / observer 块）
        for branch_key in ('then', 'else'):
            branch = e.get(branch_key, [])
            if isinstance(branch, list):
                result.extend(_flatten_effects(branch))
        # 顶层 op（含 observer / when 块本身）
        if 'op' in e:
            result.append(e)
    return result


def _summary_abnormal(flat: list[dict]) -> float:
    for e in flat:
        if e.get('op') == 'abnormal':
            name = e.get('name', '')
            for i, an in enumerate(ABNORMAL_ORDER):
                if an == name:
                    return (i + 1) / 7.0
    return 0.0


def _summary_stat_stage(flat: list[dict]) -> float:
    total = 0.0
    for e in flat:
        if e.get('op') == 'stat_stage':
            steps = e.get('steps', 0)
            if isinstance(steps, (int, float)):
                total += steps * 0.25
            elif isinstance(steps, dict):
                # 动态 steps（如 "abnormal_stacks * 3"），用 scale 近似
                total += steps.get('scale', 0) * 0.25
    return float(np.clip(total, -1.0, 1.0))


def _summary_heal_energy(flat: list[dict]) -> float:
    for e in flat:
        op = e.get('op', '')
        if op == 'heal':
            return e.get('ratio', 0.0)
        if op == 'energize':
            return e.get('amount', 0) / 10.0
        if op == 'steal':
            return e.get('amount', 0) / 10.0
    return 0.0


def _summary_weather(flat: list[dict]) -> float:
    for e in flat:
        if e.get('op') == 'weather':
            name = e.get('name', '')
            for i, wt in enumerate(WEATHER_ORDER):
                if wt == name:
                    return (i + 1) / 4.0
    return 0.0


def _summary_special_tag(flat: list[dict]) -> float:
    """摘要: 特殊标签 — 先制/蓄力/打断/印记/逃离 组合编码。

    bit layout: priority(16) + charge(8) + interrupt(4) + mark(2) + escape(1) = 31
    """
    bits = 0
    for e in flat:
        op = e.get('op', '')
        if op in ('power_mod', 'mult_mod'):
            bits |= 16
        if op == 'charge':
            bits |= 8
        if op == 'interrupt':
            bits |= 4
        if op == 'mark':
            bits |= 2
        if op in ('escape', 'return'):
            bits |= 1
    return bits / 31.0


def _get_trait_listeners(sprite: Sprite) -> set[str]:
    listeners: set[str] = set()
    for eff in getattr(sprite, 'active_effects', []) or []:
        if isinstance(eff, ObserverEffect):
            listen = getattr(eff, 'listen', frozenset())
            if isinstance(listen, frozenset):
                listeners.update(listen)
            elif isinstance(listen, (list, tuple)):
                listeners.update(listen)
            elif isinstance(listen, str):
                listeners.add(listen)
    return listeners
