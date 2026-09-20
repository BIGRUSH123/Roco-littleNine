"""dbg_moves_cmp — 同配置下逐决策对比 py/rust 采样序列（12 sims）。"""

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

import backend.engine.ai.train as T  # noqa: E402

calls = []
orig = T._sample_action


def spy(probs, temperature):
    idx = orig(probs, temperature)
    calls.append(int(idx))
    return idx


T._sample_action = spy
pg = py_game(spec, ev, seed, 12, 1.0, 16)
T._sample_action = orig

ru = rust_game(spec, Adapter(ev), Adapter(ev), 12, 1.0, 16)

rmoves = [i for (_s, i) in ru["moves"]]
print(f"py calls n={len(calls)}  rust moves n={len(rmoves)}")
mism = [i for i in range(min(len(calls), len(rmoves))) if calls[i] != rmoves[i]]
print(f"首个采样分叉 decision: {mism[0] if mism else '无 — 采样全程一致'}")
if mism:
    k = mism[0]
    print(f"  py  calls[{k}:{k+6}] = {calls[k:k+6]}")
    print(f"  rust moves[{k}:{k+6}] = {rmoves[k:k+6]}")
print(f"py  turns={pg['turns']} winner={pg['winner']} lives={pg['lives']} active={pg['active']}")
print(f"rust turns={ru['turns']} winner={ru['winner']} lives={ru['lives']} active={ru['active']}")
for line in pg["log_tail"]:
    print("  |", line)
