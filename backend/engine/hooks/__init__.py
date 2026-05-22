"""Engine-level trait hooks (Phase C4).

Hooks that modify engine behavior based on trait presence.
Moved from backend/sim/traits/_complex.py to keep the engine self-contained.

Each hook is a callback registered via register_hook() and fires at a
specific engine interception point (before_apply_mark, max_energy_override,
etc.). The hook checks whether the sprite has the relevant trait using
trait_id comparison.
"""

from backend.common.skill_trait_ids import (
    TRAIT_多人宿舍,
    TRAIT_守望星,
    TRAIT_星地善良,
    TRAIT_机械变式,
    TRAIT_吟游之弦,
    TRAIT_惊吓,
    TRAIT_石头大餐,
    TRAIT_不朽,
)
from backend.sim.traits.trait_engine import register_hook


# ── 吟游之弦: mark coexistence mode ──

def _bard_before_apply_mark(team, name, category, stacks, user):
    """吟游之弦: 印记共存模式。"""
    if user is None:
        return None
    from backend.sim.traits import get_trait
    h = get_trait(user)
    return 'coexist' if (h and h.trait_id == TRAIT_吟游之弦) else None


register_hook('before_apply_mark', _bard_before_apply_mark, '吟游之弦')


# ── 多人宿舍: max energy 10→15 ──

def _dorm_max_energy(sprite):
    """多人宿舍: 能量上限 10→15。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    return 15 if (h and h.trait_id == TRAIT_多人宿舍) else None


register_hook('max_energy_override', _dorm_max_energy, '多人宿舍')


# ── 守望星: starfall consume halved ──

def _starguard_consume_starfall(team, amount, sprite, starfall_mark):
    """守望星: 星陨消耗减半。"""
    if sprite is None:
        return None
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    return max(1, amount // 2) if (h and h.trait_id == TRAIT_守望星) else None


register_hook('before_consume_starfall', _starguard_consume_starfall, '守望星')


# ── 星地善良: bench auto-swap when active energy=0 ──

def _starground_bench_check(battle, team, active, player):
    """星地善良: 己方能量=0时主动替换上场。"""
    if active.is_fainted or active.energy > 0:
        return None
    for i, bench in enumerate(player.team):
        if i == player.active_index or bench.is_fainted:
            continue
        from backend.sim.traits import get_trait
        h = get_trait(bench)
        if h and h.trait_id == TRAIT_星地善良:
            return (i, '星地善良')
    return None


register_hook('turn_end_bench_check', _starground_bench_check, '星地善良')


# ── 机械变式: skill position change → cost -1 ──

def _mechvar_after_transmission(sprite, prev, battle, team):
    """机械变式: 传动后技能位置变化 → 能耗-1（仅限拥有此特性的精灵）。"""
    from backend.sim.traits import get_trait
    trait = get_trait(sprite)
    if trait is None or trait.trait_id != TRAIT_机械变式:
        return None
    events = []
    for i, bs in enumerate(sprite.skills):
        if i < len(prev) and bs.name != prev[i]:
            bs.base.energy_cost = max(0, bs.base.energy_cost - 1)
            events.append(f'{sprite.name} 机械变式: {bs.name} 能耗-1(传动位移)')
    return events if events else None


register_hook('after_transmission', _mechvar_after_transmission, '机械变式')


# ── 石头大餐: energy short → HP substitute ──

def _stone_feast_energy_short(sprite, cost, battle, team):
    """石头大餐: 能量不足时消耗5%生命代替1能量。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    return round(sprite.max_hp * 0.05 * cost) if (h and h.trait_id == TRAIT_石头大餐) else None


register_hook('on_energy_short', _stone_feast_energy_short, '石头大餐')


# ── 惊吓: fatal damage prevention ──

def _fright_fatal_damage(sprite, damage, battle, team):
    """惊吓: 能量=0的精灵无法对自己造成伤害。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    if h is None or h.trait_id != TRAIT_惊吓:
        return None
    opp_team = 'B' if team == 'A' else 'A'
    attacker = battle.get_opponent(team).active
    return attacker.energy == 0


register_hook('on_fatal_damage', _fright_fatal_damage, '惊吓')


# ── 不朽: record faint turn ──

def _immortal_on_faint(sprite, killer, battle, team):
    """不朽: 力竭3回合后复活。记录力竭回合。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    if h is None or h.trait_id != TRAIT_不朽:
        return None
    sprite._faint_turn = battle.turn
    return [f'{sprite.name} 不朽: 第{battle.turn}回合力竭, 3回合后复活']


register_hook('on_faint', _immortal_on_faint, '不朽')
