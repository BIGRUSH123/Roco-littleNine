"""临时调试15：打印 spec_0001 index=11 双方完整 digest。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import battle_from_spec, gate_digest, run_python  # noqa: E402

spec = json.loads(Path("native/gate_specs/spec_0001.json").read_text(encoding="utf-8"))
py_d, py_w = run_python(spec)
r = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
idx = 11
print("PY  B:", json.dumps(py_d[idx]["players"][1], ensure_ascii=False)[:1200])
print()
print("RUST B:", json.dumps(r["turns"][idx]["players"][1], ensure_ascii=False)[:1200])
print()
print("PY  A active/lives:", py_d[idx]["players"][0]["active_index"], py_d[idx]["players"][0]["lives"])
print("RUST A active/lives:", r["turns"][idx]["players"][0]["active_index"], r["turns"][idx]["players"][0]["lives"])
