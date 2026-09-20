"""bench_selfplay_pool — 自博弈吞吐基准：队列拓扑 vs 直连 CUDA 评估器。

复刻 exp13 训练拓扑（16 worker + BatchedInferenceServer batch128/5ms + CUDA），
对比三种配置的聚合决策吞吐（decisions/s ≈ 训练 samples/s）：

  queue-rust   队列拓扑 + rust 引擎自博弈（py_selfplay_game）—— 当前架构上限
  direct-rust  每 worker 进程内直连 CUDA 评估器 + rust 引擎自博弈 —— 场景3
  direct-py    每 worker 进程内直连 CUDA 评估器 + py 引擎自博弈 —— 今天即可
               通过 evaluator.py env 门控接入真实训练（无需改只读文件）

用法：env\\python.exe -X utf8 native/tools/bench_selfplay_pool.py
      [--workers 16] [--games 3] [--sims 100] [--leaf 16 64] [--device cuda]
结果写 native/tools/_bench_pool_last.txt（UTF-8）。
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
CKPT = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
OUT = Path(__file__).parent / "_bench_pool_last.txt"
LINES: list[str] = []


def log(msg: str) -> None:
    LINES.append(msg)
    print(msg, flush=True)


def _load_spec(base_seed: int) -> dict:
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    spec["seed"] = base_seed
    return spec


def _rust_cfg(sims: int, leaf: int) -> str:
    return json.dumps({
        "num_simulations": sims, "root_noise": 0.25, "max_turns": 60,
        "opp_greedy": True, "leaf_batch_size": leaf, "temperature": 1.0,
    }, ensure_ascii=False)


class _RustAdapter:
    """rust PyEvaluator 适配：list 化 torch 评估输出。"""

    def __init__(self, ev):
        import numpy as np
        self._np = np
        self._ev = ev

    def evaluate_batch(self, states, masks):
        import numpy as np
        values, priors = self._ev.evaluate_batch(states, masks)
        return (
            [float(v) for v in np.asarray(values).ravel()],
            np.asarray(priors, dtype=np.float32).tolist(),
        )


def _worker_direct_rust(wid, ckpt, device, spec, cfg, n_games, torch_threads, out_q):
    os.environ["OMP_NUM_THREADS"] = str(torch_threads)
    import torch
    torch.set_num_threads(torch_threads)
    import roco_engine
    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.model import ModularBattleNet

    model = ModularBattleNet.load(str(ckpt), device="cpu").to(device)
    model.eval()
    ev = _RustAdapter(TorchEvaluator(model, device))
    n = 0
    for g in range(n_games):
        r = roco_engine.py_selfplay_game(json.dumps(spec, ensure_ascii=False), cfg, ev, ev)
        n += len(r["P"]) if hasattr(r["P"], "__len__") else 0
    out_q.put((wid, n))


def _worker_direct_py(wid, ckpt, device, spec, n_games, torch_threads, out_q,
                      leaf=16, sims=100):
    os.environ["OMP_NUM_THREADS"] = str(torch_threads)
    import random

    import numpy as np
    import torch
    torch.set_num_threads(torch_threads)
    from backend.engine.ai.core.evaluator import TorchEvaluator
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import _load_sprite_skills, _play_one_rl_battle
    from backend.sim.factory import SimFactory

    model = ModularBattleNet.load(str(ckpt), device="cpu").to(device)
    model.eval()
    ev = TorchEvaluator(model, device)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    n = 0
    for g in range(n_games):
        random.seed(spec["seed"] + 1)
        np.random.seed((spec["seed"] + 1) % (2**32 - 1))
        _states, probs, _masks, _out, _reason, _summary = _play_one_rl_battle(
            factory, sprite_skills, ev, sims, 60, 1.0, 0.25,
            draw_margin=0.15, game_timeout_s=1e9, gamma=1.0, tanh_k=0.0,
            leaf_batch_size=leaf, mirror=False,
        )
        n += len(probs)
    out_q.put((wid, n))


def _worker_queue_rust(wid, ckpt, device, spec, cfg, n_games, torch_threads,
                       request_q, reply_q, out_q):
    os.environ["OMP_NUM_THREADS"] = str(torch_threads)
    import roco_engine
    from backend.engine.ai.core.evaluator import QueuePolicyEvaluator

    qev = QueuePolicyEvaluator(wid, request_q, reply_q)
    ev = _RustAdapter(qev)
    n = 0
    for g in range(n_games):
        r = roco_engine.py_selfplay_game(json.dumps(spec, ensure_ascii=False), cfg, ev, ev)
        n += len(r["P"]) if hasattr(r["P"], "__len__") else 0
    out_q.put((wid, n))


def _worker_queue_py(wid, ckpt, device, spec, n_games, torch_threads, out_q,
                     leaf=16, sims=100, request_q=None, reply_q=None):
    os.environ["OMP_NUM_THREADS"] = str(torch_threads)
    import random

    import numpy as np
    from backend.engine.ai.core.evaluator import QueuePolicyEvaluator
    from backend.engine.ai.core.model import ModularBattleNet
    from backend.engine.ai.train import _load_sprite_skills, _play_one_rl_battle
    from backend.sim.factory import SimFactory

    model = ModularBattleNet.load(str(ckpt), device="cpu").to(device)
    model.eval()
    qev = QueuePolicyEvaluator(wid, request_q, reply_q)
    factory = SimFactory()
    sprite_skills = _load_sprite_skills()
    n = 0
    for g in range(n_games):
        random.seed(spec["seed"] + 1)
        np.random.seed((spec["seed"] + 1) % (2**32 - 1))
        _states, probs, _masks, _out, _reason, _summary = _play_one_rl_battle(
            factory, sprite_skills, qev, sims, 60, 1.0, 0.25,
            draw_margin=0.15, game_timeout_s=1e9, gamma=1.0, tanh_k=0.0,
            leaf_batch_size=leaf, mirror=False,
        )
        n += len(probs)
    out_q.put((wid, n))


def _run_pool(mode, workers, games, sims, leaf, device, torch_threads, base_seed):
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    spec = _load_spec(base_seed)
    cfg = _rust_cfg(sims, leaf)
    warm = 1

    if mode in ("queue-rust", "queue-py"):
        import torch as _t
        from backend.engine.ai.core.evaluator import (
            BatchedInferenceServer,
            SyncPickleQueue,
        )
        from backend.engine.ai.core.model import ModularBattleNet

        model = ModularBattleNet.load(str(CKPT), device=device)
        model.eval()
        request_q = SyncPickleQueue(maxsize=4, ctx=ctx)
        reply_qs = {w: ctx.Queue() for w in range(workers)}
        server = BatchedInferenceServer(model, device, request_q,
                                        {w: reply_qs[w] for w in range(workers)},
                                        batch_size=256, timeout_ms=5.0)
        server.start()
        if mode == "queue-rust":
            procs = [
                ctx.Process(target=_worker_queue_rust, args=(
                    w, CKPT, device, spec, cfg, warm + games, torch_threads,
                    request_q, reply_qs[w], out_q))
                for w in range(workers)
            ]
        else:
            procs = [
                ctx.Process(target=_worker_queue_py, args=(
                    w, CKPT, device, spec, warm + games, torch_threads, out_q,
                    leaf, sims, request_q, reply_qs[w]))
                for w in range(workers)
            ]
    elif mode == "direct-rust":
        procs = [
            ctx.Process(target=_worker_direct_rust, args=(
                w, CKPT, device, spec, cfg, warm + games, torch_threads, out_q))
            for w in range(workers)
        ]
    elif mode == "direct-py":
        procs = [
            ctx.Process(target=_worker_direct_py, args=(
                w, CKPT, device, spec, warm + games, torch_threads, out_q, leaf, sims))
            for w in range(workers)
        ]
    else:
        raise ValueError(mode)

    t0 = time.perf_counter()
    for p in procs:
        p.start()
    got = [out_q.get() for _ in range(workers)]
    wall = time.perf_counter() - t0
    for p in procs:
        p.join()
    if mode == "queue-rust":
        server.stop()

    total = sum(n for _, n in got)
    # warmup 已含在 wall 内，按比例扣除
    frac = games / (warm + games)
    eff = total * frac / wall
    return wall, total, eff


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--device", type=str, default="cuda")
    ap.add_argument("--torch-threads", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--games", type=int, default=2)
    ap.add_argument("--configs", type=str, default=(
        "direct-rust:128:16,direct-rust:256:16,direct-rust:128:8,"
        "direct-py:64:16,direct-py:128:16"),
        help="逗号分隔 模式:leaf:workers，模式 ∈ queue-rust|direct-rust|direct-py")
    args = ap.parse_args()

    log(f"sims={args.sims} device={args.device} torch_threads/worker={args.torch_threads}")
    log(f"configs={args.configs}")

    results = {}
    for spec_str in args.configs.split(","):
        mode, leaf, workers = spec_str.strip().split(":")
        leaf, workers = int(leaf), int(workers)
        wall, total, eff = _run_pool(mode, workers, args.games, args.sims,
                                     leaf, args.device, args.torch_threads, args.seed)
        tag = f"{mode} leaf={leaf} w={workers}"
        log(f"[{tag}] wall={wall:.1f}s decisions={total} 吞吐={eff:.1f} decisions/s")
        results[tag] = eff

    base = results.get("queue-rust leaf=16 w=16")
    for k, v in results.items():
        rel = f" = {v / base:.2f}x 基线" if base and k != "queue-rust leaf=16 w=16" else ""
        log(f"    {k}: {v:.1f}/s{rel}")
    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
