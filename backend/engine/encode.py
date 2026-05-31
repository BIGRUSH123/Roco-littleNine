"""backend/engine/encode.py — Battle 状态 → 固定长度向量，供神经网络输入。

总维度: 394

约定:
- 空槽位（阵亡 / 不存在）→ 全 0
- 不完全信息（对方板凳特征不可见）→ -1
- 特性不直接编码——其效果已反映在 buff/debuff/异常/印记等状态中
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from backend.common.constants import STAT_KEYS
from backend.sim.effects import (
    AbnormalEffect as SkillAbnormal,
    ConditionalEffect,
    MarkEffect as SkillMark,
    SpecialEffect,
    StatEffect,
    WeatherEffect as SkillWeather,
)
from backend.vm.effect import (
    AbnormalEffect as RuntimeAbnormal,
    MarkEffect as RuntimeMark,
    StatBuffEffect,
    StateEffect,
)
from backend.vm.ir_skill import WhenBlock

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

BUFF_KEYS: tuple[str, ...] = (
    'atk', 'def', 'sp_atk', 'sp_def', 'speed',
    'power_mult', 'damage_mult', 'damage_reduction', 'life_drain', 'combo',
)

STAT_MAP: dict[str, int] = {k: i for i, k in enumerate(STAT_KEYS)}  # {'atk':1, 'def':2, ...}

_EMPTY = object()


# ═══════════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════════

def encode_battle_state(battle: Battle, *, mask_opp_bench: bool = False) -> np.ndarray:
    """将 Battle 状态编码为 (394,) float32 向量。

    Args:
        battle: 对局对象。
        mask_opp_bench: 若 True，对方板凳特征以 -1 填充（模拟不完全信息）。
    """
    pieces: list[np.ndarray] = []

    # A: 全局状态 (52)
    pieces.append(_encode_global(battle))

    # B: 己方场上精灵 (96)
    player_a: Player = battle.player_a
    active_a: Sprite | None = _active_or_none(player_a)
    opp_active: Sprite | None = _active_or_none(battle.player_b)
    pieces.append(_encode_active_sprite(active_a, player_a, battle, opp_active))

    # C: 己方板凳 ×5 (75)
    pieces.append(_encode_bench_all(player_a, opp_active, mask_unknown=False))

    # D: 对方场上精灵 (96)
    player_b: Player = battle.player_b
    active_b: Sprite | None = _active_or_none(player_b)
    pieces.append(_encode_active_sprite(active_b, player_b, battle, active_a))

    # E: 对方板凳 ×5 (75)
    pieces.append(_encode_bench_all(player_b, active_a, mask_unknown=mask_opp_bench))

    result = np.concatenate(pieces)
    assert result.shape == (394,), f"维度错误: {result.shape}"
    return result.astype(np.float32)


# ═══════════════════════════════════════════════════════════════════
# 模块 A: 全局状态 (52)
# ═══════════════════════════════════════════════════════════════════

def _encode_global(battle: Battle) -> np.ndarray:
    g = battle.globals
    a: Player = battle.player_a
    b: Player = battle.player_b
    out: list[float] = []

    # 回合 (2)
    t = battle.turn
    out.append(t / 150.0)
    out.append(1.0 - t / 150.0)

    # 天气 (3)
    w = g.weather or ""
    for wt in WEATHER_ORDER[:3]:  # rain/sand/snow only, 暴风雪 is snow variant
        out.append(1.0 if w == wt else 0.0)

    # 天气剩余 (1)
    out.append(g.weather_turns / 8.0)

    # 己方正印记 (7)
    marks_a_pos, marks_a_neg = _classify_marks(g, "A")
    for mn in MARKS_POS:
        m = _find_mark(marks_a_pos, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 己方负印记 (6)
    for mn in MARKS_NEG:
        m = _find_mark(marks_a_neg, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 对方正印记 (7)
    marks_b_pos, marks_b_neg = _classify_marks(g, "B")
    for mn in MARKS_POS:
        m = _find_mark(marks_b_pos, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 对方负印记 (6)
    for mn in MARKS_NEG:
        m = _find_mark(marks_b_neg, mn)
        out.append(m.stacks / 10.0 if m else 0.0)

    # 魔力 (2)
    out.append(a.lives / 6.0)
    out.append(b.lives / 6.0)

    # 己方道具 (4)
    out.extend(_encode_item(a.item))

    # 对方道具 (4)
    out.extend(_encode_item(b.item))

    # 己方奉献 (5)
    dev_a = getattr(a, 'devotion', {}) or {}
    for dn in DEVOTION_ORDER:
        out.append(dev_a.get(dn, 0) / 10.0)

    # 对方奉献 (5)
    dev_b = getattr(b, 'devotion', {}) or {}
    for dn in DEVOTION_ORDER:
        out.append(dev_b.get(dn, 0) / 10.0)

    return np.array(out, dtype=np.float32)


def _encode_item(item) -> list[float]:
    if item is None:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        1.0,                                           # has
        item.uses / max(item.max_uses, 1),             # uses ratio
        item.last_use_turn / max(item.cooldown_turns, 1) if item.cooldown_turns > 0 else 0.0,
        1.0 if getattr(item, 'is_exhausted', False) else 0.0,
    ]


# ═══════════════════════════════════════════════════════════════════
# 模块 B / D: 场上精灵 (96)
# ═══════════════════════════════════════════════════════════════════

def _encode_active_sprite(
    sprite: Sprite | None, player: Player,
    battle: Battle | None, opp_active: Sprite | None,
) -> np.ndarray:
    if sprite is None:
        return np.zeros(96, dtype=np.float32)

    out: list[float] = []

    # ── 本体 (47) ──

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
        # 每种异常有不同 max，归一化
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

    # ── 技能 ×4 (48) ──
    skills: list = sprite.skills or []
    for i in range(4):
        sk = skills[i] if i < len(skills) else None
        out.extend(_encode_skill(sk, sprite, opp_active))

    return np.array(out, dtype=np.float32)


def _encode_skill(sk, sprite: Sprite, opp_sprite: Sprite | None) -> list[float]:
    """每技能 12 维。sk 为 None 时返回全零。"""
    if sk is None:
        return [0.0] * 12

    out: list[float] = []

    # 1. 有效威力（sk.power 属性已包含 _modifiers）
    eff_power = sk.power + sprite.power_mod * 10
    out.append(eff_power / 300.0)

    # 2. 有效能耗（sk.energy_cost 属性已包含 _modifiers + _mech_energy_reduction）
    eff_cost = sk.energy_cost
    out.append(eff_cost / 15.0)

    # 3. 对对手克制倍率
    if opp_sprite is not None:
        opp_el = _sprite_primary_element(opp_sprite)
        adv = _type_advantage(sk.element, opp_el)
    else:
        adv = 1.0
    out.append((adv - 0.5) / 1.5)  # 0.5→0, 1→0.33, 2→1

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

    # 8-12: 效果摘要 (5)
    effects = sk.effects if sk.effects else []
    flat = _flatten_effects(effects)
    out.append(_summary_abnormal(flat) if flat else 0.0)
    out.append(_summary_stat_stage(flat) if flat else 0.0)
    out.append(_summary_heal_energy(flat) if flat else 0.0)
    out.append(_summary_weather(flat) if flat else 0.0)
    out.append(_summary_special_tag(flat) if flat else 0.0)

    return out


# ═══════════════════════════════════════════════════════════════════
# 模块 C / E: 板凳精灵 (每只 15, ×5 = 75)
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
                pieces.append(np.full(15, -1.0, dtype=np.float32))
            else:
                pieces.append(_encode_bench_sprite(bench[slot], player, opp_active))
        else:
            pieces.append(np.zeros(15, dtype=np.float32))

    result = np.concatenate(pieces) if pieces else np.zeros(75, dtype=np.float32)
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


def _type_advantage(atk_el: str, def_el: str) -> float:
    from backend.sim.resolver import _TYPE_CHART
    if atk_el not in _TYPE_CHART:
        return 1.0
    return _TYPE_CHART[atk_el].get(def_el, 1.0)


def _get_abnormal_stacks(sprite: Sprite, name: str) -> int:
    return sprite.get_stacks(name)


def _abnormal_max(name: str) -> int:
    """各异常状态的参考最大层数（用于归一化）。"""
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
    """将 buff/debuff 编码为有符号归一化值。

    倍率型 (power_mult, damage_mult 等): value - 1.0
    步数型 (atk, def 等): steps / 6
    绝对值型 (combo): steps 直接取
    """
    if key in STAT_KEYS:  # atk, def, sp_atk, sp_def, speed
        steps = sprite._sum_steps(key)
        return np.clip(steps / 6.0, -1.0, 1.0)
    if key in ('power_mult', 'damage_mult', 'damage_reduction', 'life_drain'):
        val = sprite._modifiers.get(key, 0.0)
        if key == 'damage_reduction':
            return val  # 增量型，0.0-1.0
        return val  # power_mult/damage_mult 可能存总值或增量
    if key in ('combo',):
        return sprite._modifiers.get(key, 0.0) / 6.0
    if key == 'power':
        return sprite.power_mod / 10.0
    return 0.0


def _effective_priority(sprite: Sprite) -> float:
    """当前有效先制度（考虑 sprite 级和技能级 modifier）。"""
    return float(np.clip(sprite.priority_mod, -3.0, 3.0))


def _classify_marks(g, team: str) -> tuple[list, list]:
    """将团队印记分为正/负两类。返回 (pos_list, neg_list)。"""
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
# 技能效果摘要（展平 + 分类）
# ═══════════════════════════════════════════════════════════════════

def _flatten_effects(effects: list) -> list:
    """递归展平技能效果列表（处理 ConditionalEffect / WhenBlock 嵌套）。"""
    result: list = []
    for e in effects:
        # 编译后的 WhenBlock
        if isinstance(e, WhenBlock):
            for branch in (e.then + e.else_ + e.elif_):
                result.extend(_flatten_effects(branch))
        elif isinstance(e, (tuple, list)):
            result.extend(_flatten_effects(e))
        # Dataclass 的 ConditionalEffect
        elif isinstance(e, ConditionalEffect):
            if e.then:
                result.extend(_flatten_effects(e.then))
        else:
            result.append(e)
    return result


def _summary_abnormal(flat: list) -> float:
    """摘要: 是否施加异常 → 异常类型ID / 7。取第一个找到的。"""
    for e in flat:
        if isinstance(e, SkillAbnormal):
            name = e.name
            for i, an in enumerate(ABNORMAL_ORDER):
                if an == name:
                    return (i + 1) / 7.0
    return 0.0


def _summary_stat_stage(flat: list) -> float:
    """摘要: 属性等级修改幅度（有符号归一化）。"""
    total = 0.0
    for e in flat:
        if isinstance(e, StatEffect):
            steps = getattr(e, 'steps', 0)
            total += steps * 0.25  # +1 step → 0.25
    return np.clip(total, -1.0, 1.0)  # type: ignore[return-value]


def _summary_heal_energy(flat: list) -> float:
    """摘要: 治疗/能量变化。正=回复, 负=损耗。"""
    for e in flat:
        if isinstance(e, SpecialEffect):
            name = getattr(e, 'name', '')
            if name == 'heal' or name == 'direct_heal':
                return getattr(e, 'value', 0.0)  # ratio like 0.3
            if name == 'gain_energy':
                return getattr(e, 'amount', 0) / 10.0
            if name == 'steal_energy':
                return getattr(e, 'amount', 0) / 10.0
            if name == 'life_drain':
                return getattr(e, 'value', 0.0)  # ratio
    return 0.0


def _summary_weather(flat: list) -> float:
    """摘要: 天气设置 → 天气ID / 4。"""
    for e in flat:
        if isinstance(e, SkillWeather):
            weather = getattr(e, 'weather', '')
            for i, wt in enumerate(WEATHER_ORDER):
                if wt == weather:
                    return (i + 1) / 4.0
    return 0.0


def _summary_special_tag(flat: list) -> float:
    """摘要: 特殊标签 — 先制/蓄力/打断/印记/逃离 组合编码。

    bit layout: priority(16) + charge(8) + interrupt(4) + mark(2) + escape(1) = 31
    """
    bits = 0
    for e in flat:
        if isinstance(e, SpecialEffect):
            name = getattr(e, 'name', '')
            if name in ('priority_bonus',):
                bits |= 16
            if name in ('charge',):
                bits |= 8
            if name in ('interrupt',):
                bits |= 4
            if name in ('escape', 'escape_inherit', 'force_return', 'return_self'):
                bits |= 1
        if isinstance(e, SkillMark):
            bits |= 2
    return bits / 31.0


def _get_trait_listeners(sprite: Sprite) -> set[str]:
    """从精灵的 active_effects 中提取特性监听时机。"""
    listeners: set[str] = set()
    from backend.vm.effect import ObserverEffect

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
