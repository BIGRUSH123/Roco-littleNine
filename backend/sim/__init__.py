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
  RoundRecord  — 单回合结构化记录
  ActionRecord — 单个行动记录
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

# 加载数据驱动特性（JSON → DataDrivenTrait），优先于 Python 类
import os

from backend.engine import hooks as _engine_hooks  # noqa: E402, F401  Phase C4: engine-level hooks

from .action import Action
from .agent import Agent, HumanAgent, RuleAgent
from .battle import Battle
from .battle_context import BattleContext
from .battle_mechanics import BattleMechanicsMixin
from .battleskill import BattleSkill
from .factory import SimFactory
from .globals import GlobalEffects
from .pipeline import TurnPipeline
from .player import Item, Player, PlayStyle
from .resolver import SkillResolver
from .round_record import ActionRecord, RoundRecord
from .skill import Skill
from .sprite import Sprite

# 导入 trait 子模块以触发 @register 装饰器（放在最后，避免循环导入）
from .traits import _complex  # noqa: E402, F401
from .traits.trait_engine import register_data_traits

_data_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'traits')
_data_dir = os.path.normpath(_data_dir)
_loaded = register_data_traits(_data_dir)
if _loaded:
    import logging
    logging.getLogger(__name__).debug(f'加载 {_loaded} 个数据驱动特性')
