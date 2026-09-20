"""dbg_bench5 — 阶段5 引擎吞吐基准：直连 torch（无队列）对比 py/rust 每样本耗时。"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_bench5_log.txt", "w", encoding="utf-8")


class _Tee:
    def __init__(self, *streams):
        self._s = streams

    def write(self, s):
        for x in self._s:
            x.write(s)

    def flush(self):
        for x in self._s:
            x.flush()


sys.stdout = _Tee(sys.stdout, _logf)

from gate_phase5 import _RustEvalAdapter, py_game, rust_game  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402


def main() -> None:
    import torch
    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1
    adapter = _RustEvalAdapter(torch_ev)

    for sims in (24, 48):
        # 预热
        py_game(spec, torch_ev, seed, 4, 1.0, 16)
        rust_game(spec, adapter, adapter, 4, 1.0, 16)

        t0 = time.perf_counter()
        r = py_game(spec, torch_ev, seed, sims, 1.0, 16)
        py_t = time.perf_counter() - t0
        py_n = r["P"].shape[0]

        t0 = time.perf_counter()
        rr = rust_game(spec, adapter, adapter, sims, 1.0, 16)
        ru_t = time.perf_counter() - t0
        ru_n = rr["P"].shape[0]

        print(f"sims={sims}: py {py_t:.2f}s/{py_n}样本 = {py_n / py_t:.1f}/s | "
              f"rust {ru_t:.2f}s/{ru_n}样本 = {ru_n / ru_t:.1f}/s | "
              f"每样本加速比 {(py_t / py_n) / (ru_t / ru_n):.2f}x", flush=True)


if __name__ == "__main__":
    main()
