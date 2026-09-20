"""dbg_b22 v2 — 按 A/B 流对齐比较记录（py 行 = A 流 + B 流拼接）。

A 流：行 0..a_len-1；B 流：行 a_len..。找两条流各自的第一个不一致
（状态/mask/P），并重点对比 B 的第 22 个决策（B 流下标 21）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_dbg_b22_log.txt", "w", encoding="utf-8")


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


def cmp_state(ps, rs, pm, rm):
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
            bad.append(f"{k} max|Δ|={d.max():.4e} @ {tuple(int(x) for x in idx)} "
                       f"py={a[idx]} rust={b[idx]}")
    am, bm = np.asarray(pm), np.asarray(rm)
    if not np.array_equal(am, bm):
        bad.append(f"mask py={set(np.nonzero(am)[0].tolist())} "
                   f"rust={set(np.nonzero(bm)[0].tolist())}")
    return bad


def stream_report(tag, py_rows, ru_rows, pyP, ruP, pyM, ruM, focus=None):
    n = min(len(py_rows), len(ru_rows))
    first_state = first_p = -1
    for i in range(n):
        if first_state < 0:
            bad = cmp_state(py_rows[i], ru_rows[i], pyM[i], ruM[i])
            if bad:
                first_state = i
                print(f"[{tag}] 状态/mask 首个不一致 流下标 {i}（共 {len(bad)} 项）：",
                      flush=True)
                for b in bad[:8]:
                    print("    ", b, flush=True)
        if first_p < 0 and not np.array_equal(pyP[i], ruP[i]):
            first_p = i
            d = np.abs(pyP[i].astype(np.float64) - ruP[i].astype(np.float64))
            print(f"[{tag}] P 首个不一致 流下标 {i} max|Δ|={d.max():.4e}", flush=True)
            idx = np.argsort(-d)[:6]
            for j in idx:
                print(f"    [{j}] py={pyP[i][j]:.9f} rust={ruP[i][j]:.9f}",
                      flush=True)
        if first_state >= 0 and first_p >= 0:
            break
    if first_state < 0:
        print(f"[{tag}] 状态+mask 全部 {n} 条一致", flush=True)
    if first_p < 0:
        print(f"[{tag}] P 全部 {n} 条一致", flush=True)
    if focus is not None:
        i = focus
        same = np.array_equal(pyP[i], ruP[i])
        print(f"[{tag}] focus 流下标 {i}: P {'逐位一致' if same else '不一致'}", flush=True)
        if not same:
            d = np.abs(pyP[i].astype(np.float64) - ruP[i].astype(np.float64))
            idx = np.argsort(-d)[:8]
            for j in idx:
                print(f"    [{j}] py={pyP[i][j]:.9f} rust={ruP[i][j]:.9f} Δ={d[j]:.3e}",
                      flush=True)
        bad = cmp_state(py_rows[i], ru_rows[i], pyM[i], ruM[i])
        print(f"[{tag}] focus 状态/mask: {'一致' if not bad else bad[:6]}", flush=True)
        top_p = np.argsort(-pyP[i])[:5]
        print(f"[{tag}] focus py P top5: {[(int(j), float(pyP[i][j])) for j in top_p]}",
              flush=True)
        top_r = np.argsort(-ruP[i])[:5]
        print(f"[{tag}] focus rust P top5: {[(int(j), float(ruP[i][j])) for j in top_r]}",
              flush=True)


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

    a_len = py["a_len"]
    ra_len = sum(1 for s in (int(s) for s in ru["side"]) if int(s) == 0) \
        if "side" in ru else a_len
    print(f"py a_len={a_len}  rust a_len={ra_len}  py 样本={py['P'].shape[0]}  "
          f"rust 样本={np.asarray(ru['P']).shape[0]}", flush=True)

    pyP = np.asarray(py["P"])
    ruP = np.asarray(ru["P"])
    pyM = np.asarray(py["M"])
    ruM = np.asarray(ru["M"])

    # A 流
    stream_report("A流", py["states"][:a_len], ru["states"][:ra_len],
                  pyP[:a_len], ruP[:ra_len], pyM[:a_len], ruM[:ra_len])
    # B 流
    py_b = py["states"][a_len:]
    ru_b = ru["states"][ra_len:]
    stream_report("B流", py_b, ru_b, pyP[a_len:], ruP[ra_len:],
                  pyM[a_len:], ruM[ra_len:], focus=21)


if __name__ == "__main__":
    main()
