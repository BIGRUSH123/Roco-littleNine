# -*- coding: utf-8 -*-
"""probe_reward_ab.py — 验证"自博弈没效果"的两个结构性原因（在远端跑）。

背景与判据（2026-09-23）：
  缺口1 搜索吃的奖励是**手写启发式**（`--leaf-value-weight` CLI 默认 1.0 → V_leaf = tanh(0.5·state_value)），
        网络价值头被旁路 → 策略目标的天花板 = 那个静态函数。**判据**：同一网络下，w=1（启发式）
        与 w=0（纯网络价值）/ w=0.5（混合）对打，若 w=1 并没有更强，则"用启发式"不带来强度，
        自博弈也就无从超越它。
  缺口2 门控（150 局、阈值 0.55）分辨率不足 → 真实的小提升也会被回滚。**判据**：把一个已知
        有小幅差距的换手（BC 10k vs BC 30k）分别过 150 局门控协议与 600 局高分辨率评估，
        比较"门控的判决"与"高分辨率估计"是否一致。

实验台：`train.evaluate_parallel`（门控用的就是它）。它按 `_EVAL_ROSTER_SEED` 生成**固定阵容套件**、
逐局用局号播种 —— 所以**各臂打的是同一批 200/150 局**，差异只能归因于被测变量。相邻两局交换候选
所在侧，先手偏置自动抵消。

用法（远端）:
  python -X utf8 native/tools/probe_reward_ab.py --only null,leaf,ckpt
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="new_10000.pt", help="主权重（缺口1/2 都用它）")
    ap.add_argument("--ckpt-other", default="t_g30000.pt", help="缺口2 的对照权重（10k vs 30k BC）")
    ap.add_argument("--only", default="null,leaf,leaf_blend,ckpt", help="要跑哪几臂")
    ap.add_argument("--games", type=int, default=200, help="A/B 臂局数（成对，样本数偶数）")
    ap.add_argument("--gate-games", type=int, default=150, help="门控协议局数（缺口2）")
    ap.add_argument("--hi-games", type=int, default=600, help="高分辨率评估局数（缺口2）")
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--max-turns", type=int, default=150)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--leaf-batch-size", type=int, default=128)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--sims-arms", default="100,400",
                    help="搜索 vs 策略那一臂要测的 sims 档位（逗号分隔）")
    ap.add_argument("--json-out", default="")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    import torch

    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import _load_sprite_skills, evaluate_parallel
    from backend.sim.factory import SimFactory

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    main_net = ModularBattleNet.load(args.ckpt, device=dev)
    factory = SimFactory()
    skills = _load_sprite_skills()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    results: list[dict] = []

    def run(tag: str, net_c, net_b, w_c: float, w_b: float, games: int,
            sims_c: int | None = None, sims_b: int | None = None) -> float:
        random.seed(args.seed)          # 固定 worker 播种 → 与阵容套件一起保证可复现
        t0 = time.time()
        print(f"\n[{tag}] candidate(w={w_c}, sims={sims_c or args.sims}) vs "
              f"best(w={w_b}, sims={sims_b or args.sims})  {games} 局", flush=True)
        wr = evaluate_parallel(
            net_c, net_b, factory, skills,
            n_games=games, num_workers=args.workers, device=dev,
            inference_batch_size=256, inference_timeout_ms=5.0,
            num_simulations=args.sims, max_turns=args.max_turns, draw_margin=0.15,
            progress_every=max(1, games // 8), stall_timeout_s=1200.0,
            leaf_batch_size=args.leaf_batch_size,
            candidate_leaf_weight=w_c, best_leaf_weight=w_b,
            candidate_sims=sims_c, best_sims=sims_b,
        )
        dt = time.time() - t0
        print(f"[{tag}] 得分 {wr:.4f}  用时 {dt/60:.1f} 分钟", flush=True)
        results.append({"arm": tag, "cand_leaf": w_c, "best_leaf": w_b,
                        "cand_sims": sims_c or args.sims, "best_sims": sims_b or args.sims,
                        "games": games, "score": wr, "minutes": round(dt / 60, 1)})
        return wr

    # 臂0：零假设 —— 同权重同网络（应≈0.50；顺带洗掉"门控本身有偏"的可能）
    if "null" in only:
        run("null(同权重同网络, 门控协议)", main_net, main_net, 1.0, 1.0, args.gate_games)

    # 臂1/2：缺口1 —— 启发式 vs 纯网络价值 / 混合
    if "leaf" in only:
        run("leaf0(纯网络) vs leaf1(启发式)", main_net, main_net, 0.0, 1.0, args.games)
    if "leaf_blend" in only:
        run("leaf05(混合) vs leaf1(启发式)", main_net, main_net, 0.5, 1.0, args.games)

    # 臂3：缺口2 —— 门控协议 vs 高分辨率（同一对权重）
    if "ckpt" in only:
        other = ModularBattleNet.load(args.ckpt_other, device=dev) if args.ckpt_other else None
        if other is not None:
            run(f"gate(150) {args.ckpt} vs {args.ckpt_other}", main_net, other, 1.0, 1.0, args.gate_games)
            run(f"hi(600)   {args.ckpt} vs {args.ckpt_other}", main_net, other, 1.0, 1.0, args.hi_games)

    # 臂4：缺口1 的正面检验 —— 搜索 vs 自己的策略头（同网络，sims=100 vs sims=1）
    # sims=1 时只有一个子节点被访问 → 选择≈先验 argmax（等价策略贪心）。若≈0.5，
    # 说明"搜索比策略强"这个自博弈的前提不成立，数据里没有可学的增量。
    if "sims" in only:
        for s_n in [int(x) for x in str(args.sims_arms).split(",") if x.strip()]:
            run(f"search({s_n} sims) vs policy(1 sim)", main_net, main_net, 1.0, 1.0,
                args.games, sims_c=s_n, sims_b=1)

    print("\n=== 汇总 ===")
    for r in results:
        lo = 0.5 - 1.96 * (0.25 / max(1, r["games"])) ** 0.5
        hi = 0.5 + 1.96 * (0.25 / max(1, r["games"])) ** 0.5
        print(f"  {r['arm']:<44s} 得分 {r['score']:.4f}  "
              f"（±95%CI 半宽≈{(hi-lo)/2:.3f}，{r['games']} 局，{r['minutes']} 分钟）")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        print(f"  写出 {args.json_out}")


if __name__ == "__main__":
    main()
