"""dbg_mcts01 - locate first state divergence for a spec (default spec_0001)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "native" / "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

from mcts_gate import CFG, _first_diff, run_python  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "spec_0001.json"
sims = int(sys.argv[2]) if len(sys.argv) > 2 else 24
CFG["num_simulations"] = sims

spec = json.loads((ROOT / "native" / "gate_specs" / name).read_text(encoding="utf-8"))
py = run_python(spec, CFG)
ru = json.loads(roco_engine.py_mcts_stub(json.dumps(spec, ensure_ascii=False), json.dumps(CFG)))

print("py trace len", len(py["trace"]), "ru trace len", len(ru["trace"]))
for i, (pt, rt) in enumerate(zip(py["trace"], ru["trace"])):
    if pt != rt:
        print(f"trace[{i}] py={pt} ru={rt}")
        break

for i, (pd, rd) in enumerate(zip(py["digest_trace"], ru["digest_trace"])):
    if pd != rd:
        for j, (x, y) in enumerate(zip(pd, rd)):
            if x != y:
                print(f"首个状态分歧: sim={i} step={j}  actions={py['trace'][i][:j + 1]}")
                print("   ", _first_diff(x, y))
                print("--- 全部差异字段 ---")
                for pi in (0, 1):
                    for si, (sx, sy) in enumerate(zip(x["players"][pi]["sprites"],
                                                     y["players"][pi]["sprites"])):
                        for k in ("hp", "energy", "modifiers", "effects", "skills"):
                            if sx[k] != sy[k]:
                                print(f"  P{pi}S{si}.{k}: py={sx[k]} ru={sy[k]}")
                for k in ("team_counters", "counter_values", "marks", "weather"):
                    if x.get(k) != y.get(k):
                        print(f"  {k}: py={x.get(k)} ru={y.get(k)}")
                print("--- py players ---")
                for pi in (0, 1):
                    for si, sp in enumerate(x["players"][pi]["sprites"]):
                        print(f"  py P{pi}S{si} {sp['name']} hp={sp['hp']} mods={sp['modifiers']} eff={sp['effects']}")
                print("--- ru players ---")
                for pi in (0, 1):
                    for si, sp in enumerate(y["players"][pi]["sprites"]):
                        print(f"  ru P{pi}S{si} {sp['name']} hp={sp['hp']} mods={sp['modifiers']} eff={sp['effects']}")
                print("--- tc py:", x["team_counters"], " ru:", y["team_counters"])
                break
        break
