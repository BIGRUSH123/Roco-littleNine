"""TraitLoader — load trait JSON and register Observers with battle engine.

The entry point for the IR_RISC.md trait pipeline:
  data/traits/*.json → TraitToObserver.compile() → ObserverRegistry.register()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from backend.vm.compiler.trait_to_observer import TraitToObserver

if TYPE_CHECKING:
    from backend.engine.observer import ObserverRegistry
    from backend.sim.sprite import Sprite


# Cache of loaded trait JSON (id → data)
_trait_cache: dict[int, dict] = {}
_compiler = TraitToObserver()


class TraitLoader:
    """Load trait JSON files and register their observers."""

    def __init__(self, registry: ObserverRegistry, data_dir: str | None = None):
        self.registry = registry
        self._data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent.parent / "data" / "traits"
        # Track which observers belong to which sprite for cleanup
        self._sprite_sources: dict[int, set[str]] = {}  # sprite_id → {source_name}

    # ── Loading ──

    def load_for_sprite(self, sprite: Sprite):
        """Load and register all observers from the sprite's trait.

        Safe to call multiple times — existing observers for this sprite
        are replaced rather than duplicated.
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
        self._remove_direct_mods(sprite)

        trait_source = trait_data.get("name", trait_name)
        effects = trait_data.get("effects", [])

        from backend.engine.observer import Observer

        # Split effects: observer ops → compile to Observers; other ops → apply directly
        observer_effects = [e for e in effects if e.get("op") == "observer"]
        direct_effects = [e for e in effects if e.get("op") != "observer"]

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
                        owner_sprite_id=sprite_id,
                    )
                    self.registry.register(obs)
                    sources.add(obs.source)

        # Apply non-observer effects as permanent modifiers to matching skills.
        # Also cache the raw effects so they can be re-applied after
        # _PER_TURN_KEYS cleanup each turn.
        if direct_effects:
            self._apply_direct_mods(sprite, direct_effects)
            sprite._trait_direct_effects = direct_effects
        else:
            sprite._trait_direct_effects = None

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
        self._remove_direct_mods(sprite)

    def reapply_all_direct_mods(self, sprites: list):
        """Re-apply trait direct modifiers to all sprites (after _PER_TURN_KEYS cleanup)."""
        for sprite in sprites:
            effects = getattr(sprite, '_trait_direct_effects', None)
            if effects:
                self._apply_direct_mods(sprite, effects)

    # ── Direct modifiers (non-observer effects like power_mod in effects[]) ──

    def _apply_direct_mods(self, sprite, effects: list[dict]):
        """Apply non-observer trait effects as permanent modifiers to matching skills."""
        from backend.engine.modifiers import eval_skill_where

        tracked: dict[str, dict[str, float]] = {}
        for effect in effects:
            op = effect.get("op", "")
            if op != "power_mod":
                continue
            attr = effect.get("attr", "")
            delta = effect.get("delta", 0)
            if isinstance(delta, dict):
                continue
            skill_where = effect.get("skill_where")
            for bs in (sprite.skills or []):
                bs_mods = getattr(bs, '_modifiers', None)
                if bs_mods is None:
                    continue
                bs_name = getattr(bs, 'name', '')
                if skill_where:
                    skill_info = {
                        "name": bs_name,
                        "energy_cost": getattr(bs, 'energy_cost', 0),
                        "element": getattr(getattr(bs, 'base', None), 'element', ''),
                        "skill_type": getattr(getattr(bs, 'base', None), 'skill_type', ''),
                    }
                    if not eval_skill_where(skill_where, skill_info):
                        continue
                cur = bs_mods.get(attr, 0.0)
                bs_mods[attr] = cur + delta
                tracked.setdefault(bs_name, {})[attr] = tracked.get(bs_name, {}).get(attr, 0.0) + delta
        sprite._direct_mod_tracked = tracked

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
                    ids_data = json.loads(ids_file.read_text("utf-8"))
                    by_id = ids_data.get("by_id", {})
                    entry = by_id.get(str(trait_id))
                    if entry:
                        fname = entry.get("file", entry.get("filename", ""))
                        if fname:
                            fpath = self._data_dir / fname
                            if fpath.exists():
                                data = json.loads(fpath.read_text("utf-8"))
                                _trait_cache[trait_id] = data
                                return data
                except Exception:
                    pass

        # Try by name
        if trait_name:
            fpath = self._data_dir / f"{trait_name}.json"
            if fpath.exists():
                try:
                    data = json.loads(fpath.read_text("utf-8"))
                    if trait_id:
                        _trait_cache[trait_id] = data
                    return data
                except Exception:
                    pass

        return None
