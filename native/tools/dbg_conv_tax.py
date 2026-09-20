"""dbg_conv_tax — 隔离测量 rust 路径的 编码+转换 税（stub 评估器，无 torch）。"""
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

_logf = open(ROOT / "native" / "tools" / "_conv_tax_log.txt", "w", encoding="utf-8")


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

import json as _json  # noqa: E402

import roco_engine  # noqa: E402


class StubEval:
    """确定性 stub：value=0，prior=mask 归一化。测引擎+转换上限。"""

    def evaluate_batch(self, states, masks):
        n = len(states)
        vals = [0.0] * n
        priors = []
        for m in masks:
            row = list(m)
            s = sum(row)
            priors.append([x / s if s > 0 else 0.0 for x in row])
        return vals, priors


def main() -> None:
    spec = _json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    ev = StubEval()
    for leaf in (16, 64, 128):
        cfg = _json.dumps({
            "num_simulations": 100, "root_noise": 0.25, "max_turns": 60,
            "opp_greedy": True, "leaf_batch_size": leaf, "temperature": 1.0,
        })
        # 预热 1 局
        roco_engine.py_selfplay_game(_json.dumps(spec, ensure_ascii=False), cfg, ev, ev)
        t0 = time.perf_counter()
        r = roco_engine.py_selfplay_game(_json.dumps(spec, ensure_ascii=False), cfg, ev, ev)
        wall = time.perf_counter() - t0
        n = len(r["P"])
        print(f"leaf={leaf}: {wall:.2f}s / {n} 决策 = {wall / n * 1000:.0f} ms/决策 "
              f"({n / wall:.1f}/s 单进程上限, 纯引擎+编码+转换)", flush=True)


if __name__ == "__main__":
    main()
