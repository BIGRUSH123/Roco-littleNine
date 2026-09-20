"""dbg_batch6 — 批量打印多个 spec 的首回合差异明细。

用法：env\\python.exe native/tools/dbg_batch6.py spec_id:turn ...
例如：dbg_batch6.py 0137:13 0144:2
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json  # noqa: E402

import roco_engine  # noqa: E402

from backend.engine.test_rust_gate import run_python  # noqa: E402


def diff_one(sid: str, t: int) -> None:
    spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
    py_digests, _ = run_python(spec)
    result = json.loads(roco_engine.py_run_battle(json.dumps(spec, ensure_ascii=False)))
    rust_digests = result["turns"]
    if t >= len(py_digests) or t >= len(rust_digests):
        print(f"== spec_{sid} t{t}: out of range py={len(py_digests)} rust={len(rust_digests)}")
        return
    print(f"== spec_{sid} t{t} ==")
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
            if len(ps["effects"]) != len(rs["effects"]):
                print(f"   eff len py={len(ps['effects'])} ru={len(rs['effects'])}")
                print(f"   eff py={ps['effects']}")
                print(f"   eff ru={rs['effects']}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        sid, _, t = arg.partition(":")
        diff_one(sid, int(t))
