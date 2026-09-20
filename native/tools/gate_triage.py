"""gate_triage2 — 按 spec 列表分诊首分歧字段。

用法：env\\python.exe native/tools/gate_triage2.py 0003 0004 ...
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


def norm(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


buckets: Counter[str] = Counter()
for sid in sys.argv[1:]:
    spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
    py_digests, py_winner = run_python(spec)
    result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
    rust_digests = result["turns"]
    rust_winner = result.get("winner") or None
    if rust_winner == py_winner and rust_digests == py_digests:
        print(f"spec_{sid}: PASS now")
        continue
    first = next(
        (i for i, (a, b) in enumerate(zip(py_digests, rust_digests)) if a != b), None
    )
    if first is None:
        key = "count/winner-only"
    else:
        key = norm(first_diff_path(py_digests[first], rust_digests[first]))
    buckets[key] += 1
    print(f"spec_{sid}: t{first} {key}")

print("\n聚合：")
for k, n in buckets.most_common():
    print(f"  {n:>3}  {k}")
