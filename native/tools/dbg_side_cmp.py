"""dbg_side_cmp — 按 A/B 侧分别对拍整局记录（P/M/状态），定位首个真分叉。"""

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

side = ru["side"]
a_n = side.count(0)
print(f"py  a_len={pg['a_len']} total={pg['P'].shape[0]}")
print(f"rust a_n={a_n} b_n={len(side)-a_n} total={len(side)}")

KEYS = ["sprite_states", "skill_stats", "global_stats", "ast_tokens", "ast_values"]


def first_mismatch(py_states, py_p, py_m, ru_states, ru_p, ru_m, n):
    mism_p = mism_m = mism_s = None
    for k in range(n):
        if mism_p is None and not np.array_equal(py_p[k], ru_p[k]):
            mism_p = k
        if mism_m is None and not np.array_equal(py_m[k], ru_m[k]):
            mism_m = k
        if mism_s is None and not all(
            np.array_equal(
                np.asarray(py_states[k][key]), np.asarray(ru_states[k][key])
            )
            for key in KEYS
        ):
            mism_s = k
        if mism_p is not None and mism_m is not None and mism_s is not None:
            break
    return mism_p, mism_m, mism_s


for name, s0, s1 in (("A", 0, pg["a_len"]), ("B", pg["a_len"], len(side))):
    py_states = pg["states"][s0:s1]
    py_p = pg["P"][s0:s1]
    py_m = pg["M"][s0:s1]
    ru_states = ru["states"][s0:s1]
    ru_p = ru["P"][s0:s1]
    ru_m = ru["M"][s0:s1]
    n = min(len(py_states), len(ru_states), len(py_p), len(ru_p))
    mism_p, mism_m, mism_s = first_mismatch(py_states, py_p, py_m, ru_states, ru_p, ru_m, n)
    print(f"[{name}] py 样本 {len(py_states)} rust 样本 {len(ru_states)}（对比 {n}）")
    print(f"  P 首差异: {mism_p}   M 首差异: {mism_m}   状态 首差异: {mism_s}")
