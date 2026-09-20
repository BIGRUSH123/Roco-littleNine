"""dbg_mcts37e - capture rust ROCO_DEBUG_DMG stderr for spec 0037 first sim."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["ROCO_DEBUG_DMG"] = "1"

import roco_engine  # noqa: E402

spec = json.loads((ROOT / "native" / "gate_specs" / "spec_0037.json").read_text(encoding="utf-8"))
cfg = {
    "num_simulations": 1,
    "c_puct": 2.0,
    "root_noise": 0.25,
    "max_turns": 60,
    "opp_greedy": True,
    "opp_temperature": 1.0,
    "use_network_opponent": True,
    "leaf_batch_size": 1,
    "digest_trace": True,
}

err_path = ROOT / "native" / "tools" / "dbg_mcts37e.err"
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
d0 = res["digest_trace"][0][0]
print("rust post A:", d0["players"][0]["sprites"][0])
print("rust post B:", d0["players"][1]["sprites"][0])
print("rust tc:", d0["team_counters"])

lines = err_path.read_text(encoding="utf-8", errors="replace").splitlines()
print("--- stderr 行数:", len(lines))
for ln in lines[:40]:
    print(ln)
