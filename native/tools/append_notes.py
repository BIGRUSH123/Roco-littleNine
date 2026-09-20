# -*- coding: utf-8 -*-
"""append_notes.py — 向 native/PORTING_NOTES.md 追加阶段5a/5b结论（UTF-8 no BOM 追加）。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "native" / "PORTING_NOTES.md"

TEXT = """
## 阶段5a/5b 收官（2026-09-18）：gate_phase5 一致性 PASS + 速度门结论

### 一致性（铁律门）✅
- `gate_phase5.py`（不带 --skip-consistency）：**一致性 PASS — 60 回合 / 124 样本逐位一致**，
  py/rust 双方整局 digest、P、M、v、ast_tokens、sprite_states 全部相同，
  结局相同（60 回合超时平局，lives=[2,2]）。
- `dbg_simpaths.py`：**124/124 次搜索的每条模拟动作路径完全一致**。

### 本轮三处修复
1. **rust `policy_select_idx` 采样语义**（mcts.rs）：旧实现 `cutoff = u*total` + f64 累加
   与 py `np.random.choice`（p→f64 顺序 cumsum → /cdf[-1] → searchsorted right）在边界处
   有 1e-7 量级翻转概率；回合 22 的换宠 vs 技能分叉即此。现按 py 精确镜像：
   f32 成对求和归一化 → f32 逐元素除 → f64 widen → f64 顺序 cumsum → `cdf[i]/total <= u`
   计数式 searchsorted。注意 py 在 choice 内部会把 p 转 float64 再 cumsum。
2. **rust B 视图观察者 owner 翻转**（engine.rs `flip_owner_teams` + selfplay.rs `decide`）：
   py 观察者 owner = id(精灵对象)，交换 player_a/b 天然免疫；rust owner 是 (team, idx)
   静态标签，swap 后失配 → B 侧搜索的模拟里特性不触发（如 生物碱 post_skill 中毒+2）。
   B 搜索前后对称翻转注册表 owner 队伍标签（搜索期间新注册的同样对称还原）。
3. **rust 直改修饰标签随视图翻转**（selfplay.rs）：`direct_mod_sprite_ids` 也是 (team, idx)
   标签（state 上），B 视图克隆里同步翻转；否则模拟内 begin_turn 重注入把
   共鸣的 虫鸣 power+20 加到错误队伍 → 编码不同 → 树 walk 分叉（k=34）。
4. **py 真 bug 修复（oracle）**（backend/engine/ai/core/mcts.py）：`_execute_turn_core`
   每次把传入 agent 写进 `battle._agent_a/_agent_b`，MCTS 仿真经由
   `execute_turn_headless` 把**搜索代理**（`_PlayerSwappedAgent`/`_OppFixedAgent`，
   其 player 绑定还是交换期的对方队对象）永久留在真实战斗上；回合末力竭换宠
   `_get_agent` 拿到代理 → 按对方队伍选替补 → 引擎用该下标查本队 → 误触
   『替补已死 → 立即判负』。修复：mcts_search 保存并在 finally 还原
   `_agent_a/_agent_b`（与 _mcts_sim/RNG 还原同范式）。pytest 1656 全绿。

### 速度门（48 sims / batch16 / CPU torch 14 线程）
- 队列拓扑（BatchedInferenceServer batch128/5ms + 队列，种子对齐后）：
  py 2.7 samples/s vs rust 2.9 samples/s（3 局 146/156 样本），端到端 ≈1.0x。
- 直连 torch（无队列）耗时分解：**双方评估调用次数完全相同**（967 次/7845 状态）；
  torch 推理占 73%（py）/80%（rust），引擎+编码：py 3.38s vs rust 2.36s = **1.43x**。
- 结论：当前瓶颈是 CPU 推理而非引擎；引擎收益要兑现需 GPU 推理/更大批
  （多 worker 攒批）/评估器进 Rust。gate_phase5 速度段已修为同种子对局（此前
  py 用 seed+100+g、rust 固定 spec.seed+1，比较不公平）。

### 排查工具（native/tools/，全部可复跑）
- `dbg_first_div.py` 全 digest 扫首个分歧；`dbg_b22.py` A/B 流对齐比较记录；
- `dbg_eval_trace.py` 评估指纹轨迹（FNV-1a64 over 10 数组）按搜索分段对比；
- `dbg_simpaths.py` 每次搜索的模拟动作路径对比 + 指定搜索的逐步 digest 倾存
  （rust 侧 env：ROCO_NP_TRACE=1 输出 np 事件/路径；ROCO_SIM_DIGEST=k 倾存第 k 次搜索）；
- `dbg_np_trace.py` py/rust np 随机事件流对齐；`dbg_ulp.py` 逐位/反推访问计数；
- `dbg_split5.py` 评估 vs 引擎耗时分解；`dbg_bench5.py` 直连 torch 吞吐。

### 遗留
- 阶段5b：ROCO_ENGINE 开关 + 4-worker 拓扑吞吐；阶段5c：FastAPI/API 路径 + 事件串一致。
- rust 端 np_sum_f64 已无调用方（保留备用）；ROCO_NP_TRACE/ROCO_SIM_DIGEST
  为 env 门控调试插桩，生产路径零开销（OnceLock 判断）。
"""

with io.open(P, "a", encoding="utf-8", newline="\n") as f:
    f.write(TEXT)
print("appended", len(TEXT), "chars")
