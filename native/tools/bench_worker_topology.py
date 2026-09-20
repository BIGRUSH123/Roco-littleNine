"""bench_worker_topology — 拓扑忠实基准：真实训练架构下的单 worker 出招节奏。

复刻 train.py 的 selfplay 推理拓扑：本进程 BatchedInferenceServer 线程
（batch 128 / 5ms 攒批 / torch 默认线程）+ worker 侧 QueuePolicyEvaluator
（队列往返）。对比两条路径的每 move 耗时（200 sims, leaf_batch=16）：

  py   ：battle_from_spec 逐 move 推进 + py mcts_search（python 引擎+编码）
  rust ：roco_engine.py_mcts_search 每次从 spec 重建（rust 引擎+编码）

真实 rust worker 会让对局状态留在 rust 内（省掉 spec 重建，更快），
本基准取的是保守值。samples/s = 每 move 一次搜索的节奏倒数。

用法：env\\python.exe native/tools/bench_worker_topology.py [--moves 6]
       [--sims 200] [--batch 16] [--checkpoint 路径]
报告写 native/tools/_bench_topology_last.txt（UTF-8）。
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os_chdir = __import__("os").chdir
os_chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import roco_engine  # noqa: E402
from backend.engine.ai.core.evaluator import (  # noqa: E402
    BatchedInferenceServer,
    QueuePolicyEvaluator,
    SyncPickleQueue,
)
from backend.engine.ai.core.mcts import NetworkPolicyAgent, mcts_search  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402

OUT = Path(__file__).parent / "_bench_topology_last.txt"
LINES: list[str] = []


def log(msg: str) -> None:
    LINES.append(msg)
    print(msg, flush=True)


class _RustEvalAdapter:
    def __init__(self, ev):
        self._ev = ev

    def evaluate_batch(self, states, masks):
        values, priors = self._ev.evaluate_batch(states, masks)
        return (
            [float(v) for v in np.asarray(values).ravel()],
            np.asarray(priors, dtype=np.float32).tolist(),
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--moves", type=int, default=6)
    ap.add_argument("--sims", type=int, default=200)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--checkpoint", type=str,
                    default=str(ROOT / "checkpoints" / "exp16" / "model_rl.pt"))
    args = ap.parse_args()

    ckpt = Path(args.checkpoint)
    if ckpt.exists():
        model = ModularBattleNet.load(str(ckpt), device="cpu")
        log(f"checkpoint: {ckpt.name}（torch 默认线程={torch.get_num_threads()}）")
    else:
        model = ModularBattleNet()
        log(f"警告: {ckpt} 不存在，随机初始化")
    model.eval()

    ctx = mp.get_context("spawn")
    request_queue = SyncPickleQueue(maxsize=4, ctx=ctx)
    reply_q = ctx.Queue()
    server = BatchedInferenceServer(
        model, "cpu", request_queue, {0: reply_q},
        batch_size=128, timeout_ms=5.0,
    )
    server.start()

    qpe = QueuePolicyEvaluator(0, request_queue, reply_q)
    adapter = _RustEvalAdapter(qpe)

    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    spec_json = json.dumps(spec, ensure_ascii=False)
    cfg = json.dumps({
        "num_simulations": args.sims, "c_puct": 2.0, "root_noise": 0.25,
        "max_turns": 60, "opp_greedy": True, "opp_temperature": 1.0,
        "use_network_opponent": True, "leaf_batch_size": args.batch,
        "digest_trace": False,
    }, ensure_ascii=False)

    def make_battle():
        random.seed(spec["seed"] + 1)
        np.random.seed((spec["seed"] + 1) % (2**32 - 1))
        b = battle_from_spec(spec)
        a = RuleAgent("A", b.player_a)
        rl = RuleAgent("B", b.player_b)
        b.player_a.active_index = a.choose_lead(b)
        b.player_b.active_index = rl.choose_lead(b)
        b._invalidate_ctx_team_cache()
        return b, a, rl

    def py_moves(k: int) -> float:
        battle, a, rl = make_battle()
        ev = qpe
        opp = NetworkPolicyAgent(evaluator=ev, greedy=True)
        t0 = time.perf_counter()
        done = 0
        while done < k and not battle.is_finished:
            probs = mcts_search(
                battle, None, __import__("backend.sim.factory", fromlist=["SimFactory"]).SimFactory(), opp,
                num_simulations=args.sims, c_puct=2.0, root_noise=0.25,
                max_turns=60, opp_greedy=True, evaluator=ev,
                draw_margin=0.15, gamma=1.0, tanh_k=0.0,
                leaf_batch_size=args.batch,
            )
            idx = int(np.argmax(probs))
            from backend.engine.ai.core.mcts import action_index_to_action
            act = action_index_to_action(battle.player_a, idx)
            if act is None:
                break
            battle.execute_turn_headless(a, rl, fixed_action_a=act)
            done += 1
        return (time.perf_counter() - t0) / max(done, 1)

    def rust_moves(k: int) -> float:
        t0 = time.perf_counter()
        for _ in range(k):
            roco_engine.py_mcts_search(spec_json, cfg, adapter, adapter)
        return (time.perf_counter() - t0) / k

    # 预热（编译/缓存/队列连通）
    py_moves(1)
    rust_moves(1)

    py_s = py_moves(args.moves)
    ru_s = rust_moves(args.moves)
    log(f"py   worker 出招节奏: {py_s*1000:7.0f} ms/move（{args.moves} moves × {args.sims} sims）")
    log(f"rust worker 出招节奏: {ru_s*1000:7.0f} ms/move（含 spec 重建，保守值）")
    log(f"单 worker 加速比: {py_s / ru_s:.2f}x")
    log(f"samples/s（每 move 1 样本）: py={1/py_s:.2f} rust={1/ru_s:.2f}")

    server.stop()
    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
