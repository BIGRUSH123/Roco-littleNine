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
  SkillResolver — 技能效果解析器（类型化分派 + 伤害）
  TurnContext  — 回合快照（战场事实）
  TurnRecord  — 单回合记录
  Battle      — 对局
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
from .action import Action
from .player import Item, PlayStyle, Player
from .globals import Mark, GlobalEffects
from .resolver import SkillResolver, TurnContext
from .battle import TurnRecord, Battle
from .agent import Agent, RuleAgent, HumanAgent
from .factory import SimFactory
