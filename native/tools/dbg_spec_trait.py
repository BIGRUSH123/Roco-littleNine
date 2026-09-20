"""dbg_spec_trait - list sprite abilities of a spec + dump matching trait JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

name = sys.argv[1] if len(sys.argv) > 1 else "spec_0019.json"
spec = json.loads((ROOT / "native" / "gate_specs" / name).read_text(encoding="utf-8"))
for pi, ps in enumerate(spec["players"]):
    for si, ss in enumerate(ps["sprites"]):
        print(f"P{pi}S{si} {ss['name']} ability={ss.get('ability')!r} ability_id={ss.get('ability_id')} bloodline={ss.get('bloodline')!r}")

if len(sys.argv) > 2:
    key = sys.argv[2]
    for pat in (ROOT / "data" / "traits").glob("*.json"):
        txt = pat.read_text(encoding="utf-8")
        if key in txt or key in pat.name:
            print(f"=== {pat.name} ===")
            print(txt[:2500])
