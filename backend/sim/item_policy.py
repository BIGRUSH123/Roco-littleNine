"""backend/sim/item_policy.py — 队级道具的使用判据（RuleAgent / RuleAgentV2 共用）。

为什么单独一个模块：这两条规则过去在 `agent.py` 与 `agent_v2.py` 里各写了一份
（"进化之力只在 turn <= 2"、"愿力残血就用"），口径改了容易只改一半。

**道具不消耗回合**（`battle.py::_select_action` 是道具循环：结算道具后重新让
agent 选行动），所以「先道具、再出招」在同一回合内完成——用道具的唯一门槛是值不值，
而不是抢不抢回合。

规则（2026-09-20 定稿，见 `docs/培养方案-pvp口径.md` §1.1）：

1. **进化之力**（首领血脉 → 首领化）：只要当前场上精灵是首领血脉、引擎判定可用，
   就立刻首领化。首领化没有代价——技能槽不变、六维按首领形态种族值 + 原 IV/性格
   重算、不消耗回合；旧规则额外要求 `turn <= 2`，实测让 189 侧首领队里的 132 侧
   整局变不了身（首发不是首领、或首领第 3 回合才上场就永远用不上）。
2. **愿力**（元素血脉 → 把技能槽 0 换成该血脉的血脉技能，持续本回合）：判据是
   **换出的技能比本回合最强可负担攻击更疼、或能直接斩杀**。血脉属性克制对手时
   它自然胜出（伤害里已含属性/印记/天气倍率）；没有可用攻击时，攻击型血脉技能
   也算净收益。血脉技能是状态/防御技时伤害为 0 → 不触发（那种血脉的愿力确实没用）。

判据全部走引擎自己的 `battle._resolver.calc_damage` 与 `battle.item_usable`，
不另立一套伤害/克制表。
"""
from __future__ import annotations

from .battleskill import SkillUse


def item_usable(battle, team: str) -> bool:
    """引擎判定的「当前场上精灵能否用这个道具」（血脉条件 + 次数 + 冷却）。

    轻量假 battle（测试替身、旧调用点）没有该方法时返回 True，由各规则自己兜底。
    """
    try:
        return bool(battle.item_usable(team))
    except AttributeError:
        return True


def bloodline_skill(battle, sprite):
    """愿力会换出的血脉技能（BattleSkill）；不可用时返回 None。"""
    bloodline = getattr(sprite, 'bloodline', '')
    if not bloodline or bloodline == '首领':
        return None
    skill_id = (getattr(sprite, 'bloodline_skills', None) or {}).get(bloodline)
    loader = getattr(battle, 'skill_loader', None)
    if skill_id is None or loader is None:
        return None
    try:
        from backend.common.skill_trait_ids import SKILL_ID_TO_NAME

        name = SKILL_ID_TO_NAME.get(int(skill_id))
    except (TypeError, ValueError):
        return None
    if not name:
        return None
    skills = loader([name]) or []
    return skills[0] if skills else None


def estimate_damage(battle, attacker, defender, skill, team: str) -> int:
    """单发伤害估算（含本系/克制/印记/天气/当前强化等级）。"""
    dmg, _events = battle._resolver.calc_damage(
        attacker, defender, SkillUse(battle_skill=skill), battle.globals,
        attacker_team=team,
    )
    return dmg


def best_attack_damage(battle, sprite, opp, team: str) -> int:
    """本回合最强**可负担**攻击的伤害（冷却/封印中、能量不够的不算）。"""
    best = 0
    for skill in getattr(sprite, 'skills', ()) or ():
        if not skill.is_attack or skill.cooldown > 0 or skill.sealed:
            continue
        if skill.energy_cost > sprite.energy:
            continue
        best = max(best, estimate_damage(battle, sprite, opp, skill, team))
    return best


def should_evolve(battle, team: str, sprite) -> bool:
    """进化之力：当前场上精灵是首领血脉且引擎判定可用 → 立刻首领化。"""
    return (getattr(sprite, 'bloodline', '') == '首领'
            and item_usable(battle, team))


def wish_decision(battle, team: str, sprite, opp,
                  best_attack_dmg: int | None = None) -> str:
    """愿力的用法判定：'kill'（换出的技能能斩杀）/ 'better'（比最强攻击更疼）/ ''（不用）。

    换出的血脉技能只持续本回合，所以判据就是"这个替代品本回合的伤害"：
    比最强可负担攻击更疼或能斩杀 → 值得用；血脉属性克制对手时它自然胜出。
    """
    if not item_usable(battle, team) or opp is None:
        return ''
    wish = bloodline_skill(battle, sprite)
    # 换出的技能本回合要能放出来（能量不够就白换）
    if wish is None or not wish.is_attack or wish.energy_cost > sprite.energy:
        return ''
    wish_dmg = estimate_damage(battle, sprite, opp, wish, team)
    if getattr(opp, 'current_hp', 0) > 0 and wish_dmg >= opp.current_hp:
        return 'kill'
    if best_attack_dmg is None:
        best_attack_dmg = best_attack_damage(battle, sprite, opp, team)
    return 'better' if wish_dmg > best_attack_dmg else ''


def should_use_wish(battle, team: str, sprite, opp,
                    best_attack_dmg: int | None = None) -> bool:
    """愿力是否值得用（`wish_decision` 的布尔封装）。"""
    return wish_decision(battle, team, sprite, opp, best_attack_dmg) != ''
