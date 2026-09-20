"""dbg_replay_sim — 把 MCTS 里第 N 轮仿真的分歧从树里剥离出来复现。

做法：先用 mcts_gate 的钩子跑一遍 py MCTS，取出 trace（A 动作）与
digest_trace（含 B 动作），拼出 0..N 轮仿真的 (A,B) 动作序列，然后在
「固定动作逐回合」路径上分别跑 py / rust 并逐回合比对。

注意：MCTS 的**对局 RNG 不随仿真回滚**（跨仿真单调推进），所以复现第 N 轮
必须把 0..N-1 轮也一起重放，RNG 才落在同一位置。B 侧换人策略在重放里用
「首只存活」，与 py 仿真里的网络策略头不同——若前序轮次因此分歧，比对会在
更早处暴露（那时说明是换人策略差异，而非本次要查的根因）。

用法：env\\python.exe native/tools/dbg_replay_sim.py <spec_no> <sim_idx> [--dump]
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
from dbg_fixed_turn import _compare, _dump  # noqa: E402

spec_no = int(sys.argv[1])
sim_idx = int(sys.argv[2])
dump = "--dump" in sys.argv

spec = json.loads((ROOT / "native" / "gate_specs" / f"spec_{spec_no:04d}.json").read_text("utf-8"))
cfg = dict(G.CFG)
cfg["num_simulations"] = int(os.environ.get("ROCO_SIMS", "200"))
py = G.run_python(spec, cfg)

sims: list[list[tuple[int, int]]] = []
for i in range(sim_idx + 1):
    a_acts = py["trace"][i]
    steps = py["digest_trace"][i]
    if len(a_acts) != len(steps):
        print(f"sim {i}: A 动作数 {len(a_acts)} != 步数 {len(steps)} —— 有步进失败，无法重放")
        print("   A 动作:", a_acts)
        print("   B 动作:", [s["b"] for s in steps])
        sys.exit(1)
    sims.append([(int(a), int(s["b"])) for a, s in zip(a_acts, steps)])

print(f"重放 0..{sim_idx} 轮，共 {sum(len(s) for s in sims)} 个回合")
_compare(spec, sims, dump=dump, expected=py["digest_trace"][: sim_idx + 1])
