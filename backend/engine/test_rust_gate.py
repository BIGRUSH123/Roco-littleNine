"""阶段3 验收门：Python oracle 与 Rust 引擎双跑同 spec 对局，逐回合状态摘要 0 分歧。

种子协议：spec.seed → 阵容生成（已固化在 spec 内）；
对局内 RNG = seed+1（Python random.seed(seed+1) / Rust PyRandom(seed+1)）。
"""

from __future__ import annotations

import glob
import json
import random
import sys

import pytest

sys.path.insert(0, ".")

try:
    import roco_engine
except ImportError:
    pytest.skip("roco_engine 未编译（maturin develop --release）", allow_module_level=True)

from backend.sim.battle import Battle
from backend.sim.battleskill import BattleSkill
from backend.sim.player import Item, Player
from backend.sim.skill import Skill
from backend.sim.sprite import Sprite
from backend.sim.factory import SimFactory
from backend.sim.agent import RuleAgent
from backend.vm.effect import AbnormalEffect, StatBuffEffect

SPEC_DIR = "native/gate_specs"


def _spec_files() -> list[str]:
    return sorted(glob.glob(f"{SPEC_DIR}/spec_*.json"))


def battle_from_spec(spec: dict) -> Battle:
    players: list[Player] = []
    for ps in spec["players"]:
        sprites: list[Sprite] = []
        for ss in ps["sprites"]:

            from backend.common.models import SpeciesStats

            raw_species = {k: v for k, v in ss.items() if k in
                           ("name", "number", "attributes", "bloodline", "ability",
                            "ability_id", "pre_species", "bloodline_skills")}
            raw_species["bloodline_skills"] = dict(raw_species.get("bloodline_skills") or {})
            species = SpeciesStats(**raw_species)
            skill_objs = []
            for raw in ss["skills"]:
                d = {k: v for k, v in raw.items() if k != "effects"}
                skill_objs.append(BattleSkill(base=Skill.load(d)))
            sprite = Sprite(
                species=species,
                initial_stats=dict(ss["initial_stats"]),
                nature=ss.get("nature"),
                iv=dict(ss.get("iv") or {}),
                current_hp=ss["initial_stats"]["hp"],
                max_hp=ss["initial_stats"]["hp"],
                energy=ss.get("energy", 10),
                skills=skill_objs,
            )
            sprite.bloodline = ss.get("bloodline", "")
            sprite.bloodline_skills = dict(ss.get("bloodline_skills") or {})
            sprites.append(sprite)
        item_d = ps.get("item")
        item = Item(
            name=item_d["name"], max_uses=item_d["max_uses"],
            cooldown_turns=item_d.get("cooldown_turns", 0),
        ) if item_d else None
        players.append(Player(name=ps["name"], team=sprites, lives=ps.get("lives", 4), item=item))
    battle = Battle(player_a=players[0], player_b=players[1], weather=spec.get("weather", ""))
    factory = SimFactory()
    battle.species_db = factory.sprite_db
    battle.skill_loader = factory._build_skill_list
    battle.list_all_skill_names = factory._list_all_skill_names
    battle.skill_element_map = factory._skill_element_map
    return battle


def gate_digest(battle: Battle) -> dict:
    """与 Rust turn::state_digest 同构的逐回合摘要。"""
    from backend.vm.effect import EffectObject

    def sprite_d(s) -> dict:
        effs = []
        for e in s.active_effects:
            if isinstance(e, StatBuffEffect):
                n = e.steps
            elif isinstance(e, AbnormalEffect):
                n = e.stacks
            else:
                n = 0
            effs.append([e.name, e.scope, n])
        return {
            "name": s.name,
            "hp": s.current_hp,
            "max_hp": s.max_hp,
            "energy": s.energy,
            "entry_turn": s.entry_turn,
            "charging": getattr(s, "_charging", False),
            "first_action": s.first_action,
            "effects": effs,
            "modifiers": dict(sorted(s._modifiers.items())),
            "skills": [
                {"name": sk.name, "cooldown": sk.cooldown, "sealed": sk.sealed,
                 "modifiers": dict(sorted(sk._modifiers.items()))}
                for sk in s.skills
            ],
        }

    def player_d(p) -> dict:
        return {
            "lives": p.lives,
            "active_index": p.active_index,
            "devotion": dict(sorted(p.devotion.items())),
            "sprites": [sprite_d(s) for s in p.team],
        }

    g = battle.globals
    return {
        "turn": battle.turn,
        "winner": battle.winner,
        "weather": g.weather,
        "weather_turns": g.weather_turns,
        "players": [player_d(battle.player_a), player_d(battle.player_b)],
        "marks": {
            "A": [[m.name, m.stacks] for m in g.mark_effects.get("A", [])],
            "B": [[m.name, m.stacks] for m in g.mark_effects.get("B", [])],
        },
        "team_counters": {
            "A": dict(sorted(battle.team_counters.get("A", {}).items())),
            "B": dict(sorted(battle.team_counters.get("B", {}).items())),
        },
        "counter_values": dict(sorted(battle._vm_engine._counter_values.items())),
    }


def run_python(spec: dict):
    random.seed(spec["seed"] + 1)
    battle = battle_from_spec(spec)
    a = RuleAgent("A", battle.player_a)
    b = RuleAgent("B", battle.player_b)
    battle.player_a.active_index = a.choose_lead(battle)
    battle.player_b.active_index = b.choose_lead(battle)
    battle._invalidate_ctx_team_cache()
    digests = [gate_digest(battle)]
    while not battle.is_finished:
        battle.execute_turn(a, b)
        digests.append(gate_digest(battle))
    return digests, battle.winner


def run_rust(spec: dict):
    spec_json = json.dumps(spec, ensure_ascii=False)
    result = json.loads(roco_engine.py_run_battle(spec_json))
    turns = []
    for d in result["turns"]:
        turns.append(_rust_norm(d))
    return turns, result["winner"] or None


def _rust_norm(d: dict) -> dict:
    """Rust digest JSON → 与 gate_digest 同构。"""
    out = dict(d)
    out["winner"] = d.get("winner") or None
    return out


@pytest.mark.parametrize('spec_path', _spec_files())
def test_full_battle_parity(spec_path: str) -> None:
    spec = json.loads(open(spec_path, encoding="utf-8").read())
    py_digests, py_winner = run_python(spec)
    rust_digests, rust_winner = run_rust(spec)
    assert py_winner == rust_winner, (
        f"{spec_path}: 胜者不一致 {py_winner} vs {rust_winner}"
    )
    assert len(py_digests) == len(rust_digests), (
        f"{spec_path}: 回合数不一致 {len(py_digests)} vs {len(rust_digests)}"
    )
    for i, (pd, rd) in enumerate(zip(py_digests, rust_digests)):
        if pd != rd:
            # 找出首个差异字段
            diff = _first_diff(pd, rd)
            pytest.fail(f"{spec_path} turn#{i} 分歧: {diff}")


def _first_diff(a, b, path="") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return f"{path}.{k} 仅 Rust 有: {b[k]!r}"
            if k not in b:
                return f"{path}.{k} 仅 Python 有: {a[k]!r}"
            r = _first_diff(a[k], b[k], f"{path}.{k}")
            if r:
                return r
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path} 长度 {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = _first_diff(x, y, f"{path}[{i}]")
            if r:
                return r
        return ""
    if a != b:
        return f"{path}: py={a!r} rust={b!r}"
    return ""
