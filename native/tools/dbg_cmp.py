"""dbg_cmp — 复现 triage 的比较流程定位挂起点。

用法：env\\python.exe native/tools/dbg_cmp.py <spec_path>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402


def first_diff_path(a, b, path: str = "") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}:missing"
            p = first_diff_path(a[k], b[k], f"{path}.{k}")
            if p:
                return p
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}:len"
        for i, (x, y) in enumerate(zip(a, b)):
            p = first_diff_path(x, y, f"{path}[{i}]")
            if p:
                return p
        return ""
    if a != b:
        return path
    return ""


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    t = time.perf_counter()
    py_digests, py_winner = run_python(spec)
    print(f"py done {time.perf_counter() - t:.2f}s turns={len(py_digests)}", flush=True)
    t = time.perf_counter()
    result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
    print(f"rust done {time.perf_counter() - t:.2f}s turns={len(result['turns'])}", flush=True)
    rust_digests = result["turns"]
    t = time.perf_counter()
    eq = rust_digests == py_digests
    print(f"compare done {time.perf_counter() - t:.2f}s eq={eq}", flush=True)
    t = time.perf_counter()
    first = next((i for i, (a, b) in enumerate(zip(py_digests, rust_digests)) if a != b), None)
    print(f"first={first} {time.perf_counter() - t:.2f}s", flush=True)
    if first is not None:
        t = time.perf_counter()
        p = first_diff_path(py_digests[first], rust_digests[first])
        print(f"path={p} {time.perf_counter() - t:.2f}s", flush=True)


if __name__ == "__main__":
    main()
