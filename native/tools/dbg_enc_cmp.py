"""dbg_enc_cmp — 对比 py/rust 自对弈记录中的编码状态与 mask，找首个不一致记录。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_dbg_enc_cmp_log.txt", "w", encoding="utf-8")


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

import numpy as np  # noqa: E402

from gate_phase5 import _RustEvalAdapter, py_game, rust_game  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402

KEYS = [
    "sprite_stats", "sprite_elements", "sprite_states",
    "skill_stats", "skill_elements", "skill_states",
    "global_stats", "global_elements", "ast_tokens", "ast_values",
]


def cmp_record(i, ps, rs, pm, rm):
    bad = []
    for k in KEYS:
        a = np.asarray(ps[k])
        b = np.asarray(rs[k])
        if a.shape != b.shape:
            bad.append(f"{k} 形状 py={a.shape} rust={b.shape}")
            continue
        if not np.array_equal(a, b):
            d = np.abs(a.astype(np.float64) - b.astype(np.float64))
            idx = np.unravel_index(np.argmax(d), d.shape)
            bad.append(f"{k} max|Δ|={d.max():.6e} @ {idx} "
                       f"py={a[idx]} rust={b[idx]} 非零={int((d > 0).sum())}")
    am = np.asarray(pm)
    bm = np.asarray(rm)
    if not np.array_equal(am, bm):
        bad.append(f"mask 非零集 py={set(np.nonzero(am)[0].tolist())} "
                   f"rust={set(np.nonzero(bm)[0].tolist())}")
    return bad


def main() -> None:
    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1

    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    adapter = _RustEvalAdapter(torch_ev)
    ru = rust_game(spec, adapter, adapter, 24, 1.0, 16)

    n = min(len(py["states"]), len(ru["states"]))
    print(f"记录数 py={len(py['states'])} rust={len(ru['states'])}", flush=True)
    first = -1
    for i in range(n):
        bad = cmp_record(i, py["states"][i], ru["states"][i], py["M"][i], ru["M"][i])
        if bad:
            first = i
            print(f"✗ 记录 {i} 首个不一致（共 {len(bad)} 项）：", flush=True)
            for b in bad[:12]:
                print("   ", b, flush=True)
            break
    if first < 0:
        print(f"✓ 全部 {n} 条记录编码状态与 mask 逐位一致", flush=True)
        # 顺带比较 P
        pyP, ruP = np.asarray(py["P"]), np.asarray(ru["P"])
        for i in range(min(len(pyP), ruP.shape[0])):
            if not np.array_equal(pyP[i], ruP[i]):
                print(f"首个 P 不一致行 = {i}（编码全同 → 树逻辑/RNG 差异）", flush=True)
                break
    side = py.get("side")
    if side is not None and first >= 0:
        print(f"记录 {first} 的 side（py）= {side[first]}", flush=True)
        # 该记录是第几个同侧决策
        cnt = sum(1 for s in side[:first + 1] if s == side[first])
        print(f"    → 同侧第 {cnt} 个决策", flush=True)


if __name__ == "__main__":
    main()
