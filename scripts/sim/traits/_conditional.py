"""scripts/sim/traits/_conditional.py — Conditional 级特性（条件判断 / 多步效果）

每个特性 5-20 行，基于 if/else 条件判断。
"""

from . import register, TraitHandler
from scripts.sim.sprite import StatusEffect, Sprite
from scripts.sim.battle import Battle
from scripts.sim.battleskill import BattleSkill, SkillUse
from scripts.sim.globals import GlobalEffects


# ═══════════════════════════════════════════════════════════════
# on_modifier — 修改技能参数（条件判断）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_entry — 入场触发（条件判断）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_skill_use — 使用技能后触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_counter_success — 应对成功后触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_energy_change — 能量变化时触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_take_damage — 受到攻击后触发
# ═══════════════════════════════════════════════════════════════

@register("嫁祸")
class Scapegoat(TraitHandler):
    """每失去25%生命，连击数+2。"""

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        sprite.counters['scapegoat_quarters'] = 0
        return []

    def on_take_damage(self, target: Sprite, attacker: Sprite, damage: int,
                       battle: Battle, team: str) -> list[str]:
        hp_pct = target.current_hp / target.max_hp
        lost_quarters = int((1.0 - hp_pct) / 0.25)
        prev = target.counters.get('scapegoat_quarters', 0)
        if lost_quarters > prev:
            gained = lost_quarters - prev
            target.counters['scapegoat_quarters'] = lost_quarters
            target.add_effect(StatusEffect(
                name='嫁祸连击', category='stat', stat_key='combo',
                steps=gained * 2, scope='battlefield', source='嫁祸'))
            return [f'{target.name} 嫁祸: 连击+{gained*2}']
        return []


# ═══════════════════════════════════════════════════════════════
# on_inflict — 对敌方施加效果时触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_turn_end — 回合结束时触发（条件判断）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_ko_enemy — 击败敌方时触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_faint — 自身力竭时触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_gain_effect — 获得效果时触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_fatal_damage — 致命伤害前触发
# ═══════════════════════════════════════════════════════════════

@register("惊吓")
class Fright(TraitHandler):
    """能量=0的精灵无法对自己造成伤害。"""

    def on_fatal_damage(self, sprite: Sprite, damage: int,
                        battle: Battle, team: str) -> bool:
        opp_team = 'B' if team == 'A' else 'A'
        attacker = battle.get_opponent(team).active
        return attacker.energy == 0


@register("逐魂鸟")
class SoulChaser(TraitHandler):
    """能耗<=1的攻击技能无法对自己造成伤害。"""

    def on_fatal_damage(self, sprite: Sprite, damage: int,
                        battle: Battle, team: str) -> bool:
        # We don't have access to the attacking skill here, so this is approximate
        return False  # needs L2-level access; handled via _execute_single_action
