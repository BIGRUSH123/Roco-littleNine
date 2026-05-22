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
    TRAIT_宝剑王牌,
    TRAIT_正位宝剑,
    TRAIT_倾轧,
    TRAIT_腾挪,
    TRAIT_保卫,
    TRAIT_好象坏象,
    TRAIT_系统发育,
    TRAIT_刺肤,
    TRAIT_嫁祸,
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


# ── 宝剑王牌: seal skill slots 2,4 → only 1,3 usable ──

def _sword_ace_post_entry(sprite, battle, team):
    """宝剑王牌: 封印2号和4号位技能。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    if h is None or h.trait_id != TRAIT_宝剑王牌:
        return None
    for i, bs in enumerate(sprite.skills):
        if i not in (0, 2):
            bs.sealed = True
    return [f'{sprite.name} 宝剑王牌: 仅1号/3号位可用']


register_hook('post_entry', _sword_ace_post_entry, '宝剑王牌')


# ── 正位宝剑: seal skill slots 2,3,4 → only 1 usable ──

def _upright_sword_post_entry(sprite, battle, team):
    """正位宝剑: 封印2/3/4号位技能。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    if h is None or h.trait_id != TRAIT_正位宝剑:
        return None
    for i, bs in enumerate(sprite.skills):
        if i != 0:
            bs.sealed = True
    return [f'{sprite.name} 正位宝剑: 仅1号位可用']


register_hook('post_entry', _upright_sword_post_entry, '正位宝剑')


# ── 倾轧: energy cost modifier doubled ──

def _crush_pre_modifier(user, use, battle, team):
    """倾轧: 技能受能耗变化效果影响翻倍。"""
    from backend.sim.traits import get_trait
    h = get_trait(user)
    if h is None or h.trait_id != TRAIT_倾轧:
        return None
    total = sum(e.steps for e in user.effects if e.category == 'stat'
                and e.stat_key == 'energy_cost')
    if total == 0:
        return None
    if total > 0:
        user.lose_energy(total)
        return [f'{user.name} 倾轧: 能耗翻倍, 额外消耗{total}E']
    else:
        user.gain_energy(-total)
        return [f'{user.name} 倾轧: 能耗翻倍, 返还{-total}E']


register_hook('pre_modifier', _crush_pre_modifier, '倾轧')


# ── 裂口组共用: transform to 棋绮后 ──

def _try_transform(user, battle, team, trait_name):
    """回满状态并变换为棋绮后。"""
    from backend.common.models import SpeciesStats

    new_species = battle.lookup_species('棋绮后')
    if new_species is None:
        s = user.species
        new_species = SpeciesStats(
            name='棋绮后', form='',
            hp=s.hp, atk=s.atk, sp_atk=s.sp_atk,
            def_=s.def_, sp_def=s.sp_def, speed=s.speed,
            attributes=s.attributes, ability=s.ability,
        )
    new_skills = battle.build_skills([])
    user.heal(user.max_hp)
    user.energy = 10
    user.clear_effects('battlefield')
    events = user.transform(new_species, new_skills)
    events.insert(0, f'{user.name} {trait_name}: 回满状态→棋绮后')
    return events


# ── 腾挪: counter attack → transform ──

def _evasive_post_counter(user, countered_skill, battle, team):
    """腾挪: 攻击技能应对1次后，回满状态，变为棋绮后。"""
    from backend.sim.traits import get_trait
    h = get_trait(user)
    if h is None or h.trait_id != TRAIT_腾挪:
        return None
    if not countered_skill.base.is_attack:
        return None
    return _try_transform(user, battle, team, '腾挪')


register_hook('post_counter', _evasive_post_counter, '腾挪')


# ── 保卫: counter defense 2x → transform ──

def _defend_post_counter(user, countered_skill, battle, team):
    """保卫: 防御技能应对2次后，回满状态，变为棋绮后。"""
    from backend.sim.traits import get_trait
    h = get_trait(user)
    if h is None or h.trait_id != TRAIT_保卫:
        return None
    if not countered_skill.base.is_defense:
        return None
    count = user.inc_counter('defend_countered')
    if count >= 2:
        user.counters['defend_countered'] = 0
        return _try_transform(user, battle, team, '保卫')
    return [f'{user.name} 保卫: 防御应对 {count}/2']


register_hook('post_counter', _defend_post_counter, '保卫')


# ── 好象坏象: counter status → transform ──

def _elephant_post_counter(user, countered_skill, battle, team):
    """好象坏象: 状态技能应对1次后，回满状态，变为棋绮后。"""
    from backend.sim.traits import get_trait
    h = get_trait(user)
    if h is None or h.trait_id != TRAIT_好象坏象:
        return None
    if not countered_skill.base.is_status:
        return None
    return _try_transform(user, battle, team, '好象坏象')


register_hook('post_counter', _elephant_post_counter, '好象坏象')


# ── 系统发育: energy gain → random bench distribution ──

def _phylogeny_energy_change(sprite, delta, new_energy, battle, team):
    """系统发育: 获得能量时等量随机分配给场下精灵。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    if h is None or h.trait_id != TRAIT_系统发育:
        return None
    if delta <= 0:
        return None
    player = battle.get_player(team)
    bench = [s for i, s in enumerate(player.team)
             if i != player.active_index and not s.is_fainted]
    if not bench:
        return None
    import random
    target = random.choice(bench)
    gained = target.gain_energy(delta)
    return [f'{sprite.name} 系统发育: {target.name} +{gained}E']


register_hook('post_energy_change', _phylogeny_energy_change, '系统发育')


# ── 刺肤: damage reflection (50 power physical) ──

def _thorn_skin_take_damage(target, attacker, damage, battle, team):
    """刺肤: 每受1次攻击，对攻击者造成50威力物理伤害。"""
    from backend.sim.traits import get_trait
    h = get_trait(target)
    if h is None or h.trait_id != TRAIT_刺肤:
        return None
    if attacker is None or attacker.is_fainted or damage <= 0:
        return None
    raw = round(target.effective_stat('atk') * 50 / max(1, attacker.effective_stat('def')))
    dealt = attacker.take_damage(raw)
    return [f'{target.name} 刺肤: 反伤{attacker.name}-{dealt}HP']


register_hook('post_take_damage', _thorn_skin_take_damage, '刺肤')


# ── 嫁祸: HP loss → combo gain ──

def _scapegoat_post_entry(sprite, battle, team):
    """嫁祸: 入场初始化HP追踪。"""
    from backend.sim.traits import get_trait
    h = get_trait(sprite)
    if h is None or h.trait_id != TRAIT_嫁祸:
        return None
    sprite.counters['scapegoat_quarters'] = 0
    return None


register_hook('post_entry', _scapegoat_post_entry, '嫁祸')


def _scapegoat_take_damage(target, attacker, damage, battle, team):
    """嫁祸: 每失去25%生命，连击数+2。"""
    from backend.sim.traits import get_trait
    from backend.sim.sprite import StatusEffect
    h = get_trait(target)
    if h is None or h.trait_id != TRAIT_嫁祸:
        return None
    hp_pct = target.current_hp / target.max_hp
    lost_quarters = int((1.0 - hp_pct) / 0.25)
    prev = target.counters.get('scapegoat_quarters', 0)
    if lost_quarters > prev:
        gained = lost_quarters - prev
        target.counters['scapegoat_quarters'] = lost_quarters
        target.add_effect(StatusEffect(
            name='嫁祸连击', category='stat', stat_key='combo',
            steps=gained * 2, scope='battlefield', source='嫁祸'))
        return [f'{target.name} 嫁祸: 连击+{gained * 2}']
    return None


register_hook('post_take_damage', _scapegoat_take_damage, '嫁祸')
