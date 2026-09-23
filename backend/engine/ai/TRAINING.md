# 格斗小九 PVP AI 训练全流程文档

本文档说明 `backend/engine/ai/` 下的 AlphaZero 风格强化学习训练管线：
状态如何编码、MCTS 如何生成策略目标、网络如何训练和晋升、如何评估 checkpoint，
以及如何判断训练结果是否可信。

> 适用代码版本：`core/encoder.py` / `core/model.py` / `core/mcts.py` / `train.py` /
> `evaluate_checkpoints.py` / `service/advisor.py`。当前主线模型是
> `ModularBattleNet`，它是 `EntityBottleneckNet` 的兼容别名。

---

## 0. 一句话总览

> 让网络自己对自己打，用 MCTS 把网络先验加工成更强的访问分布，再用
> `(局面, MCTS 分布, 合法动作 mask, 终局价值)` 训练双头网络。候选模型只有在门控对打中超过
> `--gate` 才会晋升为新的 `best_model`。

```
best_model
   │
   ├─ collect_rl_samples / collect_rl_samples_parallel
   │     └─ 生成 states, policy, mask, outcome
   │
   ├─ RecentIterationsReplayBuffer 保存最近 N 轮完整样本
   │
candidate model ── train_rl ── evaluate / evaluate_parallel
   │                         ├─ 胜率 >= gate：晋升并保存 *_best.pt
   └─────────────────────────└─ 胜率 <  gate：回滚到 best_model
```

涉及文件：

| 文件 | 职责 |
|---|---|
| `core/encoder.py` | 将 `Battle` 编码为实体矩阵 + AST token 字典 |
| `core/model.py` | `EntityBottleneckNet` / `ModularBattleNet`，输出价值和 17 维策略 |
| `core/mcts.py` | 动作空间、合法动作 mask、MCTS 搜索、搜索内网络对手 |
| `core/evaluator.py` | 单进程 Torch 推理、队列推理、CUDA 批量推理服务 |
| `core/replay_buffer.py` | 字典型经验回放池，按实体 key 预分配数组 |
| `core/outcome.py` | 终局裁决、打满回合评分、门控得分换算 |
| `train.py` | 自我博弈采样、训练、门控评估、CLI 主入口 |
| `evaluate_checkpoints.py` | 固定种子 checkpoint 评估，可对规则 AI 或参考模型 |
| `benchmark_mcts.py` | 编码、推理、MCTS 热路径基准测试 |
| `service/advisor.py` | 部署侧单局建议和 PIMC 多决定化建议 |
| `tests/` | 自我博弈、编码形状、MCTS、并行、评估和 advisor 烟测 |

---

## 1. 数据如何生成

数据不是人工标注，也不是来自真实录像，而是自我博弈实时产生的。

### 1.1 随机对阵

`train.py::_random_teams` 每局随机抽取双方队伍：

- 精灵池来自 `backend/engine/ai/data/sprite_random_pool.py`。
- 每局抽取一次 1 到 3 的队伍规模，双方使用相同人数；每只精灵最多 4 个技能。
- 性格随机，六维个体值随机选择 3 项设为 10，其余为 0。
- 默认两队不复用同一批精灵；`--mirror-frac` 可让前若干比例迭代使用镜像阵容。

### 1.2 单局采样

`collect_rl_samples` 中双方都由 `MCTSAgent` 控制，并共享当前 `best_model`：

1. 每次决策先用 `encode_battle_state` 以本方视角编码局面。
2. 运行 `mcts_search`，得到 17 维访问分布 `π`。
3. 用 `get_valid_actions` 生成 17 维合法动作 mask。
4. 将 `(state_dict, π, mask)` 记入当前 agent 的 `history`。
5. 按温度 `T` 从 `π` 采样实际动作。

一局结束后，A 侧样本使用 `outcome_a`，B 侧样本使用 `-outcome_a`。这样一局同时产出双方视角样本，
数据量翻倍，也减少先后手偏置。

### 1.3 训练样本形状

当前样本不是旧版扁平 446 维向量，而是实体化 dict：

| 字段 | 形状 | 含义 |
|---|---:|---|
| `sprite_stats` | `(12, 7)` | 双方 12 个精灵槽的 HP、面板六维 |
| `sprite_elements` | `(12, 2)` | 精灵双属性 ID，`0` 为 PAD |
| `sprite_states` | `(12, 105)` | 能量、异常、buff、标记和场下技能摘要 |
| `skill_stats` | `(10, 2)` | 当前己方技能槽的威力和能耗 |
| `skill_elements` | `(10, 2)` | 技能属性 ID |
| `skill_states` | `(10, 9)` | 封印、冷却、类型 one-hot、连击、传动等 |
| `global_stats` | `(15,)` | 回合、印记、魔力、道具等全局数值 |
| `global_elements` | `(1,)` | 天气 ID |
| `ast_tokens` | `(384,)` | 技能/特性效果 AST token ID 序列 |
| `ast_values` | `(384,)` | AST token 对应数值 |
| `P` | `(N, 17)` | MCTS 策略目标 |
| `M` | `(N, 17)` | 合法动作 mask |
| `v` | `(N,)` | 本方视角终局价值，范围 `[-1, 1]` |

### 1.4 动作空间

`core/mcts.py::NUM_ACTIONS = 17`：

```
0-9   → 技能槽 0-9
10-14 → 换到板凳槽 0-4
15    → 聚能
16    → 使用道具
```

换宠使用固定板凳槽位映射：力竭精灵仍占槽但 mask 为 0，避免“输入槽位 N”和“动作 10+N”
在不同状态下指向不同精灵。

---

## 2. 模型结构

`EntityBottleneckNet` 采用实体瓶颈 + AST Transformer + 残差主干：

```
sprite entities ─┐
skill entities  ─┼─ bottleneck ─ cross attention ─ flatten ┐
global features ─┘                                          │
AST tokens/values ─ token emb + value proj + Transformer ───┤
                                                            ▼
                                                     residual trunk
                                                            │
                                  ┌─────────────────────────┴─────────────────────────┐
                                  ▼                                                   ▼
                             value_head                                           policy heads
                             tanh [-1,1]                 skill(10) + switch(5) + gather(1) + item(1)
```

关键点：

- 原始数值交给模型内 `Log1pNorm` 归一化。
- 属性和天气用 embedding，双属性通过 sum pooling 合成。
- 己方 6 个精灵实体和对方 6 个精灵实体可做 mutual cross attention。
- AST token 表示技能/特性效果结构，能让模型看到 JSON 效果的结构信息。
- 策略头拆成四个子头，最后拼成 17 维 logits。
- `forward_with_mask` 会把非法动作 logits 置为极小值，再 softmax 并乘 mask。

---

## 3. MCTS 如何使用网络

每步决策运行 `num_simulations` 次模拟：

1. **Selection**：按 PUCT 选择子节点。
   ```
   score(a) = Q(a) + c_puct * P(a) * sqrt(N(parent)) / (1 + N(a))
   ```
2. **Step**：我方执行当前树边动作，对手由 `NetworkPolicyAgent` 或外部 agent 选择动作。
3. **Expansion/Evaluation**：叶节点用 `TorchEvaluator` 或队列 evaluator 调模型估值。
4. **Backprop**：价值始终按当前搜索方视角回传。

根节点自我博弈时可加 Dirichlet 噪声（默认 `--root-noise 0.25`），评估和实战建议使用
`root_noise=0.0`。`--leaf-batch-size` 控制叶节点批量评估大小，默认走批处理路径以降低模型
forward 调用开销。

`NetworkPolicyAgent` 是槽位驱动的轻量对手：它始终为传入 battle 的 `player_b` 决策，
因此 A 侧搜索和 B 侧交换视角后的搜索都可复用同一逻辑。训练和部署搜索默认让该对手
选择策略头概率最高的合法动作（`opp_greedy=True`），确保同一树节点对应稳定的后继状态；
根节点的实际自我博弈动作仍可按温度采样，保留数据探索性。

---

## 4. 训练循环

每轮 `iteration`：

1. 用 `best_model` 自我博弈，得到 `X, P, M, v, reason_counts`。
2. 将样本写入 `RecentIterationsReplayBuffer`，只保留最近 `--buffer` 轮完整样本。
3. 用回放池随机采样 batch 训练候选模型。
4. 候选和当前最优模型门控对打。
5. 胜率达到 `--gate` 则保存并晋升，否则候选回滚。
6. 每轮保存 `model_rl_iterK.pt`，最终保存 `model_rl.pt`。

训练损失：

```
value_loss  = MSE(value_pred, outcome)
policy_loss = -sum(policy_target * log(masked_policy_pred))
loss        = value_loss + policy_loss_weight * policy_loss
```

优化器是 Adam，默认 `lr=1e-3`、`weight_decay=1e-4`，学习率用
`CosineAnnealingLR` 跨全部 iteration 衰减。`policy_loss_weight` 由
`--policy-loss-weight` 控制，默认 `1.0`。候选未通过门控时，模型参数和本轮产生的 Adam
动量会一起回滚；调度器已经推进的当前学习率会保留，避免被旧优化器状态覆盖。

---

## 5. 终局价值和打满回合裁决

`core/outcome.py::battle_outcome_a` 返回 `(outcome_a, end_reason)`：

- A 正常胜：`+1`，`decisive_a`
- B 正常胜：`-1`，`decisive_b`
- 未正常分胜负：按存活数、队伍 HP 比例、魔力、在场能量计算局面分
- 分差小于 `--draw-margin`：记平局 `0`
- 分差达到阈值：按局面领先方给 `+1/-1`

可选参数：

- `--gamma < 1`：胜利价值随回合数衰减，鼓励速胜。
- `--tanh-k > 0`：非决胜对局用 `tanh(k * margin)` 产生连续价值，替代硬阈值。

训练日志会输出 `reason_counts`，用于判断是正常击杀、打满回合裁决、僵局还是 timeout。
timeout 对局不会进入训练样本，因为截断局面的价值标签不可靠。

---

## 6. 评估方式

### 6.1 训练内门控

`evaluate` / `evaluate_parallel` 比较候选模型和 best 模型：

- 每两局组成一个配对：复用相同队伍和道具，候选分别执 A、执 B。
- 评估时温度为 0，根节点无噪声。
- 得分为 `(胜 + 0.5 * 平) / 局数`。
- 提前晋升或淘汰只在完整配对结束后判断，避免单边先后手结果造成门控偏差。
- `--eval-workers 0` 表示自动跟随 `--workers`。
- 并行评估使用 `BatchedModelInferenceServer`，请求中区分 `candidate` 和 `best`。

**门控的可复现口径**（2026-09 修正，见 `docs/博弈-概率预判口径.md` §4f）：

- **阵容套件固定**：每次门控用 `_eval_roster_rng()`（种子 `_EVAL_ROSTER_SEED`，可用
  `ROCO_EVAL_ROSTER_SEED` 覆盖）生成**同一批阵容**。此前每轮门控重新随机抽阵容，门控分数
  在阵容抽样方差里漂移（±数个百分点），曲线不可比。
- **单局随机数只由局号决定**：`_seed_eval_game(game_index)`（基准 `_EVAL_GAME_SEED`，
  可用 `ROCO_EVAL_GAME_SEED` 覆盖）。此前 worker 只在启动时 seed 一次，之后按
  work-stealing 顺序连续消耗随机数 → **同一对模型两次门控分数不同**。
- **`PYTHONHASHSEED` 在 `main()` 开头自钉**（`backend/engine/ai/determinism.py`）：否则字符串
  哈希随机化会漏进配装生成，子进程（spawn）各有不同的哈希盐 → 同 seed 也不同局。
- 结果：给定一对模型，门控分数**逐位可复现**，与 `--eval-workers`、worker 领取顺序无关；
  与串行 `evaluate` 一致。换一批阵容复测用 `ROCO_EVAL_ROSTER_SEED`（抗过拟合检查）。

### 6.2 固定种子 checkpoint 评估

`evaluate_checkpoints.py` 用固定 seed 序列横向比较 checkpoint：

```bash
python -m backend.engine.ai.evaluate_checkpoints \
  --checkpoint-dir checkpoints/formal_v1 \
  --opponent rule \
  --games 20 \
  --sims 32 \
  --device cuda \
  --output backend/engine/ai/log/formal_v1/benchmark_rule.json
```

也可以比较某个 checkpoint 对参考模型：

```bash
python -m backend.engine.ai.evaluate_checkpoints \
  --checkpoints checkpoints/formal_v1/model_rl_iter13.pt checkpoints/formal_v1/model_rl_best.pt \
  --reference checkpoints/formal_v1/model_rl_iter1.pt \
  --opponent model
```

输出包含 score、95% 置信区间、W/D/L、平均回合数、终局原因统计和每局摘要。

### 6.3 热路径基准

```bash
python -m backend.engine.ai.benchmark_mcts \
  --device cuda \
  --simulations 16 \
  --mcts-repeats 3 \
  --leaf-batch-size 16
```

该脚本分别统计编码、模型推理和 MCTS simulation 吞吐，适合验证优化是否真的改善了瓶颈。

---

## 7. 训练命令

### 7.1 常用参数

| 参数 | 默认 | 说明 |
|---|---:|---|
| `--iterations` | `5` | RL 迭代轮数 |
| `--battles` | `200` | 每轮自我博弈局数 |
| `--sims` | `200` | 自我博弈每步 MCTS 模拟次数 |
| `--epochs` | `20` | 每轮训练 epoch 数 |
| `--batch-size` | `256` | 训练 batch size |
| `--lr` | `1e-3` | 初始学习率 |
| `--hidden` | `256,128` | 当前只用首项作为 `trunk_dim` |
| `--dropout` | `0.0` | dropout |
| `--weight-decay` | `1e-4` | Adam L2 正则 |
| `--policy-loss-weight` | `1.0` | 策略损失在总损失中的权重 |
| `--buffer` | `5` | 回放池保留的最近完整迭代数 |
| `--resume` | `""` | 从已有 checkpoint 继续训练 |
| `--base-model` | `""` | 无 `--resume` 时加载基座模型 |
| `--output` | `checkpoints/model_rl.pt` | 兼容参数；当前保存路径实际为 `checkpoints/` 或 `checkpoints/<run-name>/` |
| `--device` | 自动 | `cuda` / `cpu` |
| `--workers` | `1` | 自我博弈 worker 数 |
| `--batched-inference` | 关 | 多 worker 时由主进程合并 CUDA 推理 |
| `--inference-batch-size` | `128` | 批量推理最大 batch |
| `--inference-timeout-ms` | `5` | 攒 batch 等待毫秒 |
| `--leaf-batch-size` | `16` | MCTS 叶节点批量评估大小 |
| `--worker-stall-timeout` | `600` | 并行 worker 长时间无完成对局时终止剩余 worker |
| `--max-turns` | `60` | 自我博弈单局回合上限 |
| `--eval-max-turns` | `150` | 门控评估单局回合上限 |
| `--draw-margin` | `0.15` | 打满回合局面分差小于该值记平 |
| `--gamma` | `1.0` | 回合衰减因子 |
| `--tanh-k` | `0.0` | 非决胜对局软裁决系数 |
| `--mirror-frac` | `0.0` | 前 N 比例迭代使用镜像阵容 |
| `--eval-games` | `20` | 门控对局数，`0` 关闭门控 |
| `--eval-sims` | `100` | 门控每步 MCTS 模拟次数 |
| `--eval-workers` | `0` | `0` 表示跟随 `--workers` |
| `--gate` | `0.55` | 晋升阈值 |
| `--log-dir` | `backend/engine/ai/log` | 日志目录 |
| `--run-name` | `""` | 实验名；同时影响日志和 checkpoint 子目录 |
| `--no-log` | 关 | 关闭自动日志 |

### 7.2 冒烟自测

```bash
python -m backend.engine.ai.train \
  --iterations 1 \
  --battles 4 \
  --sims 16 \
  --epochs 1 \
  --eval-games 2 \
  --eval-sims 8
```

### 7.3 并行 + CUDA 批量推理

```powershell
python -m backend.engine.ai.train --device cuda `
  --iterations 1 --battles 8 --sims 16 `
  --epochs 1 --eval-games 0 `
  --workers 2 --batched-inference `
  --inference-batch-size 64 --progress-every 1
```

Windows 使用 multiprocessing `spawn`，建议始终通过 `python -m backend.engine.ai.train`
启动，不要直接执行脚本文件。

### 7.4 正式训练示例

```powershell
python -m backend.engine.ai.train --device cuda `
  --run-name formal_v1 `
  --iterations 50 --battles 40 --sims 64 `
  --epochs 80 --batch-size 512 --hidden 512,256 `
  --buffer 8 --eval-games 16 --eval-sims 64 --gate 0.55 `
  --workers 8 --eval-workers 8 --batched-inference `
  --inference-batch-size 128 --inference-timeout-ms 5 `
  --leaf-batch-size 16 `
  --progress-every 1
```

继续训练：

```bash
python -m backend.engine.ai.train \
  --resume checkpoints/formal_v1/model_rl_best.pt \
  --run-name formal_v1_cont \
  --iterations 20
```

---

## 8. 部署和实战建议

完全信息局面：

```python
from backend.engine.ai import ModularBattleNet
from backend.engine.ai.service.advisor import advise_single

model = ModularBattleNet.load("checkpoints/model_rl_best.pt", device="cuda")
advice = advise_single(battle, model, factory, num_simulations=400, device="cuda")
print(advice.summary())
```

对手板凳未知时，用 PIMC 采样多套决定化：

```python
from backend.engine.ai.service.advisor import advise, make_determinizations

dets = make_determinizations(battle, factory, bench_pool=opponent_pool, k=20)
advice = advise(dets, model, factory, num_simulations=200, device="cuda")
print(advice.best_action, advice.summary())
```

前端 AI 对手可通过 `backend.engine.ai.service.agent.NeuralMCTSAgent` 接入。
默认 checkpoint 路径由 service agent 内部加载逻辑控制，也可用 `set_checkpoint` 切换。

---

## 9. 如何判断训练是否有效

### 9.1 正确性前提

```bash
pytest backend/engine/ai/tests -x --tb=short
```

重点关注：

- 编码输出形状是否和模型一致。
- `ModularBattleNet.NUM_ACTIONS` 是否等于 MCTS 的 `NUM_ACTIONS`。
- MCTS save/restore 是否确定。
- batch evaluator 和单条 evaluator 输出是否一致。
- 并行 self-play / 并行 evaluate 是否能跑通。
- PIMC 和 `NeuralMCTSAgent` 是否能烟测通过。

### 9.2 训练中指标

| 指标 | 含义 |
|---|---|
| `train_v_loss` / `val_v_loss` | 价值头回归误差 |
| `train_p_loss` / `val_p_loss` | 策略头模仿 MCTS 分布的交叉熵 |
| `val_acc` | 价值符号预测准确率 |
| `win_rate` | 候选 vs best 的门控得分 |
| `draw_ratio` | 平局样本比例，过高会稀释价值信号 |
| `reason_counts` | 正常终局、打满裁决、僵局、timeout 的来源分布 |
| `samples_per_sec` | 自我博弈样本吞吐 |
| `phase_percent` | selfplay/train/eval/checkpoint/other 用时占比 |

### 9.3 绝对基线

门控只说明“候选是否强于上一版 best”，不说明绝对水平。训练一段时间后应额外跑：

```bash
python -m backend.engine.ai.evaluate_checkpoints \
  --checkpoints checkpoints/formal_v1/model_rl_best.pt \
  --opponent rule \
  --games 50 \
  --sims 64 \
  --device cuda
```

如果对 `RuleAgent` 长期没有优势，优先检查样本质量、平局比例、timeout 比例、动作 mask 和
checkpoint 是否来自同一动作空间。

### 9.4 已知局限

- 搜索内默认对手是策略头，不是完整 MCTS，对手建模仍是近似。
- 同时出招和隐藏信息使严格 minimax 不适用，PIMC 只是实战近似。
- `max_turns` 过低会增加局面裁决样本，过高会拖慢 self-play。
- 队伍随机提升泛化，但固定阵容上的进步需要单独评估。
- CPU 下 MCTS 仍主要受 battle copy/restore 和模型 forward 开销影响。

### 9.5 并行吞吐现状（2026-09-21 实测，i5-14600KF 6P+8E / 20 线程 + RTX 5060 Ti 16G）

**结论：整条管线已经跑在机器的饱和点上，加 worker 不会更快；再想提速只能减 sims/局数或换机器。**
（想复现这些数字，直接按「测量方法」一列的命令跑。）

| 阶段 | 实测吞吐 | 瓶颈 | 测量方法 |
|---|---|---|---|
| 自博弈（sims=100，队列批量推理） | ~34 decisions/s | 6 个 P 核的引擎+MCTS（GPU 推理只占小头：改成每 worker 直连 CUDA 也只 +8%） | `native/tools/bench_selfplay_pool.py` |
| 门控评估（150 局 ≈ 自博弈 150 局） | 同上 | 同上；占一轮迭代 43% 墙钟 | `phase_percent.eval` |
| 训练（BC 或自博弈，bs=256） | 1843 样本/s（99% GPU，取数只占 1%） | 模型 attention 前反向 | 见 §7.4 日志的 `train_sec` |
| BC 生成（规划层专家 `plan_depth=1`） | **3.9 局/s**（6~12 worker 一样） | 6 个 P 核；19 worker 因落到 E 核反而掉到 3.1 局/s | `gen_bc_data --workers N` 的 `局/s` |
| BC 生成（纯规则 `plan_depth=0`） | 59 局/s（19 worker 最优） | — | 同上 |
| worker 冷启动（含导入 torch/SimFactory） | 3.0s / 16 worker | — | 每轮两次池开销合计 ≈ 0.5%，不值得做池复用 |

已排除的优化方向（都量过，别重复做）：BC 结果序列化/父进程堆叠占 0.3%；`train_rl` 取数占
epoch 1%；每轮重建 worker 池 0.5%；把批量推理换成每 worker 直连 CUDA 只 +8%。

### 9.6 两个影响历史数据解读的引擎修复（2026-09-21）

1. **快照回滚泄漏**（`Battle.restore_mutable_state`）：印记/队伍计数器/VM 计数器/burst/
   skill_history/devotion 等容器被**按对象**装回 live，仿真就地改写的正是快照本身 →
   同一个快照每 restore 一次就多累加一次。实测 40/40 局都在漏（星陨印记 140 → 43140 →
   … → 2.5e9，最终在 `build_ctx_cy` 里抛 OverflowError 崩掉多进程 BC 生成）。
   **影响**：所有用 MCTS/规划层搜过的对局（自博弈数据、规划层专家的决策）都跑在被污染的
   计数器上——「真实回合」的计数器里混着仿真累加值，靠计数器判定的特性会错触发。
   纯规则对局（不搜索，如 exp23 的 BC 数据）不受影响。守门测试见
   `backend/engine/test_mcts_state.py::test_repeated_restore_is_idempotent_for_mutable_containers`。
2. **Cython 构建产物过期**：`snapshot_cy.cp312-win_amd64.pyd` 是 2026-06-13 编译的，而
   `.pyx` 源码 2026-09-19 改过 —— 三个月里跑的是旧语义（对拍测试 `test_snapshot_cython`
   当时是红的：`counters_self` 恒为空；所幸 `fill_extended_registers` 会补齐，运行期被掩盖）。
   现在 `backend/engine/battle.py` 会比对 `.pyd`/`.pyx` 的 mtime，过期就走 Python 参考实现
   （`ROCO_ALLOW_STALE_CYTHON=1` 可强制放行）。
   **改了 `.pyx` 必须重新编译**，否则走不到加速路径。

### 9.7 容量对照实验（2026-09-21）：模型不是瓶颈

用同一份 BC 数据（`checkpoints/bc_data_plan.npz`）、同 seed / holdout / lr / batch，
只改 trunk 宽度，各训 6 epoch（`bc_pretrain --trunk-dim {128,256,512}`）：

| trunk | 参数量 | 留出队 best top-1 | best epoch | 第 1 轮 val p_loss | 对比 1× 的头对头（200 局 sims=32） |
|---|---|---|---|---|---|
| 128（0.5×） | 865,835 | 0.445 | 6 | 1.5732 | 0.490 [0.422, 0.559] |
| 256（1×） | 1,512,747 | **0.450** | 3 | 1.5619 | — |
| 512（2×） | 3,752,747 | 0.430 | **1** | 1.5675 | 0.527 [0.458, 0.596] |

**结论：参数翻 4.3 倍既没提升留出队 top-1（反而略降、且第 1 轮就过拟合），也没在实战头对头里
拉开差距（两组 CI 都跨 0.5）。** 三个宽度的首轮 val p_loss 几乎相同（1.562~1.573）——泛化的墙在
数据与目标上，不在网络里。所以别再靠加宽/加深求提升；要动就动数据多样性、目标（教师质量）、
正则（`--dropout` 目前 0.0）与对手池。

注意 200 局配对评估的分辨率约 ±7 个点（要分辨 3 点差异需 ~1500 局），所以"2× 略高 2.7 点"读作
"无法分辨或效应 <3 点"，而它的持有成本是 4.3 倍参数 + 更早过拟合。

```powershell
# 复现（每档 6 epoch；2× 那档约 7 分钟、0.5× 约 6 分钟，纯 GPU）
foreach ($w in 128,256,512) {
  env\python.exe -X utf8 -m backend.engine.ai.bc_pretrain --data checkpoints\bc_data_plan.npz `
      --out "checkpoints\cap_sweep\w$w.pt" --holdout-teams 4 --epochs 6 --trunk-dim $w
}
```

### 9.8 第三波引擎/专家修复（2026-09-22）：现有 BC 数据与 checkpoint 全部过期

与 §9.6 同性质。本波修复跨「伤害语义 / 合法性语义 / 专家估伤」三层，**凡是在这之前生成的
数据集、checkpoint 与 top-1 曲线，量的是另一个世界**：

| 修复 | 影响 |
|---|---|
| 本系加成 1.5 → **1.25**（实战与估伤统一） | 全体本系攻击的实战伤害下调约 17%，直接影响 BC 的胜负与 value 标签 |
| **连击 = N 次独立命中**：每段各自取整 / 各自最低 1 点 / 各自触发受击结算 | 连击技能总伤小幅变化（旋转突击 98 → 3×33=99），多段技能的斩杀线随之变化 |
| **蓄力技能此前被永久锁死**（守卫漏 `gather` 限定）：放一次蓄力技能后该精灵整局 0 伤、无法行动 | 龙吟 / 龙之利爪 / 升龙咆哮等出现在 meta 队与随机池里 → 相关对局此前等于少一只精灵 |
| **15 条专家估伤偏差清零**（全库 365 个攻击技能逐技能对拍 **0 不一致**）：蓄力 / `choices` 分支 / 支付次序 / `power_add` 浮点 / `redirect` / 不可支付 / `power:0` 命中描述 | 专家选招与 BC 的 policy 标签质量直接提升；此前蓄力技能会被「以为自己能一击必杀」的斩杀规则选中并打出 0 伤 |
| **非法动作拒绝 + 重选**（不推进回合）+ 合法性判据收敛（引擎门控 / 动作掩码 / agent 候选同源） | AI 不再静默丢掉一次行动；`ActionRecord` 新增 `status`/`code`/`turn_consumed`/`rejected` 字段（随 `round_record_to_dict` 序列化） |

受影响产物（时间戳即生成时间）：

| 产物 | 时间 | 状态 |
|---|---|---|
| `checkpoints/bc_data.npz`（2500 局，纯规则，seed 2026/meta 0.6） | 09-20 04:51 | **过期**（保留作对照基线） |
| `checkpoints/bc_data_plan.npz`（2500 局，规划层） | 09-21 04:49 | **过期** |
| §9.7 容量对照的绝对 top-1（0.430~0.450） | 09-21 | 绝对值过期（结论本身成立：加宽/加深无用） |
| 历史 top-1 曲线（2500/20000/30000 局 → 0.450/0.550/0.578） | 09-20~21 | 绝对值过期（「数据规模有边际递减收益」的结论可参考） |

**重新测量口径**：同 CLI 配置（`--games/--seed/--meta-frac/--max-turns/--draw-margin` 与旧数据一致）
+ 同 `bc_pretrain` 协议（`--holdout-teams 4 --epochs 6 --trunk-dim 256`），新旧数据集各跑一次再比。
`bc_pretrain` 本身未改，所以「同协议下的留出队 top-1」可以直接对新旧数据做 A/B。

### 9.9 数据事故：float16 `inf` 让训练变 NaN（2026-09-22，已加护栏）

**症状**：按 §9.8 重生成的 BC 数据训练 **全程 NaN** —— `v_loss=nan p_loss=nan`、
`val_acc=0.087`、留出队 top-1 **0.193**（≈ 随机）；同一协议下旧数据是 0.721。

**根因（训练侧，与本波引擎修复无关，旧数据也有同源痕迹）**：数据集里 `sprite_stats`
的 `atk`/`def` 列出现 **`inf`**（float16 上限 65504）——引擎产出了极端数值，写盘时
`arr.astype(np.float16)` 静默溢出（只报一条 RuntimeWarning）。定位过程：

| 步骤 | 观测 |
|---|---|
| 扫描 npz | 2500 局数据集里 `sprite_stats` 有 56 个 `inf`（同一局）；旧 `bc_data.npz` 0 个 `inf`，但同列最大值已到 **atk 2594 / def 3290 / spa 3404**（面板正常值 ~100–200） |
| 插桩编码器 | 命中 114 条，两个物种：**嗜波螺** 与 徘徊爪爪；最大一例 turn 60 `atk=def=3,048,659,808` 步 |
| 看增长 | **每回合 ×2**：708 → 714 → 1440 → 2892 → 5796 → … → 3.0e9（指数） |
| 定位机制 | 步数翻倍来自 `double{what:"positive"}`（**生日蛋糕**「驱散自己的减益，自己的增益翻倍」/ 泥浆铠甲 应对分支）：`sprite.double_positive()` 把**所有正增益的步数 ×2**。AI（尤其规划层）会反复使用它 → 步数随使用次数指数增长 |

**结论**：这是「按文本字面实现 + AI 反复使用」共同造成的爆炸；`stat_stage` 的步数在引擎里
**没有任何上限**。真实游戏是否允许属性被翻倍到 ×1e8 存疑，需要定一条规则（见下）。

**已加护栏（`native/tools/gen_bc_data.py`）**：写盘前先 `nan_to_num` + `clip(-65504, 65504)`
再降 float16，并把每个特征的夹取计数写进 sidecar 的 **`nonfinite_clipped`**，同时打一条
`!! 非有限值已夹到 float16 范围` 警告。

> **约定**：新生成的数据集 sidecar 里 `nonfinite_clipped` 必须是空 `{}`；
> 非空说明引擎又产出了极端数值，先查清来源再用该数据集训练（此前会静默把训练变成 NaN）。

**待定（需要用户拍板）**：`double{what:"positive"}`（以及 `stat_stage` 步数）要不要按游戏规则
加上限？可选口径：① 增益步数封顶（如 +60 步 = ×700%，需游戏佐证）；② 增益翻倍类技能
每场限次；③ 维持现状（字面实现）但接受长局出现极端面板。

**2026-09-23 补充（10000 局口径）**：新世界数据里仍有 **4/10000 局**触发夹取（共 **212 个样本**，
越界位正是双方场上精灵的 `atk`/`def`）——`double{what:"positive"}` 的爆炸没消失，只是频率从
"每 2500 局约 1 局"降到 0.04%。夹取后训练正常（本节下方 A/B 的新臂就是这份数据），
但 §9.9 的约定（`nonfinite_clipped` 必须为空）目前**不满足**，待定问题继续挂着。

---

### 9.10 干净世界重测 BC（2026-09-23）：同种子配对 A/B

**问题**：2026-09-23 的解僵局修复（空过/借用复写变身/换人上限/打不动先增益）让世界变干净了
（`docs/引擎机制对账-游戏描述图鉴.md` §18–§25），训练侧的 BC 数据要不要重生成、`top-1` 会怎么变？

**协议（配对）**：两臂同 `--seed 2026 --games 10000 --meta-frac 0.6 --mirror-frac 0.15
--optimal-frac 0.95 --max-turns 60`，只是**跑的是不同代码树**：

| 臂 | 代码树 | 说明 |
|---|---|---|
| 旧 | `0c3ba52`（解僵局修复的父提交） | 旧教师 = 未修复的 RuleAgentV2 |
| 新 | 当前 HEAD（修复 + 专家层） | 新教师 = 出厂默认（**没有**挂任何专精专家） |

产队只依赖 `--seed`（父进程唯一 RNG 流），所以两臂**打的完全是同一批对手**——
用 `native/tools/remote/bc_arm_stats.py` 逐局核对"队伍编号集合"：`共同局号 10000/10000，
队伍编号不一致 0 局`。复现：

```bash
# 远端：两棵树各跑一遍（同 seed），再各训一遍（holdout 4 / 6 epoch / seed 7）
bash /mnt/workspace/run_bc_arms.sh <旧臂.npz> <新臂.npz>
```

**结果一：世界与标签指标**

| 指标 | 旧臂 | 新臂 | 差 |
|---|---|---|---|
| 决定性 | 0.876 | **0.890** | +1.4 pt |
| 打满回合 | 0.113 | **0.100** | −1.3 pt |
| 平均回合 | 32.7 | 32.3 | −0.4 |
| 样本/局 | 69.58 | 68.79 | −0.79 |
| **聚能占比（标签）** | 10.56% | **9.45%** | −1.11 pt |
| 技能占比（标签） | 54.84% | 56.63% | +1.80 pt |
| 换宠占比（标签） | 31.44% | 30.71% | −0.73 pt |
| `max_turns_draw` 局数 | 449 | 379 | −70 |
| 非有限值（`inf`） | **110** | 0 | 护栏生效 |

聚能占比是"空过"修复最直接的标签侧证据：**能量满时聚能是严格劣动作**，修复后从标签里少了 1.1 个点。

**结果二：数据卫生（这条比 top-1 重要）**

旧臂数据（无护栏）里 `sprite_stats` 有 **110 个 `inf`**；按同样协议训练，**第 1 个 epoch 就 NaN**
（`v_loss=nan p_loss=nan val_acc=0.047 top-1=0.189`，≈随机）。把 `inf` 按护栏口径夹到 ±65504
（`native/tools/remote/bc_sanitize.py`）后重训才恢复正常。**结论：护栏出现之前生成的 BC 数据一律不可用**
——没有护栏时训练是"静默变随机"而不是报错。

**结果三：BC `top-1`（同留出队 36-39、同 seed 7、6 epoch）**

| 臂 | best epoch | best val top-1 | 末轮 top-1 | top-3 | val_acc（胜负） | 策略熵 H |
|---|---|---|---|---|---|---|
| 旧（数据夹取后） | 4 | **0.461** | 0.458 | 0.795 | 0.665 | 1.16 |
| 新 | 4 | **0.458** | 0.458 | 0.796 | **0.691** | 1.13 |

**读法**：世界干净了，但 **BC `top-1` 没变**（Δ = −0.003）。同一份变化在别处是有效应的：
胜负预测 `val_acc` +2.6 pt、决定性 +1.4 pt、聚能标签 −1.1 pt。所以 `top-1` 量的是"这套标签有多好模仿"，
**不是**"这个世界有多干净/多能打"——世界变好不必然抬高它，历史曲线（§9.8 表格：
2500/20000/30000 局 → 0.450/0.550/0.578）也不能跨世界直接比。要继续用 `top-1` 当门禁，
必须**在同一世界口径内**比较。

**下一步（未做）**：① 教师换成 RuleAgentV3 / 专精专家（`--expert v3|team`，后者 2026-09-23 新增，
`team` = meta 队各自挂 `backend/sim/experts` 的专精专家、其余回落 v2）；② 用**干净世界**重跑一遍
规模曲线（20000/30000 局），确认"边际递减"的形状有没有变；③ §9.9 的 `double{positive}` 上限口径待拍板。

---

## 10. 名词速查

| 名词 | 含义 |
|---|---|
| self-play | 网络左右互搏生成训练数据 |
| MCTS | 蒙特卡洛树搜索，用网络先验和估值扩展搜索 |
| PUCT | MCTS 选择公式，平衡 Q 值与先验探索 |
| policy target `π` | MCTS 访问分布，策略头监督目标 |
| legal mask `M` | 合法动作 mask，保证训练和推理不选非法动作 |
| value target `z` | 本方视角终局价值 |
| gating | 候选模型达到阈值才晋升 |
| replay buffer | 保存近期样本的循环经验池 |
| PIMC | 对隐藏信息采样多套决定化后平均建议 |
