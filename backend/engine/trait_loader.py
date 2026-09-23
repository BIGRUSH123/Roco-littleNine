"""TraitLoader — load trait JSON and register Observers with battle engine.

The entry point for the IR_GUIDE.md trait pipeline:
  data/traits/*.json → TraitToObserver.compile() → ObserverRegistry.register()
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import TYPE_CHECKING

from backend.vm.compiler.trait_to_observer import TraitToObserver
from backend.vm.effect_factory import from_dict as effect_from_dict

if TYPE_CHECKING:
    from backend.engine.observer import ObserverRegistry
    from backend.sim.sprite import Sprite


# Cache of loaded trait JSON (id → data)
_trait_cache: dict[int, dict] = {}
# Cache of parsed trait JSON files by filename (id-less traits re-read the
# file on every entry/switch otherwise) and the _ids.json index.
_trait_file_cache: dict[str, dict] = {}
_ids_json_cache: dict[str, dict] = {}
_compiler = TraitToObserver()


class TraitLoader:
    """Load trait JSON files and register their observers."""

    def __init__(self, registry: ObserverRegistry, data_dir: str | None = None):
        self.registry = registry
        self._data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent.parent / "data" / "traits"
        # Track which observers belong to which sprite for cleanup
        self._sprite_sources: dict[int, set[str]] = {}  # sprite_id → {source_name}
        self._direct_mod_sprite_ids: set[int] = set()

    # ── Loading ──

    def load_for_sprite(self, sprite: Sprite, *, apply_state: bool = True):
        """Load and register all observers from the sprite's trait.

        Safe to call multiple times — existing observers for this sprite
        are replaced rather than duplicated. ``apply_state=False`` is used
        while restoring a serialized battle: observers are rebuilt, but the
        already-restored effect and modifier state is left untouched.
        """
        species = getattr(sprite, 'species', None)
        trait_id = getattr(species, 'ability_id', 0) if species else 0
        trait_name = getattr(species, 'ability', '') if species else ''
        if not trait_id and not trait_name:
            return

        trait_data = self._load_trait_data(trait_id, trait_name)
        if not trait_data:
            return

        sprite_id = id(sprite)

        # Deduplicate: remove existing observers and direct modifiers before re-registering
        self.registry.unregister_by_owner(sprite_id, "reload")
        if apply_state:
            self._remove_direct_mods(sprite)

        trait_source = trait_data.get("name", trait_name)
        effects = trait_data.get("effects", [])

        from backend.engine.observer import Observer

        # Split effects: observer ops → compile to Observers; other ops → apply directly
        observer_effects = [e for e in effects if e.get("op") == "observer"]
        direct_effects = [deepcopy(e) for e in effects if e.get("op") != "observer"]

        # ── EffectObject construction (identity layer, IR-transparent) ──
        # Clear old ObserverEffect/ModifierEffect from same source (reload dedup).
        # Preserve StatBuffEffect — those are created by the battle replayer
        # during combat and must survive trait reload (e.g. permanent stat stages).
        from backend.vm.effect import ModifierEffect, ObserverEffect
        if apply_state:
            active = getattr(sprite, 'active_effects', None)
            if active:
                sprite.active_effects = [
                    e for e in active
                    if not (isinstance(e, (ObserverEffect, ModifierEffect)) and e.source == trait_source)
                ]
            else:
                sprite.active_effects = []
            for e in effects:
                obj = effect_from_dict(e, source=trait_source)
                if obj is not None:
                    sprite.active_effects.append(obj)

        sources: set[str] = set()

        if observer_effects:
            obs_params = _compiler.compile(observer_effects)
            if obs_params:
                for params in obs_params:
                    if not params.get("source"):
                        params["source"] = trait_source
                    obs = Observer(
                        cond=params["cond"],
                        then=params["then"],
                        scope=params["scope"],
                        name=params["name"],
                        source=params["source"],
                        listen=params["listen"],
                        threshold=params["threshold"],
                        reset_on_fire=params["reset_on_fire"],
                        reset=params.get("reset", ""),
                        once=params.get("once", False),
                        owner_sprite_id=sprite_id,
                    )
                    self.registry.register(obs)
                    sources.add(obs.source)

        # Apply non-observer effects as permanent modifiers to matching skills.
        # Also cache the raw effects so they can be re-applied after
        # _PER_TURN_KEYS cleanup each turn.
        if apply_state:
            if direct_effects:
                self._apply_direct_mods(sprite, direct_effects)
                sprite._trait_direct_effects = direct_effects
                self._direct_mod_sprite_ids.add(sprite_id)
            else:
                sprite._trait_direct_effects = None
                self._direct_mod_sprite_ids.discard(sprite_id)
        else:
            restored_direct_effects = getattr(sprite, '_trait_direct_effects', None)
            if restored_direct_effects is None and direct_effects:
                restored_direct_effects = direct_effects
                sprite._trait_direct_effects = direct_effects
            if restored_direct_effects:
                self._direct_mod_sprite_ids.add(sprite_id)
            else:
                self._direct_mod_sprite_ids.discard(sprite_id)

        self._sprite_sources[sprite_id] = sources

    # ── Unloading ──

    def unload_for_sprite(self, sprite, reason: str = "leave"):
        """Remove observers owned by a sprite.

        Args:
            sprite: The sprite being removed
            reason: 'leave' (switch out) or 'faint' (KO)
        """
        sprite_id = id(sprite)
        self.registry.unregister_by_owner(sprite_id, reason)
        self._sprite_sources.pop(sprite_id, None)
        self._direct_mod_sprite_ids.discard(sprite_id)
        self._remove_direct_mods(sprite)

        # Clear EffectObjects matching this reason
        active = getattr(sprite, 'active_effects', None)
        if active:
            remaining = [e for e in active if not e.should_clear(reason)]
            if len(remaining) != len(active):
                sprite.active_effects = remaining
                sprite._invalidate_effects_cache()

    def reapply_all_direct_mods(self, sprites: list, mark_mods: dict[int, int] | None = None):
        """Re-apply trait direct modifiers to all sprites (after _PER_TURN_KEYS cleanup)."""
        if not self._direct_mod_sprite_ids:
            return
        for sprite in sprites:
            sprite_id = id(sprite)
            if sprite_id not in self._direct_mod_sprite_ids:
                continue
            effects = getattr(sprite, '_trait_direct_effects', None)
            if effects:
                # Decrement ttl before re-applying; remove expired effects
                expired = []
                for e in effects:
                    ttl = e.get("ttl", 0)
                    if ttl > 0:
                        e["ttl"] = ttl - 1
                        if e["ttl"] <= 0:
                            expired.append(e)
                for e in expired:
                    effects.remove(e)
                    # Clean up display StatBuffEffect when modifier expires
                    self._remove_display_effect(sprite, e)
                if not effects:
                    self._direct_mod_sprite_ids.discard(sprite_id)
                    continue
                mark_mod = (mark_mods or {}).get(id(sprite), 0)
                self._apply_direct_mods(sprite, effects, mark_energy_mod=mark_mod)

    # ── Direct modifiers (non-observer effects like power_mod in effects[]) ──

    # Attrs that apply to sprite properties, not skills (consumed by property methods)
    # energy_gain_delta：每次回复能量的修正量（盗魂铃「在场时自己回复的能量-4」，
    # 由 Sprite.gain_energy 消费）
    _SPRITE_LEVEL_ATTRS = frozenset({
        'max_energy', 'starfall_consume_ratio', 'immune_abnormal', 'immune_stat_down',
        'energy_gain_delta',
    })

    # Ratio stats whose default value is 1.0 (not 0.0)
    _RATIO_BASE_STATS: frozenset[str] = frozenset({
        "power_mult", "damage_mult", "energy_cost_mult",
        "heal_reverse", "ignore_resistance", "ignore_mods", "survive",
    })

    def _apply_direct_mods(self, sprite, effects: list[dict], mark_energy_mod: int = 0):
        """Apply non-observer trait effects as permanent modifiers to matching skills.

        Processes energy_cost effects first so that other attr effects
        (e.g. power_mult with skill_where={"energy_cost": 0}) see the
        correctly reduced energy_cost values.

        mark_energy_mod: team-level mark energy reduction to include in
        skill_where energy_cost checks.
        """
        from backend.engine.modifiers import eval_skill_where, matches_skill_filter

        # Sort: energy_cost first, then everything else
        sorted_effects = sorted(
            effects,
            key=lambda e: 0 if e.get("attr") == "energy_cost" else 1,
        )

        tracked: dict[str, dict[str, float]] = {}
        for effect in sorted_effects:
            op = effect.get("op", "")
            if op == "burst_grant":
                self._apply_burst_grant_direct(sprite, effect)
                continue
            # power_mod / mult_mod 都写技能槽 `_modifiers`（读取点相同：build_ctx 的
            # skill_mods）；此前只吃 power_mod，顶层 `mult_mod`（不移 / 目空）整类被丢弃。
            if op not in ("power_mod", "mult_mod"):
                continue
            attr = effect.get("attr", "")
            if attr in self._SPRITE_LEVEL_ATTRS:
                continue  # sprite-level attrs read by property methods
            delta = effect.get("delta", effect.get("value", 0))
            if isinstance(delta, dict):
                continue
            if attr == "energy_cost":
                delta *= sprite._modifiers.get("energy_cost_delta_mult", 1.0)
            skill_where = effect.get("skill_where")
            skill_filter = effect.get("skill_filter")
            element_f = effect.get("element")
            for bs in (sprite.skills or []):
                bs_mods = getattr(bs, '_modifiers', None)
                if bs_mods is None:
                    continue
                bs_name = getattr(bs, 'name', '')
                if skill_where or skill_filter or element_f:
                    skill_info = {
                        "name": bs_name,
                        "energy_cost": max(0, getattr(bs, 'energy_cost', 0) - mark_energy_mod),
                        "element": getattr(getattr(bs, 'base', None), 'element', ''),
                        "skill_type": getattr(getattr(bs, 'base', None), 'skill_type', ''),
                    }
                if skill_where:
                    if not eval_skill_where(skill_where, skill_info):
                        continue
                if skill_filter and skill_filter != "all":
                    if not matches_skill_filter(skill_filter, bs, sprite=sprite):
                        continue
                if element_f:
                    # "光" 精确匹配；"!幻" 排除该系别
                    expected = element_f[1:] if element_f.startswith("!") else element_f
                    actual = skill_info.get("element", "")
                    if element_f.startswith("!"):
                        if actual == expected:
                            continue
                    elif actual != expected:
                        continue
                # power_mod 默认 add；mult_mod 默认 set（与 op_mult_mod 的 IR 默认一致）
                mode = effect.get("mode", "set" if op == "mult_mod" else "add")
                if mode == "set":
                    bs_mods[attr] = delta
                    tracked.setdefault(bs_name, {})[attr] = delta
                else:
                    default = 1.0 if attr in self._RATIO_BASE_STATS else 0.0
                    cur = bs_mods.get(attr, default)
                    bs_mods[attr] = cur + delta
                    tracked.setdefault(bs_name, {})[attr] = tracked.get(bs_name, {}).get(attr, 0.0) + delta
        sprite._direct_mod_tracked = tracked

    _ATTACK_TYPES: frozenset[str] = frozenset({"物攻", "魔攻", "动态攻击"})

    def _apply_burst_grant_direct(self, sprite, effect: dict):
        """Apply burst_grant direct effect: write then[] to matching skills' _burst_effects.

        `_burst_effects` 的唯一表示是 **IR**（`data/IR_GUIDE.md` §3D `burst_grant`）。技能显式
        `then` 与 `from:"triggered"` 两条路径写进去的本来就是已编译 IR，而**本函数所在的特性
        direct-mods 通道运行期直接解释特性 JSON、不经过编译器**，所以要在这里补编译：
        写 dict 会与 IR 条目混在同一列表，同源去重按 dict 读 `source` 就崩在 `WhenBlock` 上
        （2026-09-23 打局千局级实测，`AttributeError: 'WhenBlock' object has no attribute 'get'`）。
        """
        from backend.engine.modifiers import eval_skill_where
        from backend.vm.executor import compile_effects_batch

        skill_where = effect.get("skill_where")
        skill_filter = effect.get("skill_filter")
        then_effects = compile_effects_batch(effect.get("then", []) or [])
        source = effect.get("source", "")
        if not then_effects:
            return

        for bs in (sprite.skills or []):
            if skill_where:
                skill_info = {
                    "name": getattr(bs, 'name', ''),
                    "energy_cost": getattr(bs, 'energy_cost', 0),
                    "element": getattr(getattr(bs, 'base', None), 'element', ''),
                    "skill_type": getattr(getattr(bs, 'base', None), 'skill_type', ''),
                }
                if not eval_skill_where(skill_where, skill_info):
                    continue
            if skill_filter and skill_filter != "all":
                st = getattr(getattr(bs, 'base', None), 'skill_type', '')
                if skill_filter == "attack" and st not in self._ATTACK_TYPES or skill_filter == "defense" and st != "防御" or skill_filter == "status" and st != "状态":
                    continue
            # Remove existing burst effects from same source before re-adding。
            # `source` 是 IR 字段；池子里取回的顶层条目可能是 `WhenBlock`（没有该字段）
            # → 读不到就当"不同源"保留，绝不用 `.get()`（那正是崩溃点）。
            bs._burst_effects = [e for e in bs._burst_effects
                                if getattr(e, "source", None) != source]
            bs._burst_effects.extend(then_effects)
            bs._modifiers["burst"] = float(len(bs._burst_effects) > 0)

    def _remove_direct_mods(self, sprite):
        """Remove direct modifiers previously applied to a sprite's skills."""
        tracked = getattr(sprite, '_direct_mod_tracked', None)
        if not tracked:
            return
        for bs in (sprite.skills or []):
            bs_mods = getattr(bs, '_modifiers', None)
            if bs_mods is None:
                continue
            bs_name = getattr(bs, 'name', '')
            if bs_name in tracked:
                for attr, delta in tracked[bs_name].items():
                    bs_mods[attr] = bs_mods.get(attr, 0.0) - delta
        sprite._direct_mod_tracked = None

    def _remove_display_effect(self, sprite, effect: dict):
        """Remove the StatBuffEffect matching an expired effect_dict."""
        from backend.vm.effect import StatBuffEffect
        attr = effect.get("attr", "")
        source = effect.get("source", "")
        active = getattr(sprite, 'active_effects', None)
        if not active or not source:
            return
        to_remove = [
            e for e in active
            if isinstance(e, StatBuffEffect) and e.stat_key == attr
            and e.source == source and e.steps == 0
        ]
        for e in to_remove:
            if e in active:
                active.remove(e)

    # ── Internal ──

    def _load_trait_data(self, trait_id: int, trait_name: str) -> dict | None:
        """Load trait JSON from disk, using cache."""
        if trait_id and trait_id in _trait_cache:
            return _trait_cache[trait_id]

        # Try by ID first, then by name
        if trait_id:
            # Look up in _ids.json index
            ids_file = self._data_dir / "_ids.json"
            if ids_file.exists():
                try:
                    if str(ids_file) not in _ids_json_cache:
                        _ids_json_cache[str(ids_file)] = json.loads(
                            ids_file.read_text("utf-8"))
                    by_id = _ids_json_cache[str(ids_file)].get("by_id", {})
                    entry = by_id.get(str(trait_id))
                    if entry:
                        fname = entry.get("file", entry.get("filename", ""))
                        if fname:
                            fpath = self._data_dir / fname
                            if fpath.exists():
                                key = str(fpath)
                                if key not in _trait_file_cache:
                                    _trait_file_cache[key] = json.loads(
                                        fpath.read_text("utf-8"))
                                data = _trait_file_cache[key]
                                _trait_cache[trait_id] = data
                                return data
                except Exception:
                    pass

        # Try by name
        if trait_name:
            fpath = self._data_dir / f"{trait_name}.json"
            if fpath.exists():
                try:
                    key = str(fpath)
                    if key not in _trait_file_cache:
                        _trait_file_cache[key] = json.loads(
                            fpath.read_text("utf-8"))
                    data = _trait_file_cache[key]
                    if trait_id:
                        _trait_cache[trait_id] = data
                    return data
                except Exception:
                    pass

        return None
