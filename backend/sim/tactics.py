"""backend/sim/tactics.py — 只读的战术预判助手（不落任何伤害、不改任何状态）。

RuleAgentV2 的"高级思考"要用到、但引擎只给了"结算并落伤"入口的那几件事，
这里按**引擎同一套公式**做纯预测：

1. **出手顺序** `moves_first`：引擎顺序 = 先手值（技能 `priority` + `sprite.priority_mod`，
   聚能记 0）→ **印记减速后**的速度 → 随机（`battle.py:1136`）。旧 agent 只比裸速度，
   带先手值的技能会判错，印记减速也没算。
2. **回合末致死** `predict_turn_end_damage`：异常 tick = `max(1, round(max_hp × pct × stacks))`
   ×元素克制（`resolver.turn_end`），印记末伤 = `max(1, round(max_hp × pct × stacks))`
   （`globals.mark_turn_end_effects`）。引擎没有"只算不落伤"的入口，这里是同一套算术。
3. **星陨印记追加伤害** `starfall_bonus`：非幻攻击命中后消耗层数并追加幻系伤害，
   威力 = X² + 24X − 24（X = 触发前层数，`globals.trigger_starfall`）。加上它才能看
   出"我这一击 + 印记 = 斩杀"这种组合杀。
4. **换人进场伤害** `switch_in_damage`：`globals.mark_switch_damage` 本身就是纯函数。
5. **对手换人倾向** `opponent_likely_switch`：启发式——对面这只留场必死（我这边能一击斩杀
   或它被 tick 死）且有活的替补 → 大概率撤。

全部函数对"轻量假 battle / 缺字段"容错，返回安全默认值。
"""
from __future__ import annotations


def opponent_team(team: str) -> str:
    """'A' ↔ 'B'。"""
    return 'B' if team == 'A' else 'A'


def effective_speed(battle, sprite, team: str) -> int:
    """有效速度（含强化步数与印记减速，与引擎出手判定一致）。"""
    try:
        penalty = battle.globals.mark_speed_penalty(team)
    except AttributeError:
        penalty = 0
    return sprite.effective_stat('speed') - penalty


def attack_priority(sprite, skill) -> int:
    """该技能的有效先手值 = 技能先手值 + 精灵先手修正。"""
    base = getattr(skill, 'priority', 0) if skill is not None else 0
    return base + getattr(sprite, 'priority_mod', 0)


def best_attack_priority(sprite) -> int:
    """本回合可达的最高先手值（取可用攻击技；没有攻击技时按聚能记 0）。"""
    best = 0
    for skill in getattr(sprite, 'skills', ()) or ():
        if not skill.is_attack or skill.cooldown > 0 or skill.sealed:
            continue
        best = max(best, attack_priority(sprite, skill))
    return best


def moves_first(battle, my_sprite, my_team: str, opp_sprite, opp_team_: str) -> bool:
    """我这只本回合会不会先出手。

    最坏情况假设：双方都取各自最高先手值的攻击。先手值相等才比（印记减速后的）速度，
    与引擎 `battle.py:1136` 的判定顺序一致。
    """
    my_p, opp_p = best_attack_priority(my_sprite), best_attack_priority(opp_sprite)
    if my_p != opp_p:
        return my_p > opp_p
    return effective_speed(battle, my_sprite, my_team) >= effective_speed(
        battle, opp_sprite, opp_team_)


def switch_in_damage(battle, team: str, sprite) -> int:
    """该精灵换上场时会吃的印记进场伤害（引擎纯函数）。"""
    try:
        return int(battle.globals.mark_switch_damage(team, sprite))
    except AttributeError:
        return 0


def predict_turn_end_damage(battle, sprite, team: str) -> int:
    """回合末会落在该精灵身上的固定伤害（异常 tick + 印记末伤），只算不落伤。

    只统计**当前场上**这只：异常 tick 跟随精灵、印记末伤只打该侧当前上场者，
    与引擎 `/turn_end` 的行为一致（换人可躲 tick）。
    """
    total = 0
    for effect in getattr(sprite, 'active_effects', None) or []:
        pct = getattr(effect, 'tick_damage_pct', 0) or 0
        stacks = getattr(effect, 'stacks', 0) or 0
        if pct <= 0 or stacks <= 0:
            continue
        if getattr(effect, 'tick_per_stack', False):
            raw = max(1, round(sprite.max_hp * pct * stacks))
        else:
            raw = max(1, round(sprite.max_hp * pct))
        total += max(1, round(raw * _tick_multiplier(sprite, effect)))

    try:
        marks = battle.globals.mark_effects.get(team, []) or []
    except AttributeError:
        marks = []
    for mark in marks:
        pct = getattr(mark, 'turn_end_damage_pct', 0) or 0
        stacks = getattr(mark, 'stacks', 0) or 0
        if pct > 0 and stacks > 0:
            total += max(1, round(sprite.max_hp * pct * stacks))
    return total


def _tick_multiplier(sprite, effect) -> float:
    """异常 tick 的元素克制倍率（与 `resolver.turn_end` 同源）。"""
    try:
        from backend.sim.resolver import SkillResolver

        return SkillResolver._tick_multiplier(
            sprite, getattr(effect, 'name', ''), getattr(effect, 'tick_element', '') or '')
    except (ImportError, AttributeError, TypeError):
        return 1.0


def starfall_bonus(battle, attacker, defender, skill, defender_team: str) -> int:
    """星陨印记的追加伤害预测（0 = 不触发）。

    触发条件与引擎一致：非幻系**攻击**技能命中后，消耗该侧印记层数并追加幻系伤害；
    威力按**触发前层数** X 计（X² + 24X − 24），不吃技能的威力/克制加成，只吃攻防面板、
    幻系克制与减伤。
    """
    if getattr(skill, 'element', '') == '幻':
        return 0
    if getattr(skill, 'skill_type', '') not in ('物攻', '魔攻', '动态攻击'):
        return 0
    try:
        marks = battle.globals.mark_effects.get(defender_team, []) or []
    except AttributeError:
        return 0
    for mark in marks:
        if getattr(mark, 'name', '') != '星陨印记':
            continue
        stacks = getattr(mark, 'stacks', 0) or 0
        if stacks <= 0:
            continue
        power = stacks * stacks + 24 * stacks - 24
        if power <= 0:
            return 0
        get_keys = getattr(skill, 'get_atk_def_keys', None)
        keys = get_keys(attacker) if callable(get_keys) else None
        if not keys:
            return 0
        atk_key, def_key = keys
        atk = attacker.effective_stat(atk_key)
        defense = max(1, defender.effective_stat(def_key))
        type_mult = battle.globals._element_mult('幻', defender)
        reduction = min(1.0, max(0.0, defender._modifiers.get('damage_reduction', 0.0)))
        if reduction >= 1.0:
            return 0
        raw = round(power * atk / defense * (37.0 / 41.0) * type_mult * (1.0 - reduction))
        return max(1, raw)
    return 0


def opponent_likely_switch(battle, my_team: str, opp, my_best_dmg: int) -> bool:
    """对手大概率撤人吗（启发式）。

    两个留场必死的信号 + 有活的替补可换：① 我这边能一击把当前这只打到 0（`my_best_dmg`
    ≥ 它的当前 HP）；② 它这回合结束会被异常/印记 tick 死（换人正好能躲掉 tick）。
    信息是全公开的（对手的替补席也看得到），这里只用"它有没有地方可去"。
    """
    if opp is None:
        return False
    bench = [i for i in (getattr(opp, 'alive_sprites', None) or [])
             if i != getattr(opp, 'active_index', -1)]
    if not bench:
        return False
    active = getattr(opp, 'active', None)
    if active is None or getattr(active, 'current_hp', 0) <= 0:
        return False
    if my_best_dmg >= active.current_hp:
        return True
    return predict_turn_end_damage(battle, active, opponent_team(my_team)) >= active.current_hp
