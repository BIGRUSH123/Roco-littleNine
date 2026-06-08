"""backend/sim/traits/trait_engine.py — 通用特性引擎

DataDrivenTrait: 按名称/ID 查找的轻量特性引用。实际 Observer 编译和注册由
TraitLoader（engine/trait_loader.py）负责。

Engine hook 系统 (Layer 3b): register_hook / fire_hook / fire_hook_first。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import TraitHandler

# ═══════════════════════════════════════════════════════════════════
# Hook 注册机制 (Layer 3b)
# ═══════════════════════════════════════════════════════════════════

_HOOK_REGISTRY: dict[str, list] = {}
"""hook_name → [(callback, trait_name), ...]"""


def register_hook(hook_name: str, callback, trait_name: str = '') -> None:
    """注册一个引擎级 hook 回调。

    支持的 hook 名:
      before_apply_mark, max_energy_override, before_consume_starfall,
      turn_end_bench_check, after_transmission, after_gain_energy,
      after_take_damage, on_energy_short, on_fatal_damage
    """
    _HOOK_REGISTRY.setdefault(hook_name, []).append((callback, trait_name))



def fire_hook(hook_name: str, *args, **kwargs):
    """触发 hook，合并所有 list 结果。非 list 结果返回第一个非 None。"""
    callbacks = _HOOK_REGISTRY.get(hook_name, [])
    results = []
    for cb, _tn in callbacks:
        result = cb(*args, **kwargs)
        if result is not None:
            results.append(result)
    if not results:
        return None
    if all(isinstance(r, list) for r in results):
        merged: list[str] = []
        for r in results:
            merged.extend(r)
        return merged
    return results[0]


def fire_hook_first(hook_name: str, *args, **kwargs):
    """触发 hook，返回第一个非 None 结果。"""
    callbacks = _HOOK_REGISTRY.get(hook_name, [])
    for cb, _tn in callbacks:
        result = cb(*args, **kwargs)
        if result is not None:
            return result
    return None


# ═══════════════════════════════════════════════════════════════════
# DataDrivenTrait — 轻量特性引用
# ═══════════════════════════════════════════════════════════════════

class DataDrivenTrait(TraitHandler):
    """轻量特性引用。Observer 编译和注册由 TraitLoader 负责。"""

    def __init__(self, name: str, trait_id: int = 0):
        self.name = name
        self.trait_id = trait_id

    def on_turn_start(self, sprite, battle, team):
        """Fire turn_start observers for per-turn position-based effects."""
        events: list[str] = []
        vm_engine = getattr(battle, '_vm_engine', None)
        if vm_engine is None:
            return events
        if not vm_engine.registry.has_candidates("turn_start", id(sprite)):
            return events
        opp_team = 'B' if team == 'A' else 'A'
        opp = battle.get_player(opp_team).active
        ctx = battle._make_ctx(sprite, opp, None, None, battle.globals,
                               team=team, turn=battle.turn)
        events += vm_engine.fire_trigger(
            "turn_start", ctx, sprite, opp, battle.globals,
            team=team, battle=battle,
        )
        return events


# ═══════════════════════════════════════════════════════════════════
# JSON 加载 & 注册
# ═══════════════════════════════════════════════════════════════════


def load_data_trait(filepath: str) -> DataDrivenTrait | None:
    """从 JSON 文件加载特性名称和 ID。"""
    try:
        with open(filepath, encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    name = data.get('name', '')
    trait_id = data.get('id', 0)

    if not name or not trait_id:
        return None

    return DataDrivenTrait(name, trait_id=trait_id)


def register_data_traits(data_dir: str) -> int:
    """扫描 data_dir 下所有 .json 文件，加载并缓存到 _DATA_TRAIT_INSTANCES。

    数据驱动特性通过 get_data_trait_instance() 查找，优先于 TRAIT_REGISTRY。
    返回成功加载的数量。
    """
    count = 0
    root = Path(data_dir)
    if not root.is_dir():
        return 0

    for fpath in sorted(root.glob('*.json')):
        trait = load_data_trait(str(fpath))
        if trait is not None:
            _DATA_TRAIT_INSTANCES[trait.name] = trait
            if trait.trait_id:
                _DATA_TRAIT_INSTANCES_BY_ID[trait.trait_id] = trait
            count += 1

    return count


# 数据驱动特性实例缓存
_DATA_TRAIT_INSTANCES: dict[str, DataDrivenTrait] = {}
_DATA_TRAIT_INSTANCES_BY_ID: dict[int, DataDrivenTrait] = {}


def get_data_trait_instance(name_or_id) -> DataDrivenTrait | None:
    """获取数据驱动特性的预构造实例。支持名称(str)或ID(int)查找。"""
    if isinstance(name_or_id, int):
        return _DATA_TRAIT_INSTANCES_BY_ID.get(name_or_id)
    return _DATA_TRAIT_INSTANCES.get(name_or_id)
