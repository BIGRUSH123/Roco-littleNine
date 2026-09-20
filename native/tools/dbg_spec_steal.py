"""dbg_spec_steal - locate steal effects in a spec JSON."""

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
blob = json.dumps(spec, ensure_ascii=False)
start = 0
while True:
    i = blob.find('"steal"', start)
    if i < 0:
        break
    start = i + 1
    print(blob[max(0, i - 500):i + 250].replace("\n", " "))
    print("---")
