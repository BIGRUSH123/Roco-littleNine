"""dbg_eff_trace — 追踪某精灵每回合 effects/modifiers 演变。

用法：env\\python.exe native/tools/dbg_eff_trace.py <spec_id> <A|B> <idx> <t0> <t1>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402

sid = sys.argv[1]
pi = 0 if sys.argv[2].upper() == "A" else 1
si = int(sys.argv[3])
lo, hi = int(sys.argv[4]), int(sys.argv[5])

spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
py_digests, _ = run_python(spec)
result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rust_digests = result["turns"]

prev_py = prev_ru = None
for t in range(lo, min(hi + 1, len(py_digests), len(rust_digests))):
    ps = py_digests[t]["players"][pi]["sprites"][si]
    rs = rust_digests[t]["players"][pi]["sprites"][si]
    if ps["effects"] != prev_py or rs["effects"] != prev_ru:
        mark = "" if ps["effects"] == rs["effects"] else "  <<<"
        print(f"t{t}{mark}")
        if ps["effects"] != prev_py:
            print(f"   py eff={ps['effects']}")
        if rs["effects"] != prev_ru:
            print(f"   ru eff={rs['effects']}")
    prev_py, prev_ru = ps["effects"], rs["effects"]
