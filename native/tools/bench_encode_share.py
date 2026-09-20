"""bench_encode_share — 测 encode_battle_state 在 MCTS 搜索中的真实占比。

三块输出（UTF-8 写文件，避免 PowerShell 管道乱码）：
1. cProfile：mcts_search(100 sims, batch=16) 内 encode / torch evaluate /
   引擎 step_battle 的 cumtime 占比与调用次数；
2. 中盘状态 encode 微基准（跑 15 个固定回合后的富状态，对比根位置的 86µs）；
3. 结论行：若 rust 侧不移植 encoder，每叶需把整棵状态物化成 Python 对象
   的下限成本对比（用 battle.save_mutable_state+restore 或 encode 前置
   对象构建近似）。

用法：env\\python.exe native/tools/bench_encode_share.py [out.txt]
"""

from __future__ import annotations

import cProfile
import io
import json
import pstats
import random
import sys
import time
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[2]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "_bench_encode.txt"
LINES: list[str] = []


def log(msg: str) -> None:
    LINES.append(msg)
    print(msg, flush=True)


import numpy as np  # noqa: E402
import torch  # noqa: E402

from backend.engine.ai.core.encoder import encode_battle_state  # noqa: E402
from backend.engine.ai.core.evaluator import TorchEvaluator  # noqa: E402
from backend.engine.ai.core.mcts import NetworkPolicyAgent, mcts_search  # noqa: E402
from backend.engine.ai.core.model import ModularBattleNet  # noqa: E402
from backend.sim.agent import RuleAgent  # noqa: E402
from backend.sim.factory import SimFactory  # noqa: E402
from backend.engine.test_rust_gate import battle_from_spec  # noqa: E402

SPEC = json.loads((ROOT / "native" / "gate_specs" / "spec_0001.json").read_text("utf-8"))
SIMS = 100
BATCH = 16
REPEATS = 3

torch.set_num_threads(1)
random.seed(2)
np.random.seed(2)
torch.manual_seed(2)

battle = battle_from_spec(SPEC)
model = ModularBattleNet().to("cpu")
model.eval()
stub = TorchEvaluator(model, device="cpu")
opp = NetworkPolicyAgent(evaluator=stub, greedy=True)


def one_search() -> None:
    b = battle_from_spec(SPEC)
    b.player_a.active_index = RuleAgent("A", b.player_a).choose_lead(b)
    b.player_b.active_index = RuleAgent("B", b.player_b).choose_lead(b)
    b._invalidate_ctx_team_cache()
    mcts_search(
        b, None, SimFactory(), opp,
        num_simulations=SIMS, c_puct=2.0, root_noise=0.25,
        max_turns=60, opp_greedy=True, evaluator=stub,
        draw_margin=0.15, gamma=1.0, tanh_k=0.0,
        leaf_batch_size=BATCH,
    )


# 预热（torch 首次 forward、编码缓存等）
one_search()

# ── 1. cProfile：搜索内占比 ──
prof = cProfile.Profile()
t0 = time.perf_counter()
prof.enable()
for _ in range(REPEATS):
    one_search()
prof.disable()
total = time.perf_counter() - t0

stats = pstats.Stats(prof, stream=io.StringIO())
rows = {}
for (filename, lineno, name), (cc, nc, tt, ct, callers) in stats.stats.items():
    short = name
    if "encode_battle_state" in name:
        short = "encode_battle_state"
    elif "evaluate_batch" in name and "Torch" in filename or "evaluate" in name and "evaluator" in filename:
        short = "torch evaluate(_batch)"
    elif "execute_turn_headless" in name:
        short = "execute_turn_headless"
    elif "_step_battle" in name:
        short = "_step_battle"
    elif "save_mutable_state" in name:
        short = "save_mutable_state"
    elif "restore_mutable_state" in name:
        short = "restore_mutable_state"
    else:
        continue
    acc = rows.setdefault(short, [0.0, 0])
    acc[0] += ct
    acc[1] += nc

log(f"=== cProfile：{REPEATS} 次 mcts_search（{SIMS} sims, batch={BATCH}）===")
log(f"总耗时 {total*1000:.1f} ms（含 profile 开销，作分母仅供参考）")
for name, (ct, nc) in sorted(rows.items(), key=lambda kv: -kv[1][0]):
    log(f"  {name:26s} cumtime={ct*1000:8.1f} ms  calls={nc:6d}  share={ct/total*100:5.1f}%")

# ── 2. 中盘富状态 encode 微基准 ──
log("")
log("=== encode 微基准 ===")
b = battle_from_spec(SPEC)
b.player_a.active_index = RuleAgent("A", b.player_a).choose_lead(b)
b.player_b.active_index = RuleAgent("B", b.player_b).choose_lead(b)
b._invalidate_ctx_team_cache()
# 推进 ~15 个固定回合让状态变富（效果/冷却/异常累积）
from backend.sim.agent import Action  # noqa: E402
for _ in range(15):
    if b.is_finished:
        break
    b.execute_turn_headless(
        RuleAgent("A", b.player_a), RuleAgent("B", b.player_b),
    )

for label, bb in (("根位置", battle_from_spec(SPEC)), ("15回合后", b)):
    encode_battle_state(bb)  # 预热缓存
    n = 300
    t0 = time.perf_counter()
    for _ in range(n):
        encode_battle_state(bb)
    dt = time.perf_counter() - t0
    log(f"  {label:8s} encode: {dt/n*1e6:7.1f} µs/次（{n} 次平均）")

# ── 3. 不移植 encoder 时的替代成本：状态物化近似 ──
log("")
log("=== 替代成本近似：每叶把状态交给 py 的开销 ===")
# 近似 1：save_mutable_state（O(状态) 的字段级快照，rust→py 物化的下限之一）
t0 = time.perf_counter()
for _ in range(300):
    snap = b.save_mutable_state()
dt = time.perf_counter() - t0
log(f"  save_mutable_state 快照: {dt/300*1e6:7.1f} µs/次（rust 侧仍需再做一次跨语言物化）")
# 近似 2：状态摘要 digest（递归遍历全部精灵/效果/技能的纯读取）
from backend.engine.test_rust_gate import gate_digest  # noqa: E402
t0 = time.perf_counter()
for _ in range(300):
    gate_digest(b)
dt = time.perf_counter() - t0
log(f"  gate_digest 全状态遍历: {dt/300*1e6:7.1f} µs/次（每叶一次状态→py 的读取下限）")

OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
print(f"\nwritten {OUT}")
