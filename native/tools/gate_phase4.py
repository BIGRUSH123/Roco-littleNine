"""gate_phase4 — 阶段4门：真实模型下 py 路径 vs rust 路径。

1. 同种子一致性：py mcts_search（python 引擎+python 编码）vs
   roco_engine.py_mcts_search（rust 全链路 + py torch 回调），
   双方用同一个 ModularBattleNet（TorchEvaluator），比较
   probs_bits/counts/trace/双方 RNG 头。
2. samples/s：两路径各跑 repeats 次搜索，给出加速比。

py 侧适配：_RustEvalAdapter 把评估器返回值转 plain list
（pyo3 从 np.float32 数组抽取不可靠），协议 evaluate_batch(states, masks)
保持不变。

用法：env\\python.exe native/tools/gate_phase4.py [--specs 6] [--sims 48]
       [--batch 16] [--repeats 5] [--checkpoint 路径]
报告写 native/tools/_gate_phase4_last.txt（UTF-8）。
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
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import roco_engine  # noqa: E402
from backend.engine.ai.core.encoder import encode_battle_state  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.mcts import NetworkPolicyAgent, mcts_search  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.battle import Battle  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402

OUT = Path(__file__).parent / "_gate_phase4_last.txt"
LINES: list[str] = []


def log(msg: str) -> None:
    LINES.append(msg)
    print(msg, flush=True)


class _RustEvalAdapter:
    """rust → py 评估器薄适配：返回值转 plain list。"""

    def __init__(self, ev):
        self._ev = ev

    def evaluate_batch(self, states, masks):
        values, priors = self._ev.evaluate_batch(states, masks)
        return (
            [float(v) for v in np.asarray(values).ravel()],
            np.asarray(priors, dtype=np.float32).tolist(),
        )


class _NodeCountHook:
    """根节点 visit_count 采集（同 mcts_gate 的节点计数法）。"""

    def __init__(self):
        self.created: list = []
        self._orig = None

    def install(self):
        from backend.engine.ai.core import mcts as mcts_mod

        orig = mcts_mod.MCTSNode.__init__

        def init_patched(node, valid_actions, prior):
            orig(node, valid_actions, prior)
            self.created.append(node)

        mcts_mod.MCTSNode.__init__ = init_patched
        self._orig = orig

    def uninstall(self):
        from backend.engine.ai.core import mcts as mcts_mod

        mcts_mod.MCTSNode.__init__ = self._orig

    def root_counts(self) -> list[int]:
        counts = [0] * 17
        for n in self.created:
            if n.valid_actions:
                for ai in range(17):
                    child = n.children[ai]
                    if child is not None:
                        counts[ai] = int(child.visit_count)
                break
        return counts


def run_py(spec: dict, evaluator, opp_evaluator, sims: int, batch: int):
    random.seed(spec["seed"] + 1)
    np.random.seed((spec["seed"] + 1) % (2**32 - 1))
    battle = battle_from_spec(spec)
    a = RuleAgent("A", battle.player_a)
    b = RuleAgent("B", battle.player_b)
    battle.player_a.active_index = a.choose_lead(battle)
    battle.player_b.active_index = b.choose_lead(battle)
    battle._invalidate_ctx_team_cache()

    ev = TorchEvaluator(evaluator, device="cpu") if not isinstance(evaluator, TorchEvaluator) else evaluator
    opp = NetworkPolicyAgent(evaluator=opp_evaluator, greedy=True)
    hook = _NodeCountHook()
    hook.install()
    try:
        t0 = time.perf_counter()
        probs = mcts_search(
            battle, None, SimFactory(), opp,
            num_simulations=sims, c_puct=2.0, root_noise=0.25,
            max_turns=60, opp_greedy=True, evaluator=ev,
            draw_margin=0.15, gamma=1.0, tanh_k=0.0,
            leaf_batch_size=batch,
        )
        dt = time.perf_counter() - t0
    finally:
        hook.uninstall()
    st = np.random.get_state()
    return {
        "probs_bits": probs.view(np.uint32).tolist(),
        "counts": hook.root_counts(),
        "np_head": [int(x) for x in st[1][:8]],
        "np_pos": int(st[2]),
        "rng_head": list(random.getstate()[1][:8]),
        "rng_mti": int(random.getstate()[1][-1]),
        "seconds": dt,
    }


def run_rust(spec: dict, adapter_a, adapter_b, sims: int, batch: int):
    cfg = json.dumps({
        "num_simulations": sims, "c_puct": 2.0, "root_noise": 0.25,
        "max_turns": 60, "opp_greedy": True, "opp_temperature": 1.0,
        "use_network_opponent": True, "leaf_batch_size": batch,
        "digest_trace": False,
    }, ensure_ascii=False)
    t0 = time.perf_counter()
    r = json.loads(roco_engine.py_mcts_search(
        json.dumps(spec, ensure_ascii=False), cfg, adapter_a, adapter_b,
    ))
    dt = time.perf_counter() - t0
    r["seconds"] = dt
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--specs", type=int, default=6)
    ap.add_argument("--sims", type=int, default=48)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--checkpoint", type=str,
                    default=str(ROOT / "checkpoints" / "archive" / "mcts_v2_formal" / "model_rl.pt"))
    args = ap.parse_args()

    torch.set_num_threads(1)
    ckpt = Path(args.checkpoint)
    if ckpt.exists():
        model = ModularBattleNet.load(str(ckpt), device="cpu")
        log(f"已加载 checkpoint: {ckpt.name}")
    else:
        model = ModularBattleNet()
        log(f"警告: checkpoint 不存在({ckpt})，使用随机初始化模型")
    model.eval()
    torch_ev = TorchEvaluator(model, device="cpu")
    adapter_a = _RustEvalAdapter(torch_ev)
    adapter_b = _RustEvalAdapter(torch_ev)

    spec_files = sorted((ROOT / "native" / "gate_specs").glob("spec_*.json"))[: args.specs]

    # ── 1. 同种子一致性 ──
    passed = failed = 0
    bad: list[str] = []
    for sp in spec_files:
        spec = json.loads(sp.read_text(encoding="utf-8"))
        py = run_py(spec, model, torch_ev, args.sims, args.batch)
        ru = run_rust(spec, adapter_a, adapter_b, args.sims, args.batch)
        diffs = []
        for key in ("probs_bits", "counts", "np_head", "np_pos", "rng_head", "rng_mti"):
            if py[key] != ru[key]:
                diffs.append(f"{key}: py={py[key]!r} rust={ru[key]!r}")
        if not diffs:
            passed += 1
        else:
            failed += 1
            bad.append(sp.name)
            log(f"FAIL {sp.name}")
            for d in diffs:
                log("    " + d[:400])
    log(f"── 一致性 {passed + failed}：通过 {passed}，失败 {failed} ──")

    # ── 2. samples/s ──
    spec = json.loads(spec_files[0].read_text(encoding="utf-8"))
    py_s = ru_s = 0.0
    for _ in range(args.repeats):
        py_s += run_py(spec, model, torch_ev, args.sims, args.batch)["seconds"]
        ru_s += run_rust(spec, adapter_a, adapter_b, args.sims, args.batch)["seconds"]
    py_rate = args.repeats * args.sims / py_s
    ru_rate = args.repeats * args.sims / ru_s
    log(f"py   路径: {py_s:.2f}s / {args.repeats}×{args.sims}sims = {py_rate:.0f} sims/s")
    log(f"rust 路径: {ru_s:.2f}s / {args.repeats}×{args.sims}sims = {ru_rate:.0f} sims/s")
    log(f"加速比: {py_s / ru_s:.2f}x")

    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    if bad:
        print("失败：" + ", ".join(bad))


if __name__ == "__main__":
    main()
