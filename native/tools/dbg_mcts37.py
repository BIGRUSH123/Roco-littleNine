"""dbg_mcts37 — 定位 spec_0037 MCTS 首个仿真步进的状态分歧。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os = __import__("os")
os.chdir(ROOT)  # data/skills 为 CWD 相对路径，必须与 gate 同 CWD
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

import mcts_gate  # noqa: E402
from mcts_gate import CFG, _first_diff, run_python  # noqa: E402

spec_path = ROOT / "native" / "gate_specs" / "spec_0037.json"
spec = json.loads(spec_path.read_text(encoding="utf-8"))

sims = int(sys.argv[1]) if len(sys.argv) > 1 else 1
CFG["num_simulations"] = sims

py = run_python(spec, CFG)
ru = json.loads(roco_engine.py_mcts_stub(
    json.dumps(spec, ensure_ascii=False), json.dumps(CFG)))

print("py trace[0]:", py["trace"][0] if py["trace"] else None)
print("ru trace[0]:", ru["trace"][0] if ru["trace"] else None)
print("py counts:", py["counts"])
print("ru counts:", ru["counts"])
print("np_pos", py["np_pos"], ru["np_pos"], "rng_mti", py["rng_mti"], ru["rng_mti"])

if py["digest_trace"] and ru["digest_trace"]:
    p0 = py["digest_trace"][0][0]
    r0 = ru["digest_trace"][0][0]
    print("首步差异:", _first_diff(p0, r0))
    print("--- py sprite0 ---")
    print(json.dumps(p0["players"][0]["sprites"][0], ensure_ascii=False)[:1200])
    print("--- ru sprite0 ---")
    print(json.dumps(r0["players"][0]["sprites"][0], ensure_ascii=False)[:1200])
    print("--- py globals ---")
    print(json.dumps({k: v for k, v in p0.items() if k not in ("players",)}, ensure_ascii=False))
    print("--- ru globals ---")
    print(json.dumps({k: v for k, v in r0.items() if k not in ("players",)}, ensure_ascii=False))
    print("--- py p1 sprite0 ---")
    print(json.dumps(p0["players"][1]["sprites"][0], ensure_ascii=False)[:800])
    print("--- ru p1 sprite0 ---")
    print(json.dumps(r0["players"][1]["sprites"][0], ensure_ascii=False)[:800])
