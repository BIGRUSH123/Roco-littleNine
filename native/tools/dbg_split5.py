"""dbg_split5 — 耗时分解：评估侧（torch/FFI/转换） vs 引擎侧（搜索/结算）。"""
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

_logf = open(ROOT / "native" / "tools" / "_split5_log.txt", "w", encoding="utf-8")


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

from gate_phase5 import py_game, rust_game  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402


class TimedAdapter:
    """统计 rust→python 评估调用：次数、评估内 torch 耗时、总调用耗时。"""

    def __init__(self, ev):
        self._ev = ev
        self.n = 0
        self.states = 0
        self.t_call = 0.0
        self.t_torch = 0.0

    def evaluate_batch(self, states, masks):
        t0 = time.perf_counter()
        values, priors = self._ev.evaluate_batch(states, masks)
        t1 = time.perf_counter()
        self.n += 1
        self.states += len(states)
        self.t_call += t1 - t0
        self.t_torch += t1 - t0  # 评估内全部视为 torch（转换在 rust 侧另计）
        return (
            [float(v) for v in __import__("numpy").asarray(values).ravel()],
            __import__("numpy").asarray(priors, dtype=np_float32()).tolist(),
        )


def np_float32():
    import numpy as np
    return np.float32


def main() -> None:
    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1

    # py：包装 torch_ev.evaluate/evaluate_batch 计时
    te = {"n": 0, "states": 0, "t": 0.0}
    orig_b = torch_ev.evaluate_batch
    orig_e = torch_ev.evaluate

    def eb(states, masks):
        t0 = time.perf_counter()
        r = orig_b(states, masks)
        te["t"] += time.perf_counter() - t0
        te["n"] += 1
        te["states"] += len(states)
        return r

    def ev_(state, mask):
        t0 = time.perf_counter()
        r = orig_e(state, mask)
        te["t"] += time.perf_counter() - t0
        te["n"] += 1
        te["states"] += 1
        return r

    torch_ev.evaluate_batch = eb
    torch_ev.evaluate = ev_
    py_game(spec, torch_ev, seed, 4, 1.0, 16)  # 预热
    te.update(n=0, states=0, t=0.0)
    t0 = time.perf_counter()
    r = py_game(spec, torch_ev, seed, 48, 1.0, 16)
    py_t = time.perf_counter() - t0
    py_n = r["P"].shape[0]
    print(f"py  总 {py_t:.2f}s / {py_n} 样本 | 评估 {te['n']} 次 / {te['states']} 状态 "
          f"= {te['t']:.2f}s ({te['t'] / py_t * 100:.0f}%) | 引擎+编码 {py_t - te['t']:.2f}s",
          flush=True)

    # rust：TimedAdapter 包装
    import numpy as np

    class TA:
        def __init__(self, ev):
            self._ev = ev
            self.n = 0
            self.states = 0
            self.t = 0.0

        def evaluate_batch(self, states, masks):
            t0 = time.perf_counter()
            values, priors = self._ev.evaluate_batch(states, masks)
            self.t += time.perf_counter() - t0
            self.n += 1
            self.states += len(states)
            return (
                [float(v) for v in np.asarray(values).ravel()],
                np.asarray(priors, dtype=np.float32).tolist(),
            )

    ta = TA(torch_ev)
    rust_game(spec, ta, ta, 4, 1.0, 16)  # 预热
    ta.n = 0
    ta.states = 0
    ta.t = 0.0
    t0 = time.perf_counter()
    rr = rust_game(spec, ta, ta, 48, 1.0, 16)
    ru_t = time.perf_counter() - t0
    ru_n = rr["P"].shape[0]
    print(f"rust 总 {ru_t:.2f}s / {ru_n} 样本 | 评估回调 {ta.n} 次 / {ta.states} 状态 "
          f"= {ta.t:.2f}s ({ta.t / ru_t * 100:.0f}%) | 引擎+FFI转换 {ru_t - ta.t:.2f}s",
          flush=True)


if __name__ == "__main__":
    main()
