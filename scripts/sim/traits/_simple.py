"""scripts/sim/traits/_simple.py — Simple 级特性（无条件 / 固定效果）

每个特性 3-8 行，纯数据驱动。
"""

from . import register, TraitHandler
from scripts.sim.sprite import StatusEffect, Sprite
from scripts.sim.battle import Battle
from scripts.sim.battleskill import BattleSkill, SkillUse
from scripts.sim.globals import GlobalEffects


# ═══════════════════════════════════════════════════════════════
# on_entry — 入场触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_leave — 离场触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_turn_end — 回合末触发
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# on_modifier — 修改技能参数
# ═══════════════════════════════════════════════════════════════
