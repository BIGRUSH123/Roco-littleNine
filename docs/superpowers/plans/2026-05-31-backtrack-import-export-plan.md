# 回溯 & 导入导出 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Battle 引擎添加回溯（恢复到指定回合）和导入导出（队伍/对局的 JSON+文本双格式）功能。

**Architecture:** 统一序列化层 `backend/engine/serializer.py` 提供所有对象的 `to_dict()`/`from_dict()`。回溯用 `Battle.save_snapshot()`/`restore_snapshot()` 在每回合初拍快照存内存。导入导出通过 `roco/serializer.py` CLI 读写 JSON/文本文件。序列化策略：引用数据（species/skill）只存标识符，可变状态完整序列化。

**Tech Stack:** Python dataclasses, JSON, argparse

---

### Task 1: 序列化 Effect 类

**Files:**
- Create: `backend/engine/serializer.py`

- [ ] **Step 1: 创建 serializer.py 骨架 + effect_to_dict**

```python
"""backend/engine/serializer.py — 对战状态序列化/反序列化

Unified to_dict()/from_dict() for all stateful objects.
Backtracking and import/export share this layer.
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════
# Effect serialization
# ═══════════════════════════════════════════════════════════════

def effect_to_dict(effect: Any) -> dict:
    """Serialize any EffectObject subclass to dict."""
    from backend.vm.effect import (
        AbnormalEffect, EffectObject, MarkEffect, ModifierEffect,
        ObserverEffect, StatBuffEffect, StateEffect,
    )
    base = {
        "name": effect.name,
        "source": effect.source,
        "scope": effect.scope,
        "ttl": effect.ttl,
        "_type": type(effect).__name__,
    }
    if isinstance(effect, StatBuffEffect):
        base["stat_key"] = effect.stat_key
        base["steps"] = effect.steps
        if effect.display_mult is not None:
            base["display_mult"] = effect.display_mult
        if effect.display_value is not None:
            base["display_value"] = effect.display_value
        if effect.is_inherent:
            base["is_inherent"] = True
    elif isinstance(effect, AbnormalEffect):
        base["stacks"] = effect.stacks
        base["tick_damage_pct"] = effect.tick_damage_pct
        base["tick_element"] = effect.tick_element
        base["decay_on_tick"] = effect.decay_on_tick
        base["max_stacks"] = effect.max_stacks
        base["tick_per_stack"] = effect.tick_per_stack
    elif isinstance(effect, StateEffect):
        base["state_type"] = effect.state_type
        base["params"] = dict(effect.params) if effect.params else {}
    elif isinstance(effect, ModifierEffect):
        base["target"] = effect.target
        base["attr"] = effect.attr
        base["value"] = effect.value
        base["mode"] = effect.mode
        if effect.skill_where:
            base["skill_where"] = effect.skill_where
    elif isinstance(effect, MarkEffect):
        base["stacks"] = effect.stacks
        base["category"] = effect.category
        base["power_bonus"] = effect.power_bonus
        base["damage_mult"] = effect.damage_mult
        base["speed_penalty"] = effect.speed_penalty
        base["energy_mod"] = effect.energy_mod
        base["turn_end_energy"] = effect.turn_end_energy
        base["turn_end_damage_pct"] = effect.turn_end_damage_pct
        base["switch_damage_pct"] = effect.switch_damage_pct
        base["switch_energy_loss"] = effect.switch_energy_loss
        base["starfall_damage"] = effect.starfall_damage
        if effect.condition:
            base["condition"] = effect.condition
    elif isinstance(effect, ObserverEffect):
        base["cond"] = effect.cond
        base["then"] = effect.then
        base["listen"] = list(effect.listen) if effect.listen else []
        base["threshold"] = effect.threshold
        base["reset_on_fire"] = effect.reset_on_fire
    return base


def effect_from_dict(d: dict) -> Any:
    """Deserialize a dict back to an EffectObject subclass."""
    from backend.vm.effect import (
        AbnormalEffect, MarkEffect, ModifierEffect,
        ObserverEffect, StatBuffEffect, StateEffect,
    )
    _type = d.pop("_type", "")
    if _type == "StatBuffEffect":
        return StatBuffEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), stat_key=d.get("stat_key", ""),
            steps=d.get("steps", 0),
            display_mult=d.get("display_mult"),
            display_value=d.get("display_value"),
            is_inherent=d.get("is_inherent", False),
        )
    if _type == "AbnormalEffect":
        return AbnormalEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), stacks=d.get("stacks", 0),
            tick_damage_pct=d.get("tick_damage_pct", 0.0),
            tick_element=d.get("tick_element", ""),
            decay_on_tick=d.get("decay_on_tick", False),
            max_stacks=d.get("max_stacks", 0),
            tick_per_stack=d.get("tick_per_stack", True),
        )
    if _type == "StateEffect":
        return StateEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), state_type=d.get("state_type", ""),
            params=d.get("params", {}),
        )
    if _type == "ModifierEffect":
        return ModifierEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), target=d.get("target", "sprite_self"),
            attr=d.get("attr", ""), value=d.get("value", 0.0),
            mode=d.get("mode", "add"), skill_where=d.get("skill_where"),
        )
    if _type == "MarkEffect":
        return MarkEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "persistent"),
            ttl=d.get("ttl", 0), stacks=d.get("stacks", 0),
            category=d.get("category", "negative"),
            power_bonus=d.get("power_bonus", 0),
            damage_mult=d.get("damage_mult", 0.0),
            speed_penalty=d.get("speed_penalty", 0),
            energy_mod=d.get("energy_mod", 0),
            turn_end_energy=d.get("turn_end_energy", 0),
            turn_end_damage_pct=d.get("turn_end_damage_pct", 0.0),
            switch_damage_pct=d.get("switch_damage_pct", 0.0),
            switch_energy_loss=d.get("switch_energy_loss", 0),
            starfall_damage=d.get("starfall_damage", 0),
            condition=d.get("condition", ""),
        )
    if _type == "ObserverEffect":
        return ObserverEffect(
            name=d["name"], source=d["source"], scope=d.get("scope", "battlefield"),
            ttl=d.get("ttl", 0), cond=d.get("cond", {}),
            then=d.get("then", []), listen=frozenset(d.get("listen", [])),
            threshold=d.get("threshold", 1),
            reset_on_fire=d.get("reset_on_fire", True),
        )
    raise ValueError(f"Unknown effect type: {_type}")
```

- [ ] **Step 2: 运行现有测试确认无回归**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all existing tests pass

- [ ] **Step 3: Commit**

```bash
git add backend/engine/serializer.py
git commit -m "feat: 添加 Effect 类序列化 (to_dict/from_dict)"
```

---

### Task 2: 序列化 BattleSkill

**Files:**
- Modify: `backend/engine/serializer.py` (追加)

- [ ] **Step 1: 实现 battle_skill_to_dict / battle_skill_from_dict**

追加到 `backend/engine/serializer.py`:

```python
# ═══════════════════════════════════════════════════════════════
# BattleSkill serialization
# ═══════════════════════════════════════════════════════════════

def battle_skill_to_dict(bs) -> dict:
    """Serialize BattleSkill — only stores base skill name as reference."""
    return {
        "base_name": bs.base.name if bs.base else "",
        "_modifiers": dict(bs._modifiers),
        "sealed": bs.sealed,
        "_transmission": bs._transmission,
        "_burst_effects": list(bs._burst_effects),
        "is_temporary": bs.is_temporary,
        "cooldown": bs.cooldown,
        "next_attack_mult": bs.next_attack_mult,
        "_element_override": bs._element_override,
        "_mech_energy_reduction": bs._mech_energy_reduction,
    }


def battle_skill_from_dict(d: dict, skill_loader) -> Any:
    """Reconstruct BattleSkill from dict.

    skill_loader: callable(name) -> BattleSkill — from SimFactory._build_skill_list
    """
    base_name = d.get("base_name", "")
    if not base_name or skill_loader is None:
        return None
    skills = skill_loader([base_name])
    if not skills:
        return None
    bs = skills[0]
    bs._modifiers = dict(d.get("_modifiers", {}))
    bs.sealed = d.get("sealed", False)
    bs._transmission = d.get("_transmission", 0)
    bs._burst_effects = list(d.get("_burst_effects", []))
    bs.is_temporary = d.get("is_temporary", False)
    bs.cooldown = d.get("cooldown", 0)
    bs.next_attack_mult = d.get("next_attack_mult", 1.0)
    bs._element_override = d.get("_element_override", "")
    bs._mech_energy_reduction = d.get("_mech_energy_reduction", 0)
    return bs
```

- [ ] **Step 2: 运行现有测试**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add backend/engine/serializer.py
git commit -m "feat: 添加 BattleSkill 序列化"
```

---

### Task 3: 序列化 Sprite

**Files:**
- Modify: `backend/engine/serializer.py` (追加)

- [ ] **Step 1: 实现 sprite_to_dict / sprite_from_dict**

追加到 `backend/engine/serializer.py`:

```python
# ═══════════════════════════════════════════════════════════════
# Sprite serialization
# ═══════════════════════════════════════════════════════════════

def sprite_to_dict(sprite) -> dict:
    """Serialize Sprite — species as name/number/form identifier."""
    return {
        "species_name": sprite.species.name,
        "species_number": sprite.species.number,
        "species_form": sprite.species.form,
        "bloodline": sprite.bloodline,
        "initial_stats": dict(sprite.initial_stats),
        "nature": sprite.nature,
        "iv": dict(sprite.iv),
        "current_hp": sprite.current_hp,
        "max_hp": sprite.max_hp,
        "energy": sprite.energy,
        "active_effects": [effect_to_dict(e) for e in sprite.active_effects],
        "entry_turn": sprite.entry_turn,
        "counters": dict(sprite.counters),
        "first_action": sprite.first_action,
        "first_action_battle": sprite.first_action_battle,
        "pending_return": sprite.pending_return,
        "locked_turns": sprite.locked_turns,
        "_modifiers": dict(sprite._modifiers),
        "_mod_scopes": dict(sprite._mod_scopes),
        "_pending_effects": [
            (effect_to_dict(e), delay) for e, delay in sprite._pending_effects
        ],
        "_pending_modifiers": [
            {
                "stat": m.stat, "value": m.value, "mode": m.mode,
                "target": m.target, "scope": getattr(m, 'scope', 'turn'),
                "source": getattr(m, 'source', ''),
                "on_next": getattr(m, 'on_next', True),
                "skill_where": getattr(m, 'skill_where', None),
                "skill_filter": getattr(m, 'skill_filter', None),
            }
            for m in sprite._pending_modifiers
        ],
        "_trait_suppressed": sprite._trait_suppressed,
        "skills": [battle_skill_to_dict(bs) for bs in (sprite.skills or [])],
    }


def sprite_from_dict(d: dict, species_db, skill_loader) -> Any:
    """Reconstruct Sprite from dict.

    species_db: callable(name, form) -> SpeciesStats — from SimFactory.sprite_db.get
    skill_loader: callable(names) -> list[BattleSkill]
    """
    from backend.common.models import SpeciesStats
    from backend.sim.sprite import Sprite

    species = species_db(d["species_name"], d.get("species_form", ""))
    if species is None:
        raise ValueError(f"Species not found: {d['species_name']!r}")

    sprite = Sprite(
        species=species,
        bloodline=d.get("bloodline", ""),
        initial_stats=dict(d.get("initial_stats", {})),
        current_hp=d.get("current_hp", 0),
        max_hp=d.get("max_hp", 0),
        energy=d.get("energy", 10),
        nature=d.get("nature"),
        iv=dict(d.get("iv", {})),
    )
    sprite.entry_turn = d.get("entry_turn", 0)
    sprite.counters = dict(d.get("counters", {}))
    sprite.first_action = d.get("first_action", True)
    sprite.first_action_battle = d.get("first_action_battle", True)
    sprite.pending_return = d.get("pending_return", False)
    sprite.locked_turns = d.get("locked_turns", 0)
    sprite._modifiers = dict(d.get("_modifiers", {}))
    sprite._mod_scopes = dict(d.get("_mod_scopes", {}))

    # active_effects
    sprite.active_effects = [effect_from_dict(e) for e in d.get("active_effects", [])]

    # _pending_effects
    sprite._pending_effects = [
        (effect_from_dict(e), delay)
        for e, delay in d.get("_pending_effects", [])
    ]

    # _pending_modifiers — reconstruct as ModifierInjection objects
    from backend.vm.journal import ModifierInjection
    sprite._pending_modifiers = [
        ModifierInjection(
            stat=m["stat"], value=m["value"], mode=m["mode"],
            target=m.get("target", "sprite_self"),
            scope=m.get("scope", "turn"),
            source=m.get("source", ""),
            on_next=m.get("on_next", True),
            skill_where=m.get("skill_where"),
            skill_filter=m.get("skill_filter"),
        )
        for m in d.get("_pending_modifiers", [])
    ]

    sprite._trait_suppressed = d.get("_trait_suppressed", False)

    # skills
    if skill_loader is not None:
        sprite.skills = [
            bs for bs in (
                battle_skill_from_dict(sd, skill_loader) for sd in d.get("skills", [])
            )
            if bs is not None
        ]
    else:
        sprite.skills = []

    return sprite
```

- [ ] **Step 2: 运行现有测试**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add backend/engine/serializer.py
git commit -m "feat: 添加 Sprite 序列化"
```

---

### Task 4: 序列化 Player 和 GlobalEffects

**Files:**
- Modify: `backend/engine/serializer.py` (追加)

- [ ] **Step 1: 实现 player_to_dict / player_from_dict 和 globals_to_dict / globals_from_dict**

追加到 `backend/engine/serializer.py`:

```python
# ═══════════════════════════════════════════════════════════════
# Player serialization
# ═══════════════════════════════════════════════════════════════

def player_to_dict(player) -> dict:
    """Serialize Player — sprites as list of dicts."""
    return {
        "name": player.name,
        "lives": player.lives,
        "active_index": player.active_index,
        "devotion": dict(player.devotion),
        "team": [sprite_to_dict(s) for s in player.team],
        "item": {
            "name": player.item.name,
            "max_uses": player.item.max_uses,
            "cooldown_turns": player.item.cooldown_turns,
            "uses": player.item.uses,
            "last_use_turn": player.item.last_use_turn,
        } if player.item else None,
    }


def player_from_dict(d: dict, species_db, skill_loader) -> Any:
    """Reconstruct Player from dict."""
    from backend.sim.player import Item, Player, PlayStyle

    team = [sprite_from_dict(sd, species_db, skill_loader) for sd in d.get("team", [])]
    item = None
    if d.get("item"):
        idata = d["item"]
        item = Item(
            name=idata["name"], max_uses=idata["max_uses"],
            cooldown_turns=idata.get("cooldown_turns", 0),
            uses=idata.get("uses", 0),
            last_use_turn=idata.get("last_use_turn", 0),
        )
    return Player(
        name=d["name"], team=team, style=PlayStyle(),
        lives=d.get("lives", 4), active_index=d.get("active_index", 0),
        item=item, devotion=dict(d.get("devotion", {})),
    )


# ═══════════════════════════════════════════════════════════════
# GlobalEffects serialization
# ═══════════════════════════════════════════════════════════════

def globals_to_dict(g) -> dict:
    """Serialize GlobalEffects."""
    return {
        "weather": g.weather,
        "weather_turns": g.weather_turns,
        "marks_a": [effect_to_dict(m) for m in g.mark_effects.get("A", [])],
        "marks_b": [effect_to_dict(m) for m in g.mark_effects.get("B", [])],
    }


def globals_from_dict(d: dict) -> Any:
    """Reconstruct GlobalEffects from dict."""
    from backend.sim.globals import GlobalEffects
    g = GlobalEffects()
    g.weather = d.get("weather", "")
    g.weather_turns = d.get("weather_turns", 0)
    g.mark_effects["A"] = [effect_from_dict(m) for m in d.get("marks_a", [])]
    g.mark_effects["B"] = [effect_from_dict(m) for m in d.get("marks_b", [])]
    return g
```

- [ ] **Step 2: 运行现有测试**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add backend/engine/serializer.py
git commit -m "feat: 添加 Player 和 GlobalEffects 序列化"
```

---

### Task 5: 序列化 BattleVMEngine 状态

**Files:**
- Modify: `backend/engine/serializer.py` (追加)

- [ ] **Step 1: 实现 vm_state_to_dict / vm_state_from_dict**

追加到 `backend/engine/serializer.py`:

```python
# ═══════════════════════════════════════════════════════════════
# VM engine state serialization
# ═══════════════════════════════════════════════════════════════

def vm_state_to_dict(vm_engine) -> dict:
    """Extract VM engine mutable state for serialization."""
    return {
        "counter_values": dict(vm_engine._counter_values),
        "burst_effects": {
            team: [(name, list(effects)) for name, effects in items]
            for team, items in vm_engine._burst_effects.items()
        },
        "burst_names": {
            team: list(names) for team, names in vm_engine._burst_names.items()
        },
        "skill_history": {
            str(sprite_id): [
                (name, list(effects), dict(tags))
                for name, effects, tags in history
            ]
            for sprite_id, history in vm_engine._skill_history.items()
        },
    }


def vm_state_restore(vm_engine, state: dict) -> None:
    """Restore VM engine mutable state from dict (in-place mutation)."""
    vm_engine._counter_values = dict(state.get("counter_values", {}))
    vm_engine._burst_effects = {
        team: [(name, list(effects)) for name, effects in items]
        for team, items in state.get("burst_effects", {}).items()
    }
    vm_engine._burst_names = {
        team: set(names)
        for team, names in state.get("burst_names", {}).items()
    }
    vm_engine._skill_history = {
        int(sprite_id): [
            (name, list(effects), dict(tags))
            for name, effects, tags in history
        ]
        for sprite_id, history in state.get("skill_history", {}).items()
    }
```

- [ ] **Step 2: 运行现有测试**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add backend/engine/serializer.py
git commit -m "feat: 添加 BattleVMEngine 状态序列化"
```

---

### Task 6: BattleSerializer — 完整对局序列化

**Files:**
- Modify: `backend/engine/serializer.py` (追加)

- [ ] **Step 1: 实现 battle_to_dict 和 battle_from_dict + RoundRecord to_dict**

追加到 `backend/engine/serializer.py`:

```python
# ═══════════════════════════════════════════════════════════════
# RoundRecord serialization
# ═══════════════════════════════════════════════════════════════

def round_record_to_dict(rec) -> dict:
    """Serialize RoundRecord to dict (supplements existing to_message())."""
    return {
        "turn": rec.turn,
        "weather": rec.weather,
        "sprite_a": rec.sprite_a,
        "sprite_b": rec.sprite_b,
        "first_team": rec.first_team,
        "turn_start_events": list(rec.turn_start_events),
        "action_a": {
            "team": rec.action_a.team,
            "actor": rec.action_a.actor,
            "kind": rec.action_a.kind,
            "skill_name": rec.action_a.skill_name,
            "events": list(rec.action_a.events),
        } if rec.action_a else None,
        "action_b": {
            "team": rec.action_b.team,
            "actor": rec.action_b.actor,
            "kind": rec.action_b.kind,
            "skill_name": rec.action_b.skill_name,
            "events": list(rec.action_b.events),
        } if rec.action_b else None,
        "turn_end_events": list(rec.turn_end_events),
    }


def round_record_from_dict(d: dict) -> Any:
    """Reconstruct RoundRecord from dict."""
    from backend.sim.round_record import ActionRecord, RoundRecord
    rec = RoundRecord(
        turn=d["turn"], weather=d.get("weather", ""),
        sprite_a=d.get("sprite_a", ""), sprite_b=d.get("sprite_b", ""),
        first_team=d.get("first_team", ""),
    )
    rec.turn_start_events = list(d.get("turn_start_events", []))
    rec.turn_end_events = list(d.get("turn_end_events", []))
    if d.get("action_a"):
        a = d["action_a"]
        rec.action_a = ActionRecord(
            team=a["team"], actor=a["actor"], kind=a["kind"],
            skill_name=a.get("skill_name", ""), events=list(a.get("events", [])),
        )
    if d.get("action_b"):
        b = d["action_b"]
        rec.action_b = ActionRecord(
            team=b["team"], actor=b["actor"], kind=b["kind"],
            skill_name=b.get("skill_name", ""), events=list(b.get("events", [])),
        )
    return rec


# ═══════════════════════════════════════════════════════════════
# Full battle serialization
# ═══════════════════════════════════════════════════════════════

def battle_to_dict(battle) -> dict:
    """Serialize full Battle state."""
    return {
        "version": "1.0",
        "type": "match",
        "turn": battle.turn,
        "winner": battle.winner,
        "player_a": player_to_dict(battle.player_a),
        "player_b": player_to_dict(battle.player_b),
        "globals": globals_to_dict(battle.globals),
        "weather": battle.globals.weather,
        "log": [round_record_to_dict(r) for r in battle.log],
        "vm_state": vm_state_to_dict(battle._vm_engine),
        "team_counters": {
            "A": dict(battle.team_counters.get("A", {})),
            "B": dict(battle.team_counters.get("B", {})),
        },
        "pending_effects": {
            team: [effect_to_dict(e) for e in effects]
            for team, effects in battle.pending_effects.items()
        },
        "scheduled_effects": [
            {
                "turn": se["turn"], "phase": se["phase"],
                "effects": list(se["effects"]),
                "source_name": se["source"].name if se.get("source") else "",
                "ctx_snapshot": dict(se.get("ctx_snapshot", {})),
            }
            for se in battle.scheduled_effects
        ],
    }


def battle_from_dict(d: dict, species_db, skill_loader) -> Any:
    """Reconstruct full Battle from dict.

    species_db: SpriteDB instance (provides .get(name, form))
    skill_loader: callable(names) -> list[BattleSkill]
    """
    from backend.sim.battle import Battle

    player_a = player_from_dict(d["player_a"], species_db.get, skill_loader)
    player_b = player_from_dict(d["player_b"], species_db.get, skill_loader)

    battle = Battle(
        player_a=player_a, player_b=player_b,
        weather=d.get("weather", ""),
        verbose=False,
    )
    battle.turn = d.get("turn", 0)
    battle.winner = d.get("winner")

    # Restore globals
    gd = d.get("globals", {})
    from backend.sim.globals import GlobalEffects
    battle.globals = globals_from_dict(gd)

    # Restore log
    battle.log = [round_record_from_dict(r) for r in d.get("log", [])]

    # Restore VM state
    vm_state_restore(battle._vm_engine, d.get("vm_state", {}))

    # Restore team_counters
    battle.team_counters = {
        "A": dict(d.get("team_counters", {}).get("A", {})),
        "B": dict(d.get("team_counters", {}).get("B", {})),
    }

    # Restore pending_effects
    battle.pending_effects = {
        team: [effect_from_dict(e) for e in effects]
        for team, effects in d.get("pending_effects", {}).items()
    }

    # Restore scheduled_effects (source sprite lookup is best-effort)
    battle.scheduled_effects = []
    for se in d.get("scheduled_effects", []):
        source_name = se.get("source_name", "")
        source_sprite = None
        for s in player_a.team + player_b.team:
            if s.name == source_name:
                source_sprite = s
                break
        battle.scheduled_effects.append({
            "turn": se["turn"], "phase": se["phase"],
            "effects": list(se.get("effects", [])),
            "source": source_sprite,
            "ctx_snapshot": dict(se.get("ctx_snapshot", {})),
        })

    # Inject species_db + skill_loader (needed for transform, moe, gain_skills)
    battle.species_db = species_db
    battle.skill_loader = skill_loader

    return battle
```

- [ ] **Step 2: 运行现有测试**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add backend/engine/serializer.py
git commit -m "feat: 添加完整 Battle 序列化 (to_dict/from_dict)"
```

---

### Task 7: Battle.snapshot / restore

**Files:**
- Modify: `backend/sim/battle.py`

- [ ] **Step 1: 在 Battle.__init__ 添加 snapshots 字典，在 execute_turn 开头添加 save_snapshot 调用**

修改 `backend/sim/battle.py`:

```python
# 在 __init__ 中添加（在 self._vm_engine = BattleVMEngine() 之后）:
        self._snapshots: dict[int, dict] = {}

# 在 execute_turn 开头（self.turn += 1 之前）添加:
    def execute_turn(self, agent_a: Agent, agent_b: Agent) -> RoundRecord:
        self.save_snapshot()          # 回合开始前拍快照
        self.turn += 1
        # ... rest unchanged
```

- [ ] **Step 2: 实现 save_snapshot / restore_snapshot / clear_snapshots**

在 `backend/sim/battle.py` 的 Battle 类中添加方法:

```python
    def save_snapshot(self) -> None:
        """保存当前回合的快照（用于回溯）。"""
        from backend.engine.serializer import battle_to_dict
        self._snapshots[self.turn] = battle_to_dict(self)

    def restore_snapshot(self, turn: int) -> None:
        """恢复到指定回合开始前的状态。"""
        if turn not in self._snapshots:
            raise ValueError(
                f"快照不存在: 回合{turn}。"
                f"可用回合: {sorted(self._snapshots.keys())}"
            )
        from backend.engine.serializer import battle_from_dict

        snapshot = self._snapshots[turn]
        restored = battle_from_dict(
            snapshot, self.species_db, self.skill_loader,
        )
        # Copy restored state back into self
        self.__dict__.update(restored.__dict__)

        # Keep snapshots up to the restored turn, discard later ones
        self._snapshots = {
            t: s for t, s in self._snapshots.items() if t <= turn
        }

    def clear_snapshots(self) -> None:
        """清除所有快照释放内存。"""
        self._snapshots.clear()

    @property
    def snapshots(self) -> dict[int, dict]:
        """返回所有快照（只读视图）。"""
        return dict(self._snapshots)
```

- [ ] **Step 3: 运行现有测试**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add backend/sim/battle.py
git commit -m "feat: Battle 添加 save_snapshot/restore_snapshot 回溯支持"
```

---

### Task 8: CLI 导入导出 + 文本格式

**Files:**
- Create: `roco/serializer.py`

- [ ] **Step 1: 创建 roco/serializer.py**

```python
"""roco.serializer — 导入导出 CLI + 公开 API

Usage:
    python -m roco.serializer export team --team A -o name
    python -m roco.serializer import team name
    python -m roco.serializer export match -o name
    python -m roco.serializer import match name
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_EXPORT_DIR = Path("exports")


def _ensure_dir() -> Path:
    _EXPORT_DIR.mkdir(exist_ok=True)
    return _EXPORT_DIR


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def export_match(battle, name: str, output_dir: str | Path | None = None) -> Path:
    """Export a match to JSON + text files.

    Returns the path to the JSON file.
    """
    from backend.engine.serializer import battle_to_dict

    out = Path(output_dir) if output_dir else _ensure_dir()
    data = battle_to_dict(battle)

    # JSON
    json_path = out / f"{name}.roco-match.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Text (log)
    txt_path = out / f"{name}.roco-match.txt"
    lines = []
    lines.append(f"对局: {battle.player_a.name} vs {battle.player_b.name}")
    lines.append(f"回合: {battle.turn}")
    lines.append(f"天气: {battle.globals.weather or '无'}")
    lines.append(f"结果: {battle.winner or '进行中'}")
    lines.append("")
    for rec in battle.log:
        lines.append(rec.to_message())
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return json_path


def import_match(path: str | Path, factory) -> Any:
    """Import a match from a .roco-match.json file.

    factory: SimFactory instance (provides sprite_db + skill_loader)
    """
    from backend.engine.serializer import battle_from_dict

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    battle = battle_from_dict(data, factory.sprite_db, factory._build_skill_list)
    return battle


def export_team(player, name: str, output_dir: str | Path | None = None) -> Path:
    """Export a team to JSON + text files."""
    from backend.engine.serializer import player_to_dict

    out = Path(output_dir) if output_dir else _ensure_dir()
    data = {
        "version": "1.0",
        "type": "team",
        "name": player.name,
    }
    data.update(player_to_dict(player))

    # JSON
    json_path = out / f"{name}.roco-team.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Text
    txt_path = out / f"{name}.roco-team.txt"
    lines = [f"队伍: {player.name} (生命: {player.lives})"]
    for s in player.team:
        skill_names = ", ".join(bs.base.name for bs in (s.skills or []) if bs.base)
        lines.append(f">>>SPRITE:{s.name}:{s.species.number}:{skill_names}")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return json_path


def import_team(path: str | Path, factory) -> Any:
    """Import a team from a .roco-team.json file.

    factory: SimFactory instance
    """
    from backend.engine.serializer import player_from_dict

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return player_from_dict(data, factory.sprite_db.get, factory._build_skill_list)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _main():
    import argparse

    parser = argparse.ArgumentParser(description="导入导出工具")
    sub = parser.add_subparsers(dest="cmd")

    # export
    exp = sub.add_parser("export")
    exp_sub = exp.add_subparsers(dest="type")
    exp_team = exp_sub.add_parser("team")
    exp_team.add_argument("--team", choices=["A", "B"], default="A")
    exp_team.add_argument("-o", "--output", required=True)
    exp_match = exp_sub.add_parser("match")
    exp_match.add_argument("-o", "--output", required=True)

    # import
    imp = sub.add_parser("import")
    imp_sub = imp.add_subparsers(dest="type")
    imp_team = imp_sub.add_parser("team")
    imp_team.add_argument("name")
    imp_match = imp_sub.add_parser("match")
    imp_match.add_argument("name")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    from backend.sim.factory import SimFactory
    factory = SimFactory()

    if args.cmd == "export":
        if args.type == "team":
            print("导出队伍需要从正在进行的对局中调用 export_team(player, name)。")
            print("请使用 Python API: from roco.serializer import export_team")
        elif args.type == "match":
            print("导出对局需要从正在进行的对局中调用 export_match(battle, name)。")
            print("请使用 Python API: from roco.serializer import export_match")
    elif args.cmd == "import":
        if args.type == "team":
            name = args.name
            path = _EXPORT_DIR / f"{name}.roco-team.json"
            if not path.exists():
                print(f"文件不存在: {path}")
                sys.exit(1)
            player = import_team(path, factory)
            print(f"导入队伍: {player.name} ({len(player.team)} 精灵)")
            for s in player.team:
                print(f"  {s.name} HP={s.current_hp}/{s.max_hp} E={s.energy}")
        elif args.type == "match":
            name = args.name
            path = _EXPORT_DIR / f"{name}.roco-match.json"
            if not path.exists():
                print(f"文件不存在: {path}")
                sys.exit(1)
            battle = import_match(path, factory)
            print(f"导入对局: {battle.player_a.name} vs {battle.player_b.name}")
            print(f"  回合: {battle.turn}")
            print(f"  天气: {battle.globals.weather or '无'}")
            print(f"  日志: {len(battle.log)} 条回合记录")


if __name__ == "__main__":
    _main()
```

- [ ] **Step 2: 运行测试**

Run: `pytest backend/engine/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add roco/serializer.py
git commit -m "feat: 添加 CLI 导入导出工具 (roco.serializer)"
```

---

### Task 9: 往返测试

**Files:**
- Create: `backend/engine/test_serializer.py`

- [ ] **Step 1: 编写效果序列化往返测试**

```python
"""backend/engine/test_serializer.py — 序列化往返测试"""

import pytest


class TestEffectSerialization:
    """Effect to_dict → from_dict round-trip tests."""

    def test_stat_buff_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import StatBuffEffect

        original = StatBuffEffect(
            name="atk", source="专注力", scope="battlefield",
            stat_key="atk", steps=3, display_mult=0.3,
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, StatBuffEffect)
        assert restored.name == "atk"
        assert restored.stat_key == "atk"
        assert restored.steps == 3
        assert restored.display_mult == 0.3
        assert restored.source == "专注力"

    def test_abnormal_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import AbnormalEffect

        original = AbnormalEffect(
            name="灼烧", source="skill", scope="battlefield",
            stacks=3, tick_damage_pct=0.03, tick_element="火",
            max_stacks=10,
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, AbnormalEffect)
        assert restored.name == "灼烧"
        assert restored.stacks == 3
        assert restored.tick_damage_pct == 0.03

    def test_state_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import StateEffect

        original = StateEffect(
            name="charging", source="skill", scope="turn",
            state_type="charging", params={"skill": "火焰拳"},
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, StateEffect)
        assert restored.state_type == "charging"
        assert restored.params == {"skill": "火焰拳"}

    def test_modifier_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import ModifierEffect

        original = ModifierEffect(
            name="max_energy", source="trait", attr="max_energy",
            value=12.0, mode="set",
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, ModifierEffect)
        assert restored.attr == "max_energy"
        assert restored.value == 12.0

    def test_mark_roundtrip(self):
        from backend.engine.serializer import effect_from_dict, effect_to_dict
        from backend.vm.effect import MarkEffect

        original = MarkEffect(
            name="星陨印记", source="星陨", scope="persistent",
            stacks=5, category="negative", starfall_damage=30,
        )
        d = effect_to_dict(original)
        restored = effect_from_dict(d)
        assert isinstance(restored, MarkEffect)
        assert restored.name == "星陨印记"
        assert restored.stacks == 5
        assert restored.starfall_damage == 30
```

- [ ] **Step 2: 运行测试验证**

Run: `pytest backend/engine/test_serializer.py -v --tb=short`
Expected: 5 passed

- [ ] **Step 3: 编写完整 Battle 往返测试**

追加到 `backend/engine/test_serializer.py`:

```python
class TestBattleSerialization:
    """Full battle to_dict → from_dict round-trip."""

    @pytest.fixture
    def factory(self):
        from backend.sim.factory import SimFactory
        return SimFactory()

    @pytest.fixture
    def battle(self, factory):
        """创建一个简单对局 (A: 大头骨龙, B: 蹦蹦草)。"""
        p1 = factory.build_player("训练师A", [
            {"name": "大头骨龙", "skills": ["火焰拳", "龙之怒", "火花", "蓄力"]},
        ])
        p2 = factory.build_player("训练师B", [
            {"name": "蹦蹦草", "skills": ["飞叶快刀", "藤鞭", "寄生种子", "光合作用"]},
        ])
        return factory.build_battle(p1, p2)

    def test_battle_roundtrip(self, battle, factory):
        from backend.engine.serializer import battle_from_dict, battle_to_dict

        data = battle_to_dict(battle)
        restored = battle_from_dict(data, factory.sprite_db, factory._build_skill_list)

        assert restored.turn == battle.turn
        assert restored.player_a.name == battle.player_a.name
        assert restored.player_b.name == battle.player_b.name
        assert restored.globals.weather == battle.globals.weather
        assert len(restored.player_a.team) == len(battle.player_a.team)
        assert restored.player_a.team[0].name == battle.player_a.team[0].name
        assert restored.player_a.team[0].current_hp == battle.player_a.team[0].current_hp

    def test_battle_roundtrip_after_turn(self, battle, factory):
        """执行一回合后序列化往返。"""
        from backend.engine.serializer import battle_from_dict, battle_to_dict
        from roco.ai.agent import RandomAgent

        agent_a = RandomAgent()
        agent_b = RandomAgent()
        battle.execute_turn(agent_a, agent_b)

        data = battle_to_dict(battle)
        restored = battle_from_dict(data, factory.sprite_db, factory._build_skill_list)

        assert restored.turn == battle.turn
        assert len(restored.log) == len(battle.log)
        # 日志内容应一致
        assert restored.log[0].turn == battle.log[0].turn
        assert restored.log[0].sprite_a == battle.log[0].sprite_a

    def test_snapshot_and_restore(self, battle, factory):
        """快照回溯：拍快照 → 执行几回合 → 恢复 → 状态一致。"""
        from roco.ai.agent import RandomAgent

        agent_a, agent_b = RandomAgent(), RandomAgent()

        # 保存回合0快照
        battle.save_snapshot()
        snap0 = battle.snapshots[0]

        # 执行5回合
        for _ in range(5):
            battle.execute_turn(agent_a, agent_b)

        assert battle.turn == 5

        # 恢复到回合0
        battle.restore_snapshot(0)

        assert battle.turn == 0
        assert len(battle.log) == 0
        # 精灵HP应恢复
        assert battle.player_a.team[0].current_hp == battle.player_a.team[0].max_hp
```

- [ ] **Step 4: 运行完整测试套件**

Run: `pytest backend/engine/test_serializer.py -v --tb=short`
Expected: 8 passed

- [ ] **Step 5: 运行全部现有测试确保无回归**

Run: `pytest backend/ -x --tb=short -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add backend/engine/test_serializer.py
git commit -m "test: 添加序列化往返测试 + 回溯快照测试"
```

---

### 最终验证

- [ ] Run `pytest backend/ -x --tb=short` — 全部通过
- [ ] 验证 CLI: `python -m roco.serializer import team <test_name>`（需先有导出文件）
- [ ] 手动测试回溯流程: `battle.execute_turn()` → `battle.restore_snapshot(0)` → 再次 `execute_turn()`
