"""dbg_mcts_step — 打印某个 spec / 仿真 / 步 上 py 与 rust 的完整状态摘要差异。

用法：env\\python.exe native/tools/dbg_mcts_step.py <spec_no> <sim_idx> [max_steps]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import roco_engine  # noqa: E402

import mcts_gate as G  # noqa: E402

spec_no = int(sys.argv[1])
sim_idx = int(sys.argv[2])
max_steps = int(sys.argv[3]) if len(sys.argv) > 3 else 6

spec = json.loads((ROOT / "native" / "gate_specs" / f"spec_{spec_no:04d}.json").read_text("utf-8"))
cfg = dict(G.CFG)
cfg["num_simulations"] = int(sys.argv[4]) if len(sys.argv) > 4 else 200
py = G.run_python(spec, cfg)
ru = json.loads(
    roco_engine.py_mcts_stub(json.dumps(spec, ensure_ascii=False), json.dumps(cfg))
)

print("=== 根节点计数 ===")
print("py  ", py["counts"])
print("ru  ", ru["counts"])
print("=== 概率位 ===")
print("py  ", py["probs_bits"])
print("ru  ", ru["probs_bits"])

for i in range(sim_idx, min(sim_idx + max_steps, 8 if max_steps <= 0 else sim_idx + max_steps)):
    pt, rt = py["trace"][i], ru["trace"][i]
    pd, rd = py["digest_trace"][i], ru["digest_trace"][i]
    print(f"--- sim {i}: A动作 py={pt} ru={rt}  步数 py={len(pd)} ru={len(rd)}")
    for j in range(max(len(pd), len(rd))):
        if j >= len(pd) or j >= len(rd):
            print(f"    step {j}: 仅一侧有记录")
            break
        x, y = pd[j], rd[j]
        head = (
            f"    step {j}: b py={x['b']} ru={y['b']} | mti py={x['mti']} ru={y['mti']}"
            f" | np py={x['np']} ru={y['np']}"
        )
        if x == y:
            print(head + "  [一致]")
            continue
        print(head + "  [DIFF]")
        print("      py  :", json.dumps(x["d"], ensure_ascii=False)[:3000])
        print("      rust:", json.dumps(y["d"], ensure_ascii=False)[:3000])
        break
