# -*- coding: utf-8 -*-
"""ab_net_vs_net.py — 两个权重直接对打（双方都开搜索），只跑一臂。

为什么要独立工具：`probe_reward_ab --only ckpt` 会跑两臂，且两臂参数相同
（gate_games == hi_games 时是同一次测量，容易误读成两个证据 ✗）。

分辨率参考（配对协议，成对交换先后手）：
    300 局 ≈ ±5.7 pt｜600 局 ≈ ±4 pt｜1200 局 ≈ ±2.9 pt
sims=100 每局约 4× 便宜于 sims=400 —— 对"候选 vs 起点"这种 1~5 pt 的效应，
**降 sims、加局数**是严格更优的协议（双方同等削弱，比的是相对强弱）。

用法（本地）:
  python -X utf8 native/tools/ab_net_vs_net.py --a checkpoints/it1.pt --b checkpoints/v5.pt \
      --games 1200 --sims 100 --workers 14 --json-out logs/ab_it1_v5.json
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
if not (ROOT / "backend").is_dir():
    ROOT = Path(r"D:\projects\Roco-LittleNine")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="候选权重")
    ap.add_argument("--b", required=True, help="对手权重（基准）")
    ap.add_argument("--games", type=int, default=1200)
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--eval-max-turns", type=int, default=150)
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--seed", type=int, default=20260927)
    ap.add_argument("--device", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    import torch

    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import (
        _load_sprite_skills, evaluate_parallel,
    )
    from backend.sim.factory import SimFactory

    dev = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    net_a = ModularBattleNet.load(args.a, device=dev)
    net_b = ModularBattleNet.load(args.b, device=dev)
    factory = SimFactory()
    skills = _load_sprite_skills()

    random.seed(args.seed)
    t0 = time.time()
    print(f"[A/B] {Path(args.a).name} vs {Path(args.b).name}｜{args.games} 局｜"
          f"sims={args.sims}｜max_turns={args.eval_max_turns}｜{args.workers} workers", flush=True)
    wr = evaluate_parallel(
        net_a, net_b, factory, skills,
        n_games=args.games, num_workers=args.workers, device=dev,
        inference_batch_size=256, inference_timeout_ms=5.0,
        num_simulations=args.sims, max_turns=args.eval_max_turns,
        draw_margin=0.15, progress_every=max(1, args.games // 12),
        stall_timeout_s=1200.0, leaf_batch_size=16,
        candidate_leaf_weight=1.0, best_leaf_weight=1.0,
    )
    dt = time.time() - t0
    half = 1.96 * (0.25 / max(args.games, 1)) ** 0.5
    print(f"[A/B] {Path(args.a).name} 得分 {wr:.4f}（±{half:.3f}，{args.games} 局，"
          f"{dt / 60:.1f} 分钟）", flush=True)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "a": args.a, "b": args.b, "games": args.games, "sims": args.sims,
            "score_a": wr, "ci95_half": half, "minutes": round(dt / 60, 1),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[A/B] 写出 {args.json_out}", flush=True)


if __name__ == "__main__":
    main()
