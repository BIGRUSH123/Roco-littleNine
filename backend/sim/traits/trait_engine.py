"""backend/sim/traits/trait_engine.py — 通用特性引擎

DataDrivenTrait: 从 JSON effects 列表加载特性定义。
特性效果通过 Skill VM 执行：CountOp → CounterRegister → Observer（带 auto-inferred listen triggers）。
其他 opcode 效果直接通过 VM 产生 mutation，由引擎 replay。

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


def unregister_hook(hook_name: str, trait_name: str = '') -> None:
    """移除指定 trait 注册的所有 hook。"""
    if hook_name not in _HOOK_REGISTRY:
        return
    if not trait_name:
        _HOOK_REGISTRY.pop(hook_name, None)
    else:
        _HOOK_REGISTRY[hook_name] = [
            (cb, tn) for cb, tn in _HOOK_REGISTRY[hook_name]
            if tn != trait_name
        ]


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
# DataDrivenTrait — 纯 Skill IR effects 格式
# ═══════════════════════════════════════════════════════════════════

class DataDrivenTrait(TraitHandler):
    """从 JSON effects 列表构造的特性处理器。

    特性效果通过 Skill VM 执行：
      - CountOp → CounterRegister mutation → Observer（带 auto-inferred listen triggers）
      - 其他 opcode → 直接 mutation 由引擎 replay
    """

    def __init__(self, name: str, trait_id: int = 0, effects: list[dict] = None):
        self.name = name
        self.trait_id = trait_id
        self._effects: list[dict] = effects or []
        self._observers: list = None

    def to_observers(self) -> list:
        """Process effects through VM → extract CounterRegister → Observers.

        Result is cached after first call.
        """
        if self._observers is not None:
            return self._observers
        self._observers = self._effects_to_observers()
        return self._observers

    def _effects_to_observers(self) -> list:
        """Process effects through Skill VM, convert CounterRegister → Observer."""
        from backend.engine.observer import Observer
        from backend.vm.cond import infer_triggers
        from backend.vm.ctx import Ctx
        from backend.vm.executor import process_effects
        from backend.vm.journal import CounterRegister

        ctx = Ctx()
        effects = _inject_source_json(self._effects, self.name)
        journal = process_effects(ctx, effects)

        observers = []
        for m in journal:
            if isinstance(m, CounterRegister):
                observers.append(Observer(
                    cond=m.cond,
                    then=m.then,
                    scope=m.scope,
                    name=m.name or self.name,
                    source=self.name,
                    listen=m.listen if m.listen is not None else infer_triggers(m.cond),
                ))
        return observers

    def on_turn_start(self, sprite, battle, team):
        """Fire turn_start observers for per-turn position-based effects."""
        events: list[str] = []
        vm_engine = getattr(battle, '_vm_engine', None)
        if vm_engine is None:
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


def _inject_source_json(effects: list[dict], source: str) -> list[dict]:
    """Inject source into raw JSON effects recursively before compilation.

    Observer then-effects are compiled to frozen IR dataclasses by process_effects,
    so source must be injected at the JSON level BEFORE compilation.
    """
    import copy
    result = []
    for eff in effects:
        eff = copy.copy(eff)
        if "op" in eff and "source" not in eff:
            eff["source"] = source
        if isinstance(eff.get("then"), list):
            eff["then"] = _inject_source_json(eff["then"], source)
        if isinstance(eff.get("else"), list):
            eff["else"] = _inject_source_json(eff["else"], source)
        result.append(eff)
    return result


# ═══════════════════════════════════════════════════════════════════
# JSON 加载 & 注册
# ═══════════════════════════════════════════════════════════════════


def load_data_trait(filepath: str) -> DataDrivenTrait | None:
    """从 JSON 文件加载一个数据驱动特性。

    格式: {"effects": [SkillIROp, ...], "name": "...", "id": ...}
    """
    try:
        with open(filepath, encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    name = data.get('name', '')
    trait_id = data.get('id', 0)
    effects = data.get('effects', [])

    if not name:
        return None
    # traits with engine-level behavior (e.g. 无忧无虑 checked in apply_moe)
    # may have no effects — still register them so get_trait() works
    if not effects and not trait_id:
        return None

    return DataDrivenTrait(name, trait_id=trait_id, effects=effects)


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
