"""dbg_diff4 — 打印某回合双方指定精灵的差异明细。

用法：env\\python.exe native/tools/dbg_diff4.py <spec> <turn>
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
t = int(sys.argv[2])
py_digests, _ = run_python(spec)
result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
rust_digests = result["turns"]
for pi in (0, 1):
    for si in range(len(py_digests[t]["players"][pi]["sprites"])):
        ps = py_digests[t]["players"][pi]["sprites"][si]
        rs = rust_digests[t]["players"][pi]["sprites"][si]
        if ps == rs:
            continue
        print(f'== diff {"AB"[pi]}[{si}] {ps["name"]} (ru name {rs["name"]}) ==')
        for k in ("hp", "energy", "entry_turn"):
            if ps[k] != rs[k]:
                print(f"   {k}: py={ps[k]} ru={rs[k]}")
        for a, b in zip(ps["skills"], rs["skills"]):
            if a != b:
                print(f"   skill py={a['name']} {a['modifiers']} cd={a['cooldown']} sealed={a['sealed']}")
                print(f"   skill ru={b['name']} {b['modifiers']} cd={b['cooldown']} sealed={b['sealed']}")
        if ps.get("modifiers") != rs.get("modifiers"):
            print(f"   sprite mods py={ps['modifiers']}")
            print(f"   sprite mods ru={rs['modifiers']}")
        for a, b in zip(ps["effects"], rs["effects"]):
            if a != b:
                print(f"   eff py={a} ru={b}")
