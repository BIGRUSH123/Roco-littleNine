"""gate_phase5 — 阶段5门：rust 自对弈对局循环的一致性 + 速度测试。

1. 一致性：py _play_one_rl_battle 核心（MCTSAgent 双方 record）vs
   roco_engine.py_selfplay_game，同种子（spec.seed+1）逐样本对比
   P/M/v/状态编码/turns。
2. 速度测试：真实 worker 拓扑（BatchedInferenceServer batch128/5ms +
   QueuePolicyEvaluator 队列 + torch 默认线程）下，py 对局 vs rust
   对局的 samples/s 与加速比。

用法：env\\python.exe native/tools/gate_phase5.py [--sims 48] [--batch 16]
       [--speed-sims 48] [--checkpoint 路径]
报告写 native/tools/_gate_phase5_last.txt（UTF-8）。
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import roco_engine  # noqa: E402
from backend.engine.ai.core.evaluator import (  # noqa: E402
    BatchedInferenceServer,
    QueuePolicyEvaluator,
    TorchEvaluator,
)
from backend.engine.ai.core.mcts import (  # noqa: E402
    NetworkPolicyAgent,
    action_index_to_action,
)
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.engine.ai.core.outcome import battle_outcome_a  # noqa: E402
from backend.engine.ai.train import MCTSAgent  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec, gate_digest  # noqa: E402

OUT = Path(__file__).parent / "_gate_phase5_last.txt"
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


def py_game(spec: dict, evaluator, seed: int, sims: int, temperature: float, batch: int):
    """py _play_one_rl_battle 核心（spec 固定阵容版）。返回记录 dict。"""
    random.seed(seed)
    np.random.seed(seed)
    battle = battle_from_spec(spec)
    opp_a = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
    opp_b = NetworkPolicyAgent(evaluator=evaluator, greedy=True)
    agent_a = MCTSAgent(
        "A", battle.player_a, SimFactory(), opp_a, sims, temperature,
        root_noise=0.25, record=True, evaluator=evaluator, max_turns=60,
        draw_margin=0.15, gamma=1.0, tanh_k=0.0, leaf_batch_size=batch,
        opp_greedy=True,
    )
    agent_b = MCTSAgent(
        "B", battle.player_b, SimFactory(), opp_b, sims, temperature,
        root_noise=0.25, record=True, evaluator=evaluator, max_turns=60,
        draw_margin=0.15, gamma=1.0, tanh_k=0.0, leaf_batch_size=batch,
        opp_greedy=True,
    )
    turn = 0
    turn_digests = []
    while not battle.is_finished and turn < 60:
        battle.execute_turn(agent_a, agent_b)
        turn += 1
        turn_digests.append(gate_digest(battle))
    outcome_a, _ = battle_outcome_a(battle, 60, draw_margin=0.15, gamma=1.0, tanh_k=0.0)
    states, P, M, v = [], [], [], []
    for st, pi, m in agent_a.history:
        states.append(st); P.append(pi); M.append(m); v.append(outcome_a)
    for st, pi, m in agent_b.history:
        states.append(st); P.append(pi); M.append(m); v.append(-outcome_a)
    return {
        "states": states,
        "P": np.stack(P).astype(np.float32) if P else np.zeros((0, 17), np.float32),
        "M": np.stack(M).astype(np.float32) if M else np.zeros((0, 17), np.float32),
        "v": np.array(v, dtype=np.float32),
        "winner": battle.winner or "",
        "turns": turn,
        "outcome_a": outcome_a,
        "a_len": len(agent_a.history),
        "turn_digests": turn_digests,
        "lives": [battle.player_a.lives, battle.player_b.lives],
        "active": [battle.player_a.active_index, battle.player_b.active_index],
        "log_tail": [str(x) for x in (battle.log or [])[-8:]],
    }


def rust_game(spec: dict, adapter_a, adapter_b, sims: int, temperature: float, batch: int):
    cfg = json.dumps({
        "num_simulations": sims, "root_noise": 0.25, "max_turns": 60,
        "opp_greedy": True, "leaf_batch_size": batch, "temperature": temperature,
    }, ensure_ascii=False)
    r = roco_engine.py_selfplay_game(
        json.dumps(spec, ensure_ascii=False), cfg, adapter_a, adapter_b,
    )
    return r


def first_diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k} 键缺失"
            r = first_diff(a[k], b[k], f"{path}.{k}")
            if r:
                return r
        return ""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return f"{path} 长度 {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = first_diff(x, y, f"{path}[{i}]")
            if r:
                return r
        return ""
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        return first_diff(a.tolist(), b.tolist(), path + ".arr")
    if a != b:
        return f"{path}: py={a!r} rust={b!r}"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=24)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--speed-sims", type=int, default=48)
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--skip-consistency", action="store_true",
                    help="跳过一致性段（B 侧 swap 搜索语义对齐前仅测速度）")
    ap.add_argument("--checkpoint", type=str,
                    default=str(ROOT / "checkpoints" / "exp16" / "model_rl.pt"))
    args = ap.parse_args()

    ckpt = Path(args.checkpoint)
    if ckpt.exists():
        model = ModularBattleNet.load(str(ckpt), device="cpu")
        log(f"checkpoint: {ckpt.name}（torch 线程={torch.get_num_threads()}）")
    else:
        model = ModularBattleNet()
        log(f"警告: {ckpt} 不存在，随机初始化")
    model.eval()
    torch_ev = TorchEvaluator(model, device="cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    spec_json = json.dumps(spec, ensure_ascii=False)
    seed = spec["seed"] + 1

    # ── 1. 一致性（直连 torch，无队列） ──
    py = ru = None
    diffs = []
    if not args.skip_consistency:
        adapter_a = _RustEvalAdapter(torch_ev)
        py = py_game(spec, torch_ev, seed, args.sims, 1.0, args.batch)
        ru = rust_game(spec, adapter_a, adapter_a, args.sims, 1.0, args.batch)
        if py["turns"] != ru["turns"]:
            diffs.append(f"turns: py={py['turns']} rust={ru['turns']}")
        if py["P"].shape[0] != ru["P"].shape[0]:
            diffs.append(f"样本数: py={py['P'].shape[0]} rust={ru['P'].shape[0]}")
        else:
            d = first_diff(py["P"].tolist(), ru["P"].tolist(), "P")
            if d:
                diffs.append(d)
            d = first_diff(py["M"].tolist(), ru["M"].tolist(), "M")
            if d:
                diffs.append(d)
            if py["v"].tolist() != ru["v"].tolist():
                diffs.append(f"v: py={py['v'].tolist()} rust={ru['v'].tolist()}")
            d = first_diff(
                [np.asarray(s["ast_tokens"]).tolist() for s in py["states"]],
                [np.asarray(s["ast_tokens"]).tolist() for s in ru["states"]],
                "ast_tokens",
            )
            if d:
                diffs.append(d)
            d = first_diff(
                [np.asarray(s["sprite_states"]).tolist() for s in py["states"]],
                [np.asarray(s["sprite_states"]).tolist() for s in ru["states"]],
                "sprite_states",
            )
            if d:
                diffs.append(d)
    if diffs:
        log("一致性 FAIL")
        for d in diffs:
            log("    " + d[:400])
        if args.skip_consistency:
            log("（--skip-consistency：继续速度测试）")
        else:
            OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
            sys.exit(1)
    if py is not None:
        log(f"── 一致性 PASS（{py['turns']} 回合 / {py['P'].shape[0]} 样本逐位一致）──")

    # ── 2. 速度测试（真实 worker 拓扑：server + 队列，torch 默认线程） ──
    from backend.engine.ai.core.evaluator import SyncPickleQueue

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

    # 预热（编译/缓存/队列连通）
    py_game(spec, qpe, seed, 4, 1.0, args.batch)
    rust_game(spec, adapter, adapter, 4, 1.0, args.batch)

    py_total = 0.0
    py_samples = 0
    for g in range(args.games):
        t0 = time.perf_counter()
        r = py_game(spec, qpe, seed + 100 + g, args.speed_sims, 1.0, args.batch)
        py_total += time.perf_counter() - t0
        py_samples += r["P"].shape[0]
    ru_total = 0.0
    ru_samples = 0
    for g in range(args.games):
        t0 = time.perf_counter()
        # rust 侧种子取自 spec["seed"]+1 —— 与 py 的 seed+100+g 对齐，
        # 确保双方跑同一局（样本数可比）
        spec_g = dict(spec)
        spec_g["seed"] = seed + 100 + g - 1
        r = rust_game(spec_g, adapter, adapter, args.speed_sims, 1.0, args.batch)
        ru_total += time.perf_counter() - t0
        ru_samples += r["P"].shape[0]

    py_rate = py_samples / py_total if py_total > 0 else 0.0
    ru_rate = ru_samples / ru_total if ru_total > 0 else 0.0
    log(f"py   对局: {py_total:.2f}s / {args.games} 局（{args.speed_sims} sims）"
        f" = {py_samples} 样本, {py_rate:.1f} samples/s")
    log(f"rust 对局: {ru_total:.2f}s / {args.games} 局（{args.speed_sims} sims）"
        f" = {ru_samples} 样本, {ru_rate:.1f} samples/s")
    log(f"单 worker 加速比: {py_total / ru_total:.2f}x"
        f"（rust 侧含 spec 重建，保守值；多 worker 攒批收益另行叠加）")

    server.stop()
    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
