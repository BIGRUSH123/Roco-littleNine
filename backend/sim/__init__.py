"""
scripts/sim/ — 格斗小九 PVP 对局模拟器

类：
  Sprite      — 战斗精灵实例
  Skill       — 技能（自包含，从 JSON 反序列化）
  Action      — 回合行动
  PlayStyle   — 操作习惯画像
  Player      — 玩家
  Mark        — 单方印记
  GlobalEffects — 全局效果（天气 + 双方印记）
  SkillResolver — 技能效果解析器（应对判断 + 伤害 + 回合末结算）
  TurnRecord  — 单回合记录
  Battle      — 对局
  TurnPipeline  — 回合开始阶段管线（传动/位置预扫描/trait）
  BattleContext — Battle 最小接口协议
  Agent       — 决策代理协议
  RuleAgent   — 基于 PlayStyle 的规则 AI
  HumanAgent  — 终端人机交互
  SimFactory  — 从 JSON 数据构建模拟对象

Effect 类型 (effects.py):
  StatEffect / AbnormalEffect / MarkEffect / WeatherEffect
  / SpecialEffect / ConditionalEffect
"""

from .sprite import Sprite
from .skill import Skill
from .battleskill import BattleSkill
from .action import Action
from .player import Item, PlayStyle, Player
from .globals import Mark, GlobalEffects
from .resolver import SkillResolver
from .battle_mechanics import BattleMechanicsMixin
from .battle_context import BattleContext
from .battle import TurnRecord, Battle
from .pipeline import TurnPipeline
from .agent import Agent, RuleAgent, HumanAgent
from .factory import SimFactory

# 导入 trait 子模块以触发 @register 装饰器（放在最后，避免循环导入）
from .traits import _complex  # noqa: E402, F401

# 加载数据驱动特性（JSON → DataDrivenTrait），优先于 Python 类
import os
from .traits.trait_engine import register_data_traits
_data_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'traits')
_data_dir = os.path.normpath(_data_dir)
_loaded = register_data_traits(_data_dir)
if _loaded:
    import logging
    logging.getLogger(__name__).debug(f'加载 {_loaded} 个数据驱动特性')
