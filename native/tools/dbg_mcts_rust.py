"""dbg_mcts_rust - run rust stub search with ROCO_DEBUG_DMG and dump selected stderr lines.

usage: python dbg_mcts_rust.py spec_0019.json 3 energy|dmg|mod|all
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["ROCO_DEBUG_DMG"] = "1"

import roco_engine  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "spec_0019.json"
sims = int(sys.argv[2]) if len(sys.argv) > 2 else 3
pat = sys.argv[3] if len(sys.argv) > 3 else "energy"

spec = json.loads((ROOT / "native" / "gate_specs" / name).read_text(encoding="utf-8"))
cfg = {
    "num_simulations": sims,
    "c_puct": 2.0,
    "root_noise": 0.25,
    "max_turns": 60,
    "opp_greedy": True,
    "opp_temperature": 1.0,
    "use_network_opponent": True,
    "leaf_batch_size": 1,
    "digest_trace": True,
}

err_path = ROOT / "native" / "tools" / "dbg_mcts_rust.err"
with open(err_path, "w", encoding="utf-8") as fh:
    saved = os.dup(2)
    os.dup2(fh.fileno(), 2)
    try:
        out = roco_engine.py_mcts_stub(json.dumps(spec, ensure_ascii=False), json.dumps(cfg))
    finally:
        os.dup2(saved, 2)
        os.close(saved)

res = json.loads(out)
print("counts:", res["counts"])
print("trace:", res["trace"])
lines = err_path.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"--- stderr {len(lines)} 行，筛选 '{pat}' ---")
for ln in lines:
    if pat == "all" or pat in ln:
        print(ln)
