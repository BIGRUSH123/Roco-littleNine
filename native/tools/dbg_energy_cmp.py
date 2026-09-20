"""dbg_energy_cmp — 对比 py/rust 每回合 B 精灵 0 号位能量与 hp。

用法：env\\python.exe native/tools/dbg_energy_cmp.py <spec>
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

spec = json.loads((ROOT / sys.argv[1]).resolve().read_text(encoding="utf-8"))
py_d, _ = run_python(spec)
r = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
for t in range(0, min(len(py_d), len(r["turns"]))):
    pb = py_d[t]["players"][1]["sprites"][0]
    rb = r["turns"][t]["players"][1]["sprites"][0]
    diff = "" if (pb["energy"] == rb["energy"] and pb["hp"] == rb["hp"]) else "  <<<"
    print(f"t{t}: py e={pb['energy']} hp={pb['hp']} | rust e={rb['energy']} hp={rb['hp']}{diff}")
