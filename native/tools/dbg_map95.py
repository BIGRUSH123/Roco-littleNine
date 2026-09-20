"""dbg_map95 — 把 np 事件 #95 的 P 向量映射回 py 记录行（哪个 side/turn）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402

_logf = open(ROOT / "native" / "tools" / "_dbg_map95_log.txt", "w", encoding="utf-8")


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
    # 目标向量（来自事件 #95 的 Pnz，单位 = 访问数/24）
    target = {0: 2, 1: 4, 2: 1, 3: 3, 11: 13, 15: 1}

    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1

    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    a_len = py["a_len"]
    pyP = np.asarray(py["P"])

    # 记录的 P 是归一化后的；目标 *24/24 还原为 0..1
    tgt = np.zeros(17, dtype=np.float32)
    for k, v in target.items():
        tgt[k] = v / 24.0

    hits = []
    for i, row in enumerate(pyP):
        d = np.abs(row.astype(np.float64) - tgt.astype(np.float64)).max()
        if d < 1e-6:
            hits.append(i)
    print(f"目标 P 匹配的记录行: {hits}（a_len={a_len}）", flush=True)
    for i in hits:
        side = "A" if i < a_len else "B"
        turn = i if i < a_len else i - a_len
        print(f"  行 {i} → side={side} 同侧第 {turn} 个决策（0 基）→ "
              f"{'回合 ' + str(turn) if side == 'A' else '回合 ' + str(turn)}", flush=True)

    # 同样在 rust 记录里找 rust 版本向量
    adapter = _RustEvalAdapter(torch_ev)
    ru = rust_game(spec, adapter, adapter, 24, 1.0, 16)
    ruP = np.asarray(ru["P"])
    ra_len = sum(1 for s in ru["side"] if int(s) == 0)
    tgt_r = np.zeros(17, dtype=np.float32)
    for k, v in {0: 3, 1: 3, 2: 2, 3: 3, 11: 12, 15: 1}.items():
        tgt_r[k] = v / 24.0
    hits_r = [i for i, row in enumerate(ruP)
              if np.abs(row.astype(np.float64) - tgt_r.astype(np.float64)).max() < 1e-6]
    print(f"rust 目标 P 匹配的记录行: {hits_r}（ra_len={ra_len}）", flush=True)
    for i in hits_r:
        side = "A" if i < ra_len else "B"
        turn = i if i < ra_len else i - ra_len
        print(f"  行 {i} → side={side} 同侧第 {turn} 个决策", flush=True)


if __name__ == "__main__":
    main()
