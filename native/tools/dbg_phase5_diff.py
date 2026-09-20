"""dbg_phase5_diff — 定位 rust/py 自对弈记录的首个分歧样本。"""

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

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
seed = spec["seed"] + 1

pyp = py_game(spec, ev, seed, 12, 1.0, 16)


class _RustAdapter:
    def __init__(self, ev):
        self._ev = ev

    def evaluate_batch(self, states, masks):
        v, p = ev.evaluate_batch(states, masks)
        return [float(x) for x in np.asarray(v).ravel()], np.asarray(p, dtype=np.float32).tolist()


ru = rust_game(spec, _RustAdapter(ev), _RustAdapter(ev), 12, 1.0, 16)

print(f"py : turns={pyp['turns']} samples={pyp['P'].shape[0]} outcome={pyp['outcome_a']:.3f} winner={pyp['winner']}")
print(f"rust: turns={ru['turns']} samples={ru['P'].shape[0]} outcome={ru['outcome_a']:.3f} winner={ru['winner']}")

n = min(pyp["P"].shape[0], ru["P"].shape[0])
first_p = first_m = first_state = None
for i in range(n):
    if first_p is None and not np.array_equal(pyp["P"][i], ru["P"][i]):
        first_p = i
    if first_m is None and not np.array_equal(pyp["M"][i], ru["M"][i]):
        first_m = i
    if first_state is None:
        a = pyp["states"][i]["sprite_states"].tolist()
        b = ru["states"][i]["sprite_states"].tolist()
        if a != b:
            first_state = i
print(f"首个 P 差异样本: {first_p}")
print(f"首个 M 差异样本: {first_m}")
print(f"首个 状态 差异样本: {first_state}")
if first_p is not None:
    i = first_p
    nz = [(j, round(float(pyp["P"][i][j]), 4), round(float(ru["P"][i][j]), 4))
          for j in range(17) if abs(pyp["P"][i][j] - ru["P"][i][j]) > 1e-6]
    print(f"  P[{i}] 差异位: {nz[:8]}")
