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
