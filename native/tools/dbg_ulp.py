"""dbg_ulp — 从 P[101] 反推访问计数与求和顺序，定位 1ulp 分歧来源。"""
from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_dbg_ulp_log.txt", "w", encoding="utf-8")


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

N = 17


def sum_seq(a):
    r = np.float32(0)
    for x in a:
        r = np.float32(r + x)
    return float(r)


def sum_pairwise8(a):
    """rust 当前实现：8 累加器 → 合并 → 尾部顺序加到合并结果。"""
    n = len(a)
    if n < 8:
        return sum_seq(a)
    r = [np.float32(x) for x in a[:8]]
    i = 8
    stop = n - (n % 8)
    while i < stop:
        for k in range(8):
            r[k] = np.float32(r[k] + a[i + k])
        i += 8
    res = np.float32(np.float32(np.float32(r[0] + r[1]) + np.float32(r[2] + r[3]))
                     + np.float32(np.float32(r[4] + r[5]) + np.float32(r[6] + r[7])))
    while i < n:
        res = np.float32(res + a[i])
        i += 1
    return float(res)


def sum_pairwise8_tail_r0(a):
    """变体：尾部加到 r[0]。"""
    n = len(a)
    if n < 8:
        return sum_seq(a)
    r = [np.float32(x) for x in a[:8]]
    i = 8
    stop = n - (n % 8)
    while i < stop:
        for k in range(8):
            r[k] = np.float32(r[k] + a[i + k])
        i += 8
    for k in range(stop, n):
        r[0] = np.float32(r[0] + a[k])
    res = np.float32(np.float32(np.float32(r[0] + r[1]) + np.float32(r[2] + r[3]))
                     + np.float32(np.float32(r[4] + r[5]) + np.float32(r[6] + r[7])))
    return float(res)


CANDS = {"seq": sum_seq, "pw8": sum_pairwise8, "pw8r0": sum_pairwise8_tail_r0}


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


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

    pyP = py["P"]
    ruP = np.asarray(ru["P"])
    print(f"py rows={pyP.shape[0]} rust rows={ruP.shape[0]}", flush=True)

    # 找出所有不一致的行
    bad = [i for i in range(min(len(pyP), ruP.shape[0]))
           if not np.array_equal(pyP[i], ruP[i])]
    print(f"不一致行: {bad}", flush=True)

    for k in bad[:3]:
        a = pyP[k]
        b = ruP[k]
        mask = np.asarray(py["M"][k])
        nz = [i for i in range(N) if mask[i] > 0]
        print(f"── 行 {k}: 支撑={nz}", flush=True)
        for i in range(N):
            if a[i] != b[i]:
                print(f"    [{i}] py={a[i]!r} ({struct.pack('f', float(a[i])).hex()}) "
                      f"rust={b[i]!r} ({struct.pack('f', float(b[i])).hex()})", flush=True)
        # 反推访问计数：P = (c/T)/s，枚举 T
        for T in range(16, 40):
            c = [int(round(float(a[i]) * T)) for i in range(N)]
            if any(c[i] < 0 for i in range(N)):
                continue
            c = [c[i] if mask[i] > 0 else 0 for i in range(N)]
            if sum(c) != T:
                continue
            v = [np.float32(np.float32(c[i]) / np.float32(T)) for i in range(N)]
            hits_py = [n for n, fn in CANDS.items()
                       if all(f32(v[i] / np.float32(fn(v))) == float(a[i])
                              for i in range(N))]
            hits_ru = [n for n, fn in CANDS.items()
                       if all(f32(v[i] / np.float32(fn(v))) == float(b[i])
                              for i in range(N))]
            if hits_py or hits_ru:
                print(f"    T={T} counts={[c[i] for i in nz]} "
                      f"py命中={hits_py} rust命中={hits_ru}", flush=True)
                if hits_py:
                    sv = CANDS[hits_py[0]](v)
                    print(f"    py s={sv!r} numpy.sum={np.sum(np.array(v, np.float32))!r}",
                          flush=True)
                break


if __name__ == "__main__":
    main()
