"""dbg_hp — 打印某回合双方指定精灵的 hp（py/rust）。

用法：env\\python.exe native/tools/dbg_hp.py <spec> <player:A|B> <idx> <turn_from> <turn_to>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pi = 0 if sys.argv[2].upper() == "A" else 1
si = int(sys.argv[3])
lo, hi = int(sys.argv[4]), int(sys.argv[5])

py_digests, _ = run_python(spec)
result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rust_digests = result["turns"]
for t in range(lo, hi + 1):
    if t >= len(py_digests) or t >= len(rust_digests):
        continue
    ps = py_digests[t]["players"][pi]["sprites"][si]
    rs = rust_digests[t]["players"][pi]["sprites"][si]
    mark = "" if ps["hp"] == rs["hp"] else "  <<<"
    print(f"t{t}: py={ps['hp']}/{ps['max_hp']} rust={rs['hp']}/{rs['max_hp']}{mark}")
