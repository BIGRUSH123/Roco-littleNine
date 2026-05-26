"""backend/sim/traits/__init__.py — 特性系统框架

TraitHandler 基类 + 注册表 + dispatch 函数。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.sim.battle import Battle
    from backend.sim.battleskill import BattleSkill, SkillUse
    from backend.sim.skill import Skill
    from backend.sim.sprite import Sprite


# ═══════════════════════════════════════════════════════════════════
# TraitHandler 基类 — 17 个钩子，默认返回空
# ═══════════════════════════════════════════════════════════════════

class TraitHandler:
    name: str = ""
    trait_id: int = 0

    # ── 入场 / 离场 ──

    def on_entry(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return []

    def on_leave(self, sprite: Sprite, battle: Battle, team: str,
                 is_faint: bool = False) -> list[str]:
        return []

    # ── 回合边界 ──

    def on_turn_start(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return []

    def on_turn_end(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return []

    # ── 技能管线 ──

    def on_energy_short(self, sprite: Sprite, cost: int,
                        battle: Battle, team: str) -> int:
        """能量不足时触发。返回可替代的 HP 消耗量（0=不替代）。"""
        return 0

    def on_modifier(self, user: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
        """L0→L1：修改技能参数。"""
        return []

    def on_damage(self, user: Sprite, target: Sprite, use: SkillUse,
                  battle: Battle, team: str) -> list[str]:
        """L1→L2：影响伤害计算。"""
        return []

    def on_defend(self, target: Sprite, attacker: Sprite, use: SkillUse,
                  battle: Battle, team: str) -> list[str]:
        """L1→L2：防御方特性修改 incoming 伤害（偏振/绝对秩序等）。"""
        return []

    def on_skill_use(self, user: Sprite, skill: BattleSkill,
                     battle: Battle, team: str) -> list[str]:
        """技能执行完毕后触发。"""
        return []

    def on_take_damage(self, target: Sprite, attacker: Sprite, damage: int,
                       battle: Battle, team: str) -> list[str]:
        """受到攻击伤害后触发。"""
        return []

    def on_fatal_damage(self, sprite: Sprite, damage: int,
                        battle: Battle, team: str) -> bool:
        """受到致命伤害前触发。返回 True 免疫此次伤害。"""
        return False

    def on_ko_enemy(self, user: Sprite, victim: Sprite,
                    battle: Battle, team: str) -> list[str]:
        """主动击败敌方精灵后触发。"""
        return []

    def on_counter_success(self, user: Sprite, countered_skill: BattleSkill,
                           battle: Battle, team: str) -> list[str]:
        """应对/打断成功后触发。"""
        return []

    def on_faint(self, sprite: Sprite, killer: Sprite | None,
                 battle: Battle, team: str) -> list[str]:
        """自身力竭时触发。"""
        return []

    # ── 能量 / 效果事件 ──

    def on_energy_change(self, sprite: Sprite, delta: int, new_energy: int,
                         battle: Battle, team: str) -> list[str]:
        """能量增减后触发。"""
        return []

    def on_gain_effect(self, sprite: Sprite, effect,
                       battle: Battle, team: str) -> list[str]:
        """获得增益/减益/异常时触发。"""
        return []

    def on_inflict(self, user: Sprite, target: Sprite, effect_name: str,
                   battle: Battle, team: str) -> list[str]:
        """对敌方施加效果时触发。"""
        return []

    def on_enemy_leave(self, sprite: Sprite, enemy_old: Sprite,
                        enemy_new: Sprite, battle: Battle, team: str) -> list[str]:
        """敌方精灵离场时触发（做噩梦/下黑手/珊瑚骨）。"""
        return []

    def on_abnormal_tick(self, sprite: Sprite, effect_name: str, damage: int,
                         battle: Battle, team: str) -> list[str]:
        """异常效果回合末扣血时触发（仁心/耐活王/煤渣草）。"""
        return []

    # ── 伤害拦截 / 行动前 ──

    def on_before_take_damage(self, target: Sprite, attacker: Sprite,
                               damage: int, element: str,
                               battle: Battle, team: str) -> int | None:
        """受到伤害前拦截。返回 None=不修改, 0=免疫, <0=吸收(回血), >0=修正伤害。"""
        return None

    def on_before_action(self, sprite: Sprite, action,
                          battle: Battle, team: str):
        """行动选择后修改/否决。返回 None=不修改, 否则返回替换的 action。"""
        return None


# ═══════════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════════

TRAIT_REGISTRY: dict[str, type[TraitHandler]] = {}


def register(name: str):
    """装饰器：将特性类注册到 TRAIT_REGISTRY，并自动设置 trait_id。"""
    def decorator(cls: type[TraitHandler]) -> type[TraitHandler]:
        cls.name = name
        try:
            from backend.common.skill_trait_ids import TRAIT_NAME_TO_ID
            cls.trait_id = TRAIT_NAME_TO_ID.get(name, 0)
        except ImportError:
            pass
        TRAIT_REGISTRY[name] = cls
        return cls
    return decorator


def get_trait(sprite: Sprite) -> TraitHandler | None:
    """获取精灵的特性处理器（惰性缓存于 sprite._trait_handler）。

    优先级：数据驱动实例 > 注册的 Python 类。
    支持 ability_id 或 ability name 查找。
    若 sprite._trait_suppressed 为 True，跳过所有特性 dispatch。
    """
    if getattr(sprite, '_trait_suppressed', False):
        return None

    ability = sprite.species.ability
    ability_id = getattr(sprite.species, 'ability_id', 0)
    if not ability and not ability_id:
        return None

    cached = getattr(sprite, '_trait_handler', None)
    if cached is not None:
        return cached

    from . import trait_engine

    # 1. 优先按 ID 查找数据驱动实例
    if ability_id:
        instance = trait_engine.get_data_trait_instance(ability_id)
        if instance is not None:
            sprite._trait_handler = instance
            return instance

    # 2. 按名称查找数据驱动实例
    if ability:
        instance = trait_engine.get_data_trait_instance(ability)
        if instance is not None:
            sprite._trait_handler = instance
            return instance

    # 3. 回退到注册的 Python 类
    if not ability or ability not in TRAIT_REGISTRY:
        return None

    cls = TRAIT_REGISTRY[ability]
    instance = cls()
    sprite._trait_handler = instance
    return instance


# ═══════════════════════════════════════════════════════════════════
# dispatch 函数（供 battle / sprite 调用）
# ═══════════════════════════════════════════════════════════════════

def dispatch_entry(sprite: Sprite, battle: Battle, team: str) -> list[str]:
    # 应用 pending effects（美拉德反应/吉利丁片等离场 buff）
    pending = battle.pending_effects.get(team, [])
    for e in pending:
        sprite.add_effect(e)
    if pending:
        battle.pending_effects[team] = []

    events: list[str] = []

    # 首次入场时设置 entry_turn（初始精灵 entry_turn 默认为 0）
    if sprite.entry_turn == 0:
        sprite.entry_turn = battle.turn

    # IR_RISC trait pipeline: load trait JSON → compile observers → register
    try:
        battle._vm_engine.trait_loader.load_for_sprite(sprite)
    except Exception:
        pass  # Observer registration failure must not crash battle

    # Engine hooks: post_entry (skill sealing, etc.)
    from .trait_engine import fire_hook
    hook_events = fire_hook('post_entry', sprite, battle, team)
    if hook_events:
        events.extend(hook_events)

    # Legacy TraitHandler for backward compat
    h = get_trait(sprite)
    if h is not None and hasattr(h, 'on_entry'):
        try:
            events += h.on_entry(sprite, battle, team)
        except Exception:
            pass

    if pending:
        events.append(f'{sprite.name} 继承{len(pending)}个离场效果')
    return events


def dispatch_leave(sprite: Sprite, battle: Battle, team: str,
                   is_faint: bool = False) -> list[str]:
    events: list[str] = []

    # 清除 battlefield + turn scope 的 modifier 和效果（离场时临时效果不应保留）
    sprite.clear_effects('battlefield')
    sprite.clear_effects('turn')

    # IR_RISC trait pipeline: unregister observers by scope
    reason = "faint" if is_faint else "leave"
    try:
        battle._vm_engine.trait_loader.unload_for_sprite(sprite, reason)
    except Exception:
        pass

    # Legacy TraitHandler for backward compat
    h = get_trait(sprite)
    if h is not None and hasattr(h, 'on_leave'):
        try:
            events += h.on_leave(sprite, battle, team, is_faint)
        except Exception:
            pass

    # 清除缓存，下次入场重新创建
    sprite._trait_handler = None
    return events


def dispatch_turn_start(sprite: Sprite, battle: Battle, team: str) -> list[str]:
    h = get_trait(sprite)
    return h.on_turn_start(sprite, battle, team) if h else []


def dispatch_turn_end(sprite: Sprite, battle: Battle, team: str) -> list[str]:
    h = get_trait(sprite)
    return h.on_turn_end(sprite, battle, team) if h else []


def dispatch_energy_short(sprite: Sprite, cost: int,
                          battle: Battle, team: str) -> int:
    h = get_trait(sprite)
    return h.on_energy_short(sprite, cost, battle, team) if h else 0


def dispatch_modifier(user: Sprite, use: SkillUse,
                      battle: Battle, team: str) -> list[str]:
    from .trait_engine import fire_hook

    events: list[str] = []
    h = get_trait(user)
    if h:
        events += h.on_modifier(user, use, battle, team)

    hook_events = fire_hook('pre_modifier', user, use, battle, team) or []
    if hook_events:
        events.extend(hook_events)
    return events


def dispatch_damage(user: Sprite, target: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
    h = get_trait(user)
    return h.on_damage(user, target, use, battle, team) if h else []


def dispatch_defend(target: Sprite, attacker: Sprite, use: SkillUse,
                    battle: Battle, team: str) -> list[str]:
    """防御方特性修改 incoming 伤害（偏振/绝对秩序 等）。"""
    h = get_trait(target)
    return h.on_defend(target, attacker, use, battle, team) if h else []


def dispatch_skill_use(user: Sprite, skill: BattleSkill,
                       battle: Battle, team: str) -> list[str]:
    h = get_trait(user)
    return h.on_skill_use(user, skill, battle, team) if h else []


def dispatch_take_damage(target: Sprite, attacker: Sprite, damage: int,
                         battle: Battle, team: str) -> list[str]:
    from .trait_engine import fire_hook

    events: list[str] = []
    h = get_trait(target)
    if h:
        events += h.on_take_damage(target, attacker, damage, battle, team)

    hook_events = fire_hook('post_take_damage', target, attacker, damage, battle, team) or []
    if hook_events:
        events.extend(hook_events)
    return events


def dispatch_fatal_damage(sprite: Sprite, damage: int,
                          battle: Battle, team: str) -> bool:
    h = get_trait(sprite)
    return h.on_fatal_damage(sprite, damage, battle, team) if h else False


def dispatch_ko_enemy(user: Sprite, victim: Sprite,
                      battle: Battle, team: str) -> list[str]:
    h = get_trait(user)
    events = h.on_ko_enemy(user, victim, battle, team) if h else []
    # 同时触发受害者的力竭钩子
    victim_team = _opposite_team(team)
    events += dispatch_faint(victim, user, battle, victim_team)
    return events


def dispatch_counter_success(user: Sprite, countered_skill: BattleSkill,
                             battle: Battle, team: str) -> list[str]:
    from .trait_engine import fire_hook

    events: list[str] = []
    h = get_trait(user)
    if h:
        events += h.on_counter_success(user, countered_skill, battle, team)

    hook_events = fire_hook('post_counter', user, countered_skill, battle, team) or []
    if hook_events:
        events.extend(hook_events)
    return events


def dispatch_faint(sprite: Sprite, killer: Sprite | None,
                   battle: Battle, team: str) -> list[str]:
    if getattr(sprite, '_faint_dispatched', False):
        return []
    sprite._faint_dispatched = True
    h = get_trait(sprite)
    events = h.on_faint(sprite, killer, battle, team) if h else []
    sprite._trait_handler = None
    return events


def dispatch_energy_change(sprite: Sprite, delta: int, new_energy: int,
                           battle: Battle, team: str) -> list[str]:
    from .trait_engine import fire_hook

    events: list[str] = []
    h = get_trait(sprite)
    if h:
        events += h.on_energy_change(sprite, delta, new_energy, battle, team)

    hook_events = fire_hook('post_energy_change', sprite, delta, new_energy, battle, team) or []
    if hook_events:
        events.extend(hook_events)
    return events


def dispatch_gain_effect(sprite: Sprite, effect,
                         battle: Battle, team: str) -> list[str]:
    h = get_trait(sprite)
    return h.on_gain_effect(sprite, effect, battle, team) if h else []


def dispatch_inflict(user: Sprite, target: Sprite, effect_name: str,
                     battle: Battle, team: str) -> list[str]:
    h = get_trait(user)
    return h.on_inflict(user, target, effect_name, battle, team) if h else []


def dispatch_enemy_leave(observer: Sprite, enemy_old: Sprite, enemy_new: Sprite,
                          battle: Battle, team: str) -> list[str]:
    """敌方精灵离场时，通知己方 observer（做噩梦/下黑手/珊瑚骨）。"""
    h = get_trait(observer)
    return h.on_enemy_leave(observer, enemy_old, enemy_new, battle, team) if h else []


def dispatch_abnormal_tick(sprite: Sprite, effect_name: str, damage: int,
                            battle: Battle, team: str) -> list[str]:
    """异常效果回合末扣血时触发（仁心/耐活王/煤渣草）。"""
    h = get_trait(sprite)
    return h.on_abnormal_tick(sprite, effect_name, damage, battle, team) if h else []


def dispatch_before_take_damage(target: Sprite, attacker: Sprite,
                                 damage: int, element: str,
                                 battle: Battle, team: str) -> int | None:
    """受到伤害前拦截。返回 None=不修改, 0=免疫, <0=吸收, >0=修正伤害。"""
    h = get_trait(target)
    if h:
        result = h.on_before_take_damage(target, attacker, damage, element, battle, team)
        if result is not None:
            return result
    return None


def dispatch_before_action(sprite: Sprite, action,
                            battle: Battle, team: str):
    """行动选择后修改/否决。返回 None=不修改, 否则返回替换的 action。"""
    if sprite.is_fainted:
        return None
    h = get_trait(sprite)
    if h:
        result = h.on_before_action(sprite, action, battle, team)
        if result is not None:
            return result
    return None


def _opposite_team(team: str) -> str:
    return 'B' if team == 'A' else 'A'
