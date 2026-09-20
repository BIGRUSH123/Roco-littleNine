# -*- coding: utf-8 -*-
"""append_notes2.py — 向 PORTING_NOTES.md 追加训练提速优化章节。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "native" / "PORTING_NOTES.md"

TEXT = """
## 训练提速优化（2026-09-18 续）：leaf_batch 是最大杠杆，直连/队列在大 leaf 下打平

### 瓶颈定性（本盒 RTX 5060 Ti / 20 核 / Windows WDDM）
- 每次 evaluator 调用有 **~25ms 固定成本**（CPU 端 list[dict] 堆叠 + H2D/D2H 拷贝
  + WDDM 内核同步），与批大小弱相关。sims=100/leaf=16 时每决策 ~24 次调用
  → 自博墙钟被调用次数支配。
- 16 worker 共享一个 CUDA context（队列拓扑）时延迟来自排队；16 个独立
  context（直连拓扑）时来自 WDDM 上下文切换——**leaf=16 下直连反而更慢**
  （5.2/s vs 队列 9.2/s）。
- rust 引擎单进程 44-53ms/决策（含编码与 numpy 转换税，stub 评估器实测），
  py 引擎 ~86ms；但高并发下该差异被共享的每调用固定成本淹没。

### 吞吐全景（spec_0001 阵容 × 16 种子，同种子跨配置可比，2 局/worker）
| 配置 | decisions/s | vs 队列基线 |
|---|---|---|
| queue-rust leaf=16 w=16（≈exp13 拓扑+rust 引擎） | 9.2 | 1.00x |
| direct-rust leaf=16 w=16 | 5.2 | 0.57x |
| direct-rust leaf=64 w=16 | 14.3 | 1.56x |
| direct-rust leaf=128 w=16 | 25.3 | 2.75x |
| direct-py leaf=16 w=16 | 11.0 | 1.20x |
| queue-py leaf=64 w=16 | 23.1 | 2.51x |
| queue-py leaf=128 w=16 | 33.6 | 3.65x |
| queue-rust leaf=128 w=16 | 31.1 | 3.38x |
| direct-py leaf=128 w=16 | 32.8 | 3.57x |
| **direct-py leaf=256 w=16** | **36.3** | **3.95x** |
| direct-py leaf=128 w=8 | 33.1 | 3.60x |

结论：**leaf_batch_size 16→128 一项 ≈ 2.3-2.5x 真实训练吞吐**（exp13 实产
14.7/s → 同盒折算 ~33/s）；直连与队列在 leaf≥128 后打平（±8%）；
worker 8 与 16 无差异（共享饱和），8 个可省一半 CUDA context 显存。

### 交付的接入件（对只读四文件零改动）
1. `backend/engine/ai/core/evaluator.py`：QueuePolicyEvaluator env 门控直连
   （ROCO_SELFPLAY_EVAL=direct + ROCO_SELFPLAY_MODEL/DEVICE/TORCH_THREADS），
   构造签名不变，未设 env 时行为不变。
2. `backend/engine/ai/rust_selfplay_hook.py`：ROCO_SELFPLAY_GAME=rust 时把
   worker 内 `_play_one_rl_battle` 换成 rust 引擎 shim（team/item 沿用 worker
   random 流，battle_seed 供 rust RNG；记录 6 元组同构）。挂载点在 evaluator
   模块底部 install_if_enabled()——worker 进程先导 evaluator 后延迟导 train，
   patch 生效；主进程 train 自己的 def 后定义覆盖 patch，不受影响。
3. rust `py_selfplay_game` cfg 支持 draw_margin/gamma/tanh_k（lib.rs）。
4. 冒烟：native/tools/smoke_hook.py（hook 生效 + 直连激活 + 单局正常）。
   基准：native/tools/bench_selfplay_pool.py（三拓扑 sweep）。

### 推荐训练配置（exp13 参数基础上）
```
--leaf-batch-size 128 --inference-batch-size 256   # CLI 即可，零代码改动
# 可选直连（省排队、少一跳 pickle）：
ROCO_SELFPLAY_EVAL=direct ROCO_SELFPLAY_MODEL=checkpoints/exp16/model_rl.pt
ROCO_SELFPLAY_DEVICE=cuda ROCO_SELFPLAY_TORCH_THREADS=2
# 可选 rust 引擎（与 py 版同吞吐，消除 mcts_sim 代理污染类问题；全 rust 路径）：
ROCO_SELFPLAY_GAME=rust
```
预期：53.4h（exp13）→ ~22-24h；train 阶段占 ~45% 成为新瓶颈，下一步是
训练侧 GPU 利用率（现 ~2.2 step/s）。

### 备注
- leaf 变大会改变 MCTS 批组成 → torch 结果 ulp 级不同 → 同种子轨迹不再
  与 leaf=16 逐位一致（训练语义不受影响；引擎逐位一致性由 gate_phase5 在
  固定批组成下保证）。
- rust 引擎版与 py 版大 leaf 下吞吐相当；rust 的收益（引擎侧 1.4x）要在
  消除每调用固定成本后（Linux/MPS、CUDA graphs、或推理进 rust 进程）才能
  兑现为端到端优势。
"""

with io.open(P, "a", encoding="utf-8", newline="\n") as f:
    f.write(TEXT)
print("appended", len(TEXT), "chars")
