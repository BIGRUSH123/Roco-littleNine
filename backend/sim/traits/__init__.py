"""backend/sim/traits/__init__.py — 特性系统框架

TraitHandler 基类 + DataDrivenTrait 查找 + 3 个保留 dispatch 函数。
旧 dispatch 函数（on_modifier、on_damage 等）已移除——效果通过 ObserverRegistry 触发。
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.sim.battle import Battle
    from backend.sim.sprite import Sprite


# ═══════════════════════════════════════════════════════════════════
# TraitHandler 基类 — 仅 name + trait_id，DataDrivenTrait 继承此
# ═══════════════════════════════════════════════════════════════════

class TraitHandler:
    name: str = ""
    trait_id: int = 0

    def on_turn_start(self, sprite: Sprite, battle: Battle, team: str) -> list[str]:
        return []


# ═══════════════════════════════════════════════════════════════════
# 查找
# ═══════════════════════════════════════════════════════════════════

def get_trait(sprite: Sprite) -> TraitHandler | None:
    """获取精灵的特性处理器。

    仅查找 DataDrivenTrait（effects 格式）。旧 Python 特性类已全部移除。
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

    # 按 ID 查找数据驱动实例
    if ability_id:
        instance = trait_engine.get_data_trait_instance(ability_id)
        if instance is not None:
            sprite._trait_handler = instance
            return instance

    # 按名称查找数据驱动实例
    if ability:
        instance = trait_engine.get_data_trait_instance(ability)
        if instance is not None:
            sprite._trait_handler = instance
            return instance

    return None


# ═══════════════════════════════════════════════════════════════════
# 保留的 dispatch 函数
# ═══════════════════════════════════════════════════════════════════

def dispatch_entry(sprite: Sprite, battle: Battle, team: str) -> list[str]:
    """入场：加载特性 Observer + 触发 post_entry。"""
    # 应用 pending effects（美拉德反应/吉利丁片等离场 buff）
    pending = battle.pending_effects.get(team, [])
    for e in pending:
        sprite.add_effect(e)
    if pending:
        battle.pending_effects[team] = []

    events: list[str] = []

    if sprite.entry_turn == 0:
        sprite.entry_turn = battle.turn

    # IR_GUIDE trait pipeline: load trait JSON → compile observers → register
    with contextlib.suppress(Exception):
        battle._vm_engine.trait_loader.load_for_sprite(sprite)

    # Fire post_energy_change for initial energy state (traits like 囤积)
    try:
        if battle._vm_engine.registry.has_candidates("post_energy_change"):
            opp_team = 'B' if team == 'A' else 'A'
            opp = battle.get_player(opp_team).active
            init_ctx = battle._make_ctx(
                sprite, opp, None, None, battle.globals,
                team=team, turn=battle.turn,
                energy_changed_of="sprite_self",
            )
            battle._vm_engine.fire_trigger(
                "post_energy_change", init_ctx, sprite, opp, battle.globals,
                team=team, battle=battle,
            )
    except Exception:
        pass

    # Engine hooks: post_entry
    from .trait_engine import fire_hook
    hook_events = fire_hook('post_entry', sprite, battle, team)
    if hook_events:
        events.extend(hook_events)

    if pending:
        events.append(f'{sprite.name} 继承{len(pending)}个离场效果')
    return events


def dispatch_leave(sprite: Sprite, battle: Battle, team: str,
                   is_faint: bool = False) -> list[str]:
    """离场：卸载 Observer + 清理效果。"""
    events: list[str] = []

    sprite.clear_effects('battlefield')
    sprite.clear_effects('turn')

    reason = "faint" if is_faint else "leave"
    with contextlib.suppress(Exception):
        battle._vm_engine.trait_loader.unload_for_sprite(sprite, reason)

    sprite._trait_handler = None
    return events


def dispatch_turn_start(sprite: Sprite, battle: Battle, team: str) -> list[str]:
    """回合开始：触发位置型 turn_start 观察者。"""
    h = get_trait(sprite)
    return h.on_turn_start(sprite, battle, team) if h else []


