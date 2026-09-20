"""dbg_norm — 定位 triage 聚合逻辑的挂起点。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402
from native.tools.gate_triage import first_diff_path, norm  # noqa: E402

spec = json.loads(Path("native/gate_specs/spec_0002.json").read_text(encoding="utf-8"))
py_digests, py_winner = run_python(spec)
result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rust_digests = result["turns"]
print("runs done", flush=True)

t = time.perf_counter()
first = next((i for i, (a, b) in enumerate(zip(py_digests, rust_digests)) if a != b), None)
print(f"first={first} {time.perf_counter() - t:.3f}s", flush=True)

t = time.perf_counter()
p = first_diff_path(py_digests[first], rust_digests[first])
print(f"path={p!r} {time.perf_counter() - t:.3f}s", flush=True)

t = time.perf_counter()
k = norm(p)
print(f"norm={k!r} {time.perf_counter() - t:.3f}s", flush=True)
