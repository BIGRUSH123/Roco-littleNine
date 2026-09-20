"""debug_gate — 对拍分歧定位工具。

用法：env\\python.exe native/tools/debug_gate.py <spec_path>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import gate_digest, battle_from_spec, run_python  # noqa: E402


def main() -> None:
    spec_path = sys.argv[1]
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    py_digests, py_winner = run_python(spec)
    result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
    rust_digests = result["turns"]
    print(f"py  : winner={py_winner} turns={len(py_digests)}")
    print(f"rust: winner={result['winner'] or None} turns={len(rust_digests)}")

    n = min(len(py_digests), len(rust_digests))
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else None
    for i in range(n):
        pd, rd = py_digests[i], rust_digests[i]
        if idx is not None and i != idx:
            continue
        if pd != rd or idx is not None:
            print(f"first divergent digest at index {i} (turn={pd.get('turn')})")
            _walk(pd, rd, "")
            return
    if len(py_digests) != len(rust_digests):
        i = min(len(py_digests), len(rust_digests))
        print(f"digests identical up to index {i}; counts differ")
        if len(py_digests) > i:
            print("py  extra tail:", json.dumps(py_digests[i - 1:i + 1], ensure_ascii=False, default=str)[:400])
        if len(rust_digests) > i:
            print("rust extra tail:", json.dumps(rust_digests[i - 1:i + 1], ensure_ascii=False, default=str)[:400])


def _walk(a, b, path: str) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                print(f"  {path}.{k}: only rust = {json.dumps(b[k], ensure_ascii=False)[:200]}")
            elif k not in b:
                print(f"  {path}.{k}: only py   = {json.dumps(a[k], ensure_ascii=False)[:200]}")
            else:
                _walk(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            print(f"  {path}: len py={len(a)} rust={len(b)}")
            print(f"    py  = {json.dumps(a, ensure_ascii=False, default=str)[:240]}")
            print(f"    rust= {json.dumps(b, ensure_ascii=False, default=str)[:240]}")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, f"{path}[{i}]")
    elif a != b:
        print(f"  {path}: py={a!r} rust={b!r}")


if __name__ == "__main__":
    main()
