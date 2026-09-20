
## 阶段4：MCTS 接入 Rust（进行中）

### 已完成

1. **numpy legacy RNG 镜像** `native/roco-core/src/np_random.rs`：
   - `np.random.seed(int)` = `mt19937_seed`（init_genrand；**不是** CPython random 的 init_by_array），state[0]==seed、pos==624；
   - `rk_double` / `standard_exponential` / `standard_gamma`（shape<1 与 >=1 两分支）/ `dirichlet` 逐位对齐；
   - 校准基准 `native/tools/probe_np_rng.py`（numpy 2.4.6）；单测 `cargo test -p roco-core np_random` 7/7 通过（含 17 维 dirichlet）。
2. **MCTS 主体** `native/roco-core/src/mcts.rs`：树/PUCT/选择/扩展/回退/终局价值/合法动作/固定动作步进/轨迹采集。
   - PUCT 与 `q+u` 按 numpy 2（NEP 50）的 **float32** 语义求值（校准：`native/tools/probe_np_arith.py`）；
   - 根噪声 `prior[a] = f32( f32((1-rn)*prior[a]) as f64 + rn*noise[i] )`；
   - 终局价值 `battle_outcome_a` + `team_battle_score`（outcome.py 镜像）；
   - 回滚：每轮仿真前克隆 (BattleState, VmEngine)，**不还原 RNG**（= py save_mutable_state 语义）；搜索结束整体复原对局 RNG（= py `random.setstate`）。
3. **引擎侧新增**：
   - `turn::execute_turn_fixed` / `execute_turn_a_fixed_b_agent`（py `execute_turn_headless` + `fixed_action_*`）；
   - `ReplacementPolicy` trait（RuleAgent / RuleReplPair / FirstAliveRepl / MCTS 的网络策略头 `EvalRepl` = py `_choose_policy_replacement`）；
   - `BattleState.mcts_sim` + `Replayer.headless`（py `battle._mcts_sim`）。
4. **对拍工装**：`native/roco-py::py_mcts_stub`（确定性桩：value=0、policy=归一化 mask）+ `native/tools/mcts_gate.py`
   （比对项：概率位模式、根节点访问次数、每轮仿真动作轨迹、逐步状态摘要、numpy/对局 RNG 状态）。

### 本轮由 MCTS 对拍暴露并修掉的 4 个引擎级根因

（整局 200/200 门未覆盖这些路径——RuleAgent 不会用到的动作/时机组合）

| # | 现象 | 根因 | 修复 |
|---|------|------|------|
| 1 | 应对方 when/then/else 效果未注入 | py 过滤的是**编译后 IR**：带 `when` 字段的只有 `CountOp`，`WhenBlock` 的字段名是 `cond`；rust 此前按源 JSON 的 `"when"` 键过滤 → 整块被排除 | `turn.rs` 反制注入过滤改为仅排除 `op=="count" && 有 when` |
| 2 | 被应对方多 1 点能耗（spec_0037） | 同上：被注入的 WhenBlock 在注入 ctx 里按 **else 分支**生效（注：注入 ctx 的 counter_succeeded=False） | 同上 |
| 3 | 仿真中创建了 UI 显示效果（spec_0001 `combo=3` 等） | py `is_headless` 跳过纯 UI 显示效果（replayer.py:421 / :630）；rust 无条件创建 | `BattleState.mcts_sim` → `Replayer.headless`，两处显示效果创建前 return |
| 4 | 结算中力竭的精灵不再触发 turn_end 观察者（spec_0019 毒蘑菇偷能量） | py 在 turn_end 结算**之前**快照存活在场精灵列表（battle.py:1797-1801），结算（异常 tick/暴风雪/印记）中力竭者仍在列表里 | `phase_turn_end` 先快照 sprites 再结算；extra_turn / post_abnormal_tick / turn_end 三处遍历快照 |

### 当前对拍结果

- **阶段3 整局门仍 200/200**（本批修复后复跑通过）；pytest **1656 passed**。
- **MCTS 桩对拍**（sims=200，单仿真路径，specs 1..50）：**41/50 通过**。
  剩余失败样例（首个分歧点）：
  - `spec_0008` sim42：`P1S1`（双向光速 `extra_turn_end`）py 受伤 388 vs rust 269，且 rust B 侧 `status_skill` 计数多 1；
  - `spec_0016` sim114 / `spec_0019` sim26：回合末 hp/energy 差（26~63）——疑似 extra_turn 二次结算范围或 turn_end 快照后的行为差异；
  - `spec_0023 / 0030 / 0040 / 0041 / 0042 / 0044`：待分类。

### 阶段4 未完成项

1. `leaf_batch_size>1` 的叶节点批量评估路径（训练默认 16，生产路径必须实现——当前 rust 只实现单仿真路径）；
2. encoder 移植（10 数组 + vocab + AST tokenization）→ 由 Rust 产 numpy；
3. evaluator 回调协议（Rust → Python 批量推理队列）+ `mcts_search` 薄壳（签名不变）；
4. 阶段4 门：samples/s ≥5x + 同种子结果一致。

### 调试方法备忘（用于继续追踪剩余分歧）

- `mcts_gate.py` 已带 `digest_trace`（每步状态摘要）+ trace（每步动作），定位首个分歧的 sim/step；
- `dbg_mcts01.py <spec> <sims>`：打印首个分歧的全部差异字段与双方精灵状态；
- `dbg_mcts_rust.py <spec> <sims> <关键字>`：带 `ROCO_DEBUG_DMG` 跑 rust 桩，筛选 stderr 调试行；
- **注意**：所有脚本必须 `os.chdir(ROOT)`——`data/skills/*.json` 是 CWD 相对路径，CWD 不对会导致 py 侧技能 JSON 缺失而静默 no-op（曾误判为分歧）。
