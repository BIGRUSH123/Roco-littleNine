"""backend/engine/ai/rust_selfplay_hook.py — 自博弈 worker 的 rust 引擎挂钩（env 门控）。

背景：selfplay_worker.py / train.py 在本次重构中为只读文件。本模块让 worker
在环境变量开启时改走 rust 引擎自博弈（py_selfplay_game）与进程内直连评估器，
无需改动这两个文件。

挂载机制：worker 进程在模块顶层 `from backend.engine.ai.core.evaluator import
QueuePolicyEvaluator` 时（早于其函数体内延迟 `from ...train import
_play_one_rl_battle`），evaluator 模块底部调用 install_if_enabled()；
此时在 worker 进程内完整导入 train 并替换模块属性 `_play_one_rl_battle`，
worker 随后的延迟导入即取到 shim。主进程 train.py 自己 import evaluator
发生在其定义 _play_one_rl_battle 之前，随后 def 覆盖本补丁 → 主进程不受影响。

环境变量：
  ROCO_SELFPLAY_GAME=rust     启用 rust 引擎自博弈（本模块 patch）
  ROCO_SELFPLAY_EVAL=direct   QueuePolicyEvaluator → 进程内直连 TorchEvaluator
                              （配合 ROCO_SELFPLAY_MODEL / ROCO_SELFPLAY_DEVICE /
                               ROCO_SELFPLAY_TORCH_THREADS，见 evaluator.py）

RNG 协议：team/item 选择继续消耗 worker 的 random 流（与 py 训练同种子时
阵容完全一致）；对局内 RNG 由 battle_seed 供给 rust（PyRandom/NpRandom 均
seed+1，与 gate 协议一致），battle_seed 在 team/item 之后从 random 流抽取。
"""
from __future__ import annotations

import copy
import json
import os
import random
import time
from pathlib import Path

import numpy as np

_HOOK_INSTALLED = False


# ═══════════════════════════════════════════════════════════════════
# spec 构建（镜像 native/tools/export_battle_spec.py 的序列化函数）
# ═══════════════════════════════════════════════════════════════════

def _species_dict(ss) -> dict:
    return {
        "name": ss.name,
        "number": ss.number,
        "attributes": ss.attributes,
        "bloodline": ss.bloodline,
        "ability": ss.ability,
        "ability_id": getattr(ss, "ability_id", 0),
        "pre_species": getattr(ss, "pre_species", ""),
        "bloodline_skills": dict(getattr(ss, "bloodline_skills", {}) or {}),
    }


def _leader_forms(species_db, number: str, appearance: str = ""):
    """首领形态候选列表（与 Python 侧动作 17-21 同序）。

    Rust 引擎需要完整候选列表 + 每个候选的六维，才能实现「玩家选形态」的
    道具动作（旧版只传单个 leader_form，多首领家族无法表达）。
    """
    from backend.common.formulas import StatsCalc

    if not number:
        return []
    calc = StatsCalc()
    out = []
    for s in species_db.leader_form_candidates(number, appearance):
        result = calc.compute(s)
        out.append({
            **_species_dict(s),
            "initial_stats": dict(result.final_stats),
            "max_hp": result.final_stats["hp"],
        })
    return out


def _find_leader_form(species_db, number: str):
    """兼容旧接口：返回首个候选（无候选返回 None）。"""
    forms = _leader_forms(species_db, number)
    return forms[0] if forms else None


def _build_sprite_spec(species_db, spec: dict) -> dict:
    from backend.common.formulas import StatsCalc
    from backend.common.constants import STAT_KEYS
    from backend.common.skill_trait_ids import SKILL_ID_TO_NAME

    species = species_db.get(spec["name"], spec.get("form", ""))
    calc = StatsCalc()
    result = calc.compute(
        species,
        nature=spec.get("nature"),
        iv=spec.get("iv") or {k: 0 for k in STAT_KEYS},
    )
    skills_raw = []
    for sk_name in spec.get("skills", []):
        p = Path("data/skills") / f"{sk_name}.json"
        if p.exists():
            skills_raw.append(json.loads(p.read_text(encoding="utf-8")))
    bloodline = spec.get("bloodline") or (species.elements[0] if species.elements else "")
    bloodline_skill = None
    bl_id = (species.bloodline_skills or {}).get(bloodline)
    if bl_id is not None:
        sk_name = SKILL_ID_TO_NAME.get(bl_id)
        if sk_name:
            p = Path("data/skills") / f"{sk_name}.json"
            if p.exists():
                bloodline_skill = json.loads(p.read_text(encoding="utf-8"))
    return {
        **_species_dict(species),
        "initial_stats": dict(result.final_stats),
        "nature": spec.get("nature"),
        "iv": dict(spec.get("iv") or {}),
        "energy": 10,
        "skills": skills_raw,
        "bloodline": bloodline,
        "leader_forms": _leader_forms(species_db, species.number, species.appearance),
        "leader_form": _find_leader_form(species_db, species.number),
        "bloodline_skill": bloodline_skill,
    }


def _build_spec_from_teams(seed: int, factory, team_a, team_b, item_a, item_b) -> dict:
    def player_spec(name, team, item):
        return {
            "name": name,
            "lives": 4,
            "item": {
                "name": item.name,
                "max_uses": item.max_uses,
                "cooldown_turns": item.cooldown_turns,
                "uses": 0,
                "last_use_turn": 0,
            },
            "sprites": [_build_sprite_spec(factory.sprite_db, s) for s in team],
        }

    return {
        "seed": seed,
        "weather": "",
        "players": [
            player_spec("A", team_a, item_a),
            player_spec("B", team_b, item_b),
        ],
    }


# ═══════════════════════════════════════════════════════════════════
# rust 引擎版 _play_one_rl_battle（签名与 train.py 版逐参一致）
# ═══════════════════════════════════════════════════════════════════

class _RustEvalAdapter:
    """把任意 py evaluator（Torch/Queue…）适配为 rust PyEvaluator 接口。"""

    def __init__(self, ev):
        self._ev = ev

    def evaluate_batch(self, states, masks):
        values, priors = self._ev.evaluate_batch(states, masks)
        return (
            [float(v) for v in np.asarray(values).ravel()],
            np.asarray(priors, dtype=np.float32).tolist(),
        )


def _rust_play_one_rl_battle(
    factory,
    sprite_skills: dict[str, list[str]],
    evaluator,
    num_simulations: int,
    max_turns: int,
    temperature: float,
    root_noise: float,
    draw_margin: float = 0.15,
    game_timeout_s: float = 450.0,
    gamma: float = 1.0,
    tanh_k: float = 0.0,
    leaf_batch_size: int = 16,
    mirror: bool = False,
):
    """单局自我博弈（rust 引擎版），返回与 train._play_one_rl_battle 同构的
    (states, probs, masks, outcomes, end_reason, battle_summary)。"""
    import roco_engine

    from backend.engine.ai.train import _random_teams

    # team/item 继续消耗 worker 的 random 流（同种子时与 py 训练选相同阵容）
    team_a, team_b, item_a, item_b = _random_teams(factory, sprite_skills)
    if mirror:
        team_b = copy.deepcopy(team_a)
    battle_seed = random.getrandbits(62)

    spec = _build_spec_from_teams(battle_seed, factory, team_a, team_b, item_a, item_b)
    cfg = json.dumps({
        "num_simulations": num_simulations,
        "root_noise": root_noise,
        "max_turns": max_turns,
        "opp_greedy": True,  # 与 train._play_one_rl_battle 的自博弈语义一致
        "leaf_batch_size": leaf_batch_size,
        "temperature": temperature,
        "draw_margin": draw_margin,
        "gamma": gamma,
        "tanh_k": tanh_k,
    }, ensure_ascii=False)

    battle_started = time.monotonic()
    adapter = _RustEvalAdapter(evaluator)
    r = roco_engine.py_selfplay_game(json.dumps(spec, ensure_ascii=False), cfg, adapter, adapter)
    elapsed = time.monotonic() - battle_started

    winner = r["winner"] or ""
    turns = int(r["turns"])
    if elapsed >= game_timeout_s:
        end_reason = "timeout"
    elif winner == "A":
        end_reason = "decisive_a"
    elif winner == "B":
        end_reason = "decisive_b"
    else:
        prefix = "max_turns" if turns >= max_turns else "stalemate"
        # rust outcome_a=0 即 py 的 |margin|<draw_margin 分支
        end_reason = f"{prefix}_draw" if float(r["outcome_a"]) == 0.0 else prefix
        if float(r["outcome_a"]) != 0.0:
            end_reason = f"{prefix}_a" if float(r["outcome_a"]) > 0 else f"{prefix}_b"

    states = list(r["states"])
    probs = [np.asarray(p, dtype=np.float32) for p in r["P"]]
    masks = [np.asarray(m, dtype=np.float32) for m in r["M"]]
    outcomes = [float(v) for v in r["v"]]
    battle_summary = {
        "teams": [[s["name"] for s in p["sprites"]] for p in spec["players"]],
        "rounds": [],  # rust 路径暂不产回合技能日志
        "winner": winner or "draw",
        "turns": turns,
        "lives": list(r["lives"]),
        "end_reason": end_reason,
    }
    return states, probs, masks, outcomes, end_reason, battle_summary


def install_if_enabled() -> None:
    """evaluator 模块导入时调用：env 开启时替换 worker 内的 _play_one_rl_battle。"""
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED or os.environ.get("ROCO_SELFPLAY_GAME", "") != "rust":
        return
    import backend.engine.ai.train as _train_mod

    _train_mod._play_one_rl_battle = _rust_play_one_rl_battle
    _HOOK_INSTALLED = True
