# -*- coding: utf-8 -*-
"""probe_decision_cost.py — 把"单次决策耗时"按阶段拆开，找出跨对局的差异来源。

一局的一次决策（MCTS）由这些阶段组成，本探针从外部给每一处套计时器：
    save/restore_mutable_state  —— 状态浅拷贝（回滚用）
    execute_turn_headless       —— 引擎走一回合（效果解析/观察者派发/回合末 tick）
    encode_battle_state         —— 叶子编码（含 AST 词法化，长度随技能/效果复杂度变）
    evaluator.evaluate(_batch)  —— 网络前向
    state_value                 —— 叶节点附加估值（leaf_value_fn）

用法（远端）:
  python -X utf8 native/tools/probe_decision_cost.py --ckpt new_10000.pt \
      --games 200 --sims 100 --indices 136-199 --budget-s 180 --verbose-from 40
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "backend").is_dir():
    ROOT = Path(r"D:\projects\Roco-LittleNine")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

ACC: dict[str, float] = defaultdict(float)
CALLS: dict[str, int] = defaultdict(int)
MAXLEN = {"ast_len": 0}


def parse_indices(spec: str) -> set[int] | None:
    if not spec.strip():
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            out.update(range(lo, hi + 1))
        elif part:
            out.add(int(part))
    return out


def install() -> None:
    from backend.engine.ai.core import encoder as enc
    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.sim import battle as battle_mod

    def wrap(target, attr, key):
        orig = getattr(target, attr)

        def timed(*a, **k):
            t0 = time.perf_counter()
            try:
                return orig(*a, **k)
            finally:
                ACC[key] += time.perf_counter() - t0
                CALLS[key] += 1
        setattr(target, attr, timed)

    wrap(battle_mod.Battle, "save_mutable_state", "保存快照")
    wrap(battle_mod.Battle, "restore_mutable_state", "恢复快照")
    wrap(battle_mod.Battle, "execute_turn_headless", "引擎回合")
    wrap(enc, "encode_battle_state", "状态编码")
    # `from ... import encode_battle_state` 已经把引用绑进各自模块命名空间，
    # 只改 encoder 模块属性打不中 —— 必须把引用它们的模块也一起换掉。
    for mod_name in ("backend.engine.ai.core.mcts", "backend.engine.ai.train",
                     "backend.engine.ai.core.encoder"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "encode_battle_state"):
            wrap(mod, "encode_battle_state", "状态编码")
    wrap(TorchEvaluator, "evaluate", "网络前向")
    if hasattr(TorchEvaluator, "evaluate_batch"):
        wrap(TorchEvaluator, "evaluate_batch", "网络前向")
    from backend.sim import value as value_mod
    wrap(value_mod, "state_value", "叶节点估值")

    # AST token 长度（编码成本的直接体现）：记录每次编码的长度，看它跨对局怎么变
    orig_collect = enc._collect_ast_token_ids

    def collect(*a, **k):
        r = orig_collect(*a, **k)
        toks = a[2] if len(a) > 2 else None
        if isinstance(toks, list):
            length = len(toks)
            MAXLEN["ast_len"] = max(MAXLEN["ast_len"], length)
            ACC["_ast_sum"] += length
            CALLS["_ast_n"] += 1
        return r
    enc._collect_ast_token_ids = collect


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="new_10000.pt")
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--eval-max-turns", type=int, default=150)
    ap.add_argument("--indices", default="")
    ap.add_argument("--budget-s", type=float, default=180.0)
    ap.add_argument("--verbose-from", type=float, default=40.0)
    args = ap.parse_args()

    import torch

    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import (
        _eval_roster_rng, _load_sprite_skills, _paired_eval_tasks,
        _play_one_eval_game, _seed_eval_game,
    )
    from backend.sim.factory import SimFactory

    install()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    ev = TorchEvaluator(ModularBattleNet.load(args.ckpt, device=dev), dev)
    factory = SimFactory()
    skills = _load_sprite_skills()
    tasks = _paired_eval_tasks(factory, skills, args.games, rng=_eval_roster_rng())
    wanted = parse_indices(args.indices)
    print(f"套件 {args.games}｜sims={args.sims}｜上限 {args.budget_s:.0f}s｜"
          f"测 {len(wanted) if wanted else '全部'} 局", flush=True)

    order = ("引擎回合", "保存快照", "恢复快照", "状态编码", "网络前向", "叶节点估值")
    for g, matchup in tasks:
        if wanted is not None and g not in wanted:
            continue
        for k in ACC:
            ACC[k] = 0.0
        for k in CALLS:
            CALLS[k] = 0
        MAXLEN["ast_len"] = 0
        _seed_eval_game(g)
        stats: dict = {}
        t0 = time.time()
        score = _play_one_eval_game(
            factory, skills, ev, ev, g, args.sims, args.eval_max_turns, 0.15,
            args.budget_s, 128, matchup=matchup,
            candidate_leaf_weight=1.0, best_leaf_weight=1.0, stats_out=stats,
        )
        dt = time.time() - t0
        names = " / ".join(x.get("name", "?") for x in matchup[0])[:30]
        total_acc = sum(v for k, v in ACC.items() if not k.startswith("_")) or 1.0
        slow = dt >= args.verbose_from
        print(f"  局 {g:>4} {dt:7.1f}s 回合 {stats.get('turns', -1):>3} "
              f"{stats.get('end', '?'):<9} 得分 {score:.2f} 决策 {CALLS['引擎回合']} "
              f"AST均值{CALLS['_ast_n'] and int(ACC['_ast_sum'] / max(CALLS['_ast_n'], 1)) or 0}"
              f"/最大{MAXLEN['ast_len']} {names}{'  <<<' if slow else ''}", flush=True)
        if slow:
            print("        阶段占比: " + "  ".join(
                f"{k} {ACC[k] / total_acc * 100:4.1f}%({ACC[k]:6.1f}s/{CALLS[k]})"
                for k in order), flush=True)


if __name__ == "__main__":
    main()
