"""dbg_first_div — 扫描 py/rust 全部 digest，找第一个分歧回合 T* 并全量输出。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

_logf = open(ROOT / "native" / "tools" / "_dbg_first_div_log.txt", "w", encoding="utf-8")


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


def all_diffs(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k} rust-only={b[k]!r}")
            elif k not in b:
                out.append(f"{path}.{k} py-only={a[k]!r}")
            else:
                out += all_diffs(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path} 长度 py={len(a)} rust={len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            out += all_diffs(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: py={a!r} rust={b!r}")
    return out


def digest_summary(d):
    if isinstance(d, str):
        d = json.loads(d)
    lines = [f"turn={d['turn']} winner={d['winner']}"]
    for nm, p in zip("AB", d["players"]):
        lines.append(f"  {nm}: lives={p['lives']} active={p['active_index']}")
        for i, s in enumerate(p["sprites"]):
            eff = ";".join(f"{e[0]}({e[2]})" for e in s["effects"] if e[0])
            lines.append(f"    [{i}] {s['name']} hp={s['hp']}/{s['max_hp']} en={s['energy']}"
                         + (f" eff={eff}" if eff else ""))
    return "\n".join(lines)


def main() -> None:
    ckpt = ROOT / "checkpoints" / "exp16" / "model_rl.pt"
    model = ModularBattleNet.load(str(ckpt), device="cpu")
    model.eval()
    torch_ev = TorchEvaluator(model, "cpu")
    spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
    seed = spec["seed"] + 1

    py = py_game(spec, torch_ev, seed, 24, 1.0, 16)
    print(f"py: turns={py['turns']} winner={py['winner']} lives={py['lives']} "
          f"active={py['active']} a_len={py['a_len']}", flush=True)
    adapter = _RustEvalAdapter(torch_ev)
    ru = rust_game(spec, adapter, adapter, 24, 1.0, 16)
    print(f"rust: turns={ru['turns']} winner={ru['winner']} lives={ru['lives']} "
          f"active={ru['active']}", flush=True)

    py_dig = py["turn_digests"]
    ru_dig = json.loads(ru["digests"]) if isinstance(ru["digests"], str) else ru["digests"]
    n = min(len(py_dig), len(ru_dig))
    t_star = -1
    for t in range(n):
        if all_diffs(py_dig[t], ru_dig[t]):
            t_star = t
            break
    print(f"═" * 20 + f" 首个分歧 digest 下标 T*={t_star}（共比到 {n - 1}） " + "═" * 20,
          flush=True)
    if t_star < 0:
        print("前段全部一致", flush=True)
        return
    for t in (t_star - 1, t_star):
        print(f"── turn {t} py:", flush=True)
        print(digest_summary(py_dig[t]), flush=True)
        print(f"── turn {t} rust:", flush=True)
        print(digest_summary(ru_dig[t]), flush=True)
    ds = all_diffs(py_dig[t_star], ru_dig[t_star])
    print(f"T*={t_star} 全量差异 {len(ds)} 条：", flush=True)
    for d in ds:
        print("  ", d, flush=True)


if __name__ == "__main__":
    main()
