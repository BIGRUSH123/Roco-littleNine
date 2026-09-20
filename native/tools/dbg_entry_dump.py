"""dbg_entry_dump — 对比 t 末双方全队 entry_turn。

用法：env\\python.exe native/tools/dbg_entry_dump.py <spec_id> <turn>
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

sid, t = sys.argv[1], int(sys.argv[2])
spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
py_digests, _ = run_python(spec)
result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rust_digests = result["turns"]
for pi in (0, 1):
    for si in range(3):
        pe = py_digests[t]["players"][pi]["sprites"][si]
        re_ = rust_digests[t]["players"][pi]["sprites"][si]
        mark = "" if pe["entry_turn"] == re_["entry_turn"] else "  <<<"
        print(
            f'{"AB"[pi]}[{si}] py {pe["name"]} entry={pe["entry_turn"]} '
            f'hp={pe["hp"]} | ru {re_["name"]} entry={re_["entry_turn"]} hp={re_["hp"]}{mark}'
        )
