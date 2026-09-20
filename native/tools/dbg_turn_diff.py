"""dbg_turn_diff — 定位 rust/py 自对弈首个逐回合 digest 分叉回合与字段。"""

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
import torch  # noqa: E402

import roco_engine  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from gate_phase5 import py_game, rust_game  # noqa: E402

torch.set_num_threads(1)
model = ModularBattleNet.load("checkpoints/exp16/model_rl.pt", device="cpu")
model.eval()
ev = TorchEvaluator(model, device="cpu")


class Adapter:
    def __init__(self, ev):
        self._ev = ev

    def evaluate_batch(self, states, masks):
        v, p = ev.evaluate_batch(states, masks)
        return [float(x) for x in np.asarray(v).ravel()], np.asarray(p, dtype=np.float32).tolist()


spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
seed = spec["seed"] + 1

pg = py_game(spec, ev, seed, 12, 1.0, 16)
ru = rust_game(spec, Adapter(ev), Adapter(ev), 12, 1.0, 16)

ptd, rd = pg["turn_digests"], json.loads(ru["digests"])
n = min(len(ptd), len(rd))
print(f"py turns={len(ptd)}  rust turns={len(rd)}")
first = None
for k in range(n):
    if ptd[k] != rd[k]:
        first = k
        break
if first is None:
    print(f"逐回合 digest 全一致（{n} 回合）")
    sys.exit(0)

print(f"首个 digest 分叉回合: {first}（0-based，即第 {first+1} 次执行后）")
d = first_diff = None
from gate_phase5 import first_diff as fd  # noqa: E402

print("字段级差异:", fd(ptd[first], rd[first], f"turn{first}"))
# 打印 py 该回合的事件（RoundRecord 的 _header 与 events）
tail = pg["log_tail"]
print("py 末尾事件:")
for line in tail:
    print("  |", line[:200])
