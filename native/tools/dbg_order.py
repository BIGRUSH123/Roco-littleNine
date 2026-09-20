"""dbg_order — 追踪双方每回合技能顺序与 drive/swift/power mods。

用法：env\\python.exe native/tools/dbg_order.py <spec_id> <player> <idx> <t_from> <t_to>
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

for t in range(lo, min(hi + 1, len(py_digests), len(rust_digests))):
    ps = py_digests[t]["players"][pi]["sprites"][si]
    rs = rust_digests[t]["players"][pi]["sprites"][si]
    po = [s["name"] for s in ps["skills"]]
    ro = [s["name"] for s in rs["skills"]]
    pm = {s["name"]: s["modifiers"] for s in ps["skills"]}
    rm = {s["name"]: s["modifiers"] for s in rs["skills"]}
    mark = "" if (po == ro and pm == rm) else "  <<<"
    print(f"t{t} py order={po} mods={pm}")
    print(f"     ru order={ro} mods={rm}{mark}")
