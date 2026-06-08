# 格斗小九 PVP AI 训练全流程文档

本文档详细说明 `backend/engine/ai/` 下这套 **AlphaZero 风格强化学习** 训练管线：
数据如何生成、用什么方法训练、AI 如何自我强化、如何评估能力、关键指标、奖励定义、
训练指令，以及**如何判断这套训练代码是否真的有效**。

> 适用代码版本：`mcts.py` / `train.py` / `model.py` / `encode.py` / `advise.py`（双头网络 + 真自我博弈 + 评估门控）。

---

## 0. 一句话总览

> 让网络**自己跟自己打** → 用 **MCTS** 把"网络的直觉"加工成更强的落子分布 →
> 把（局面，MCTS 分布，最终胜负）当作监督数据训练网络 → **门控对打**确认变强了才晋升 →
> 如此循环，网络越来越强。部署时用 **PIMC** 处理对手隐藏信息。

```
┌──────────────────────────────────────────────────────────────┐
│  一轮 RL 迭代 (iteration)                                       │
│                                                                │
│  best_model ──► 自我博弈 collect_rl_samples ──► (X, P, v)       │
│       ▲              │ MCTS×sims/步                  │          │
│       │              ▼                              ▼          │
│       │        经验回放 buffer(最近N轮) ──► train_rl(候选 model) │
│       │                                             │          │
│       │                                             ▼          │
│       │        evaluate(候选 vs best, 门控)  ──► 胜率≥gate?      │
│       │              │是                         │否           │
│       └──────────────┘晋升 best=候选            回滚 候选=best   │
└──────────────────────────────────────────────────────────────┘
```

涉及文件：

| 文件 | 职责 |
|---|---|
| `encode.py` | 把 `Battle` 局面编码成 **446 维** 向量（网络输入） |
| `model.py` | 双头网络 `BattleNet`（value + policy）；单头 `BattleValueNet`（监督备用） |
| `mcts.py` | 蒙特卡洛树搜索 + `NetworkPolicyAgent`（搜索内对手）+ 动作空间 |
| `train.py` | 自我博弈采样、训练、评估门控、CLI 主入口（含多进程并行） |
| `evaluator.py` | 推理抽象：本地 Torch / 队列批量 CUDA 服务 |
| `advise.py` | 部署侧：PIMC 实战单回合建议 |
| `test_selfplay.py` | 回滚一致性、双视角采样、建议管线测试 |

---

## 1. 数据是怎么生成的

数据**不是人工标注的，也不是来自真实对局录像**，而是 AI **自我博弈**实时产生的。

### 1.1 随机对阵（`_random_teams`）
每局开始随机抽取双方队伍：
- 从 `data/sprites/` + `data/skills/` 中读出"有合法技能的精灵"（`_load_sprite_skills`）。
- 每队 **1~3 只**精灵，每只随机选 **≤4** 个技能。
- 这样网络见到的局面足够多样，避免只会打固定阵容。

### 1.2 一局自我博弈（`collect_rl_samples`）
双方都由 `MCTSAgent` 控制，**用同一个网络**：

1. 每个回合，轮到某方决策时：
   - 以**本方视角**把局面编码成 446 维（`encode_battle_state`）。
   - 跑一次 **MCTS**（`num_simulations` 次模拟）得到动作访问分布 `π`（10 维）。
   - 把 `(state, π)` 记进该 agent 的 `history`。
   - 按温度 `T` 从 `π` 采样真正执行的动作。
2. 回合推进直到分出胜负或到 `max_turns`（默认 150）。

### 1.3 双视角采样（关键改进）
游戏是**同时出招**的：`battle.execute_turn` 会先让 A、B 各自 `choose_action`，再结算。
因此**一局同时产出 A、B 两方的训练样本**：
- A 的样本：状态按 A 视角编码，结果 `v = outcome_a`。
- B 的样本：B 在内部把 `player_a/player_b` 临时交换，所以状态是**B 自己视角**，结果取反 `v = -outcome_a`。

→ 数据量翻倍、消除先手偏置，且每个决策**只搜索一次**（旧实现 A 侧重复搜了两遍）。

### 1.4 三元组与维度
最终每个样本是 `(state, π, z)`：

| 张量 | 形状 | 含义 |
|---|---|---|
| `X` | `(N, 446)` | 局面向量 |
| `P` | `(N, 10)` | MCTS 访问分布（策略目标） |
| `v` | `(N,)` | 终局结果 `z ∈ {+1, 0, -1}`（本方视角：赢/平/输） |

#### 446 维状态构成（`encode.py`）
```
全局 56  +  己方场上 115  +  己方板凳 80  +  对方场上 115  +  对方板凳 80  = 446
```
- **全局 56**：回合(2)、天气(3)+剩余(1)、双方印记(7正+6负)×2、魔力(2)、双方道具(6)×2、双方奉献(5)×2。
- **场上精灵 115**：本体 51（HP/能量/蓄力/修正/速度/元素 one-hot 18/异常 7/buff 10/先制/锁换…）+ 技能 4×16。
- **板凳精灵 16/只 ×5 = 80**：HP/能量/克制关系/最强技能威力/可用技能数/异常/buff/特性时机等。
- 约定：空槽位→全 0；不完全信息→ -1（自我博弈时全可见，故恒为 0/正常值）。

#### 17 维动作空间（`mcts.py`）
```
0-9 → 技能槽 0-9      10-14 → 换宠到板凳 0-4      15 → 聚能      16 → 使用道具
```
`get_valid_actions` 会生成合法性 mask（冷却/封印/能量不足/无板凳/锁换/道具耗尽 → 屏蔽）。

---

## 2. 用什么方法训练

### 2.1 网络结构（`model.py::BattleNet`）
共享主干（MLP）+ **双头**：

```
输入(446) → [Linear→ReLU]×len(hidden) (默认 256,128) → 主干特征
                              ├─ value_head : Linear→Tanh → v ∈ [-1, 1]
                              └─ policy_head: Linear(→17)  → 动作 logits
```
- `forward_with_mask` 会把非法动作 logit 置 `-1e9` 再 softmax，保证只在合法动作上输出概率。
- 默认参数量很小（~15万级），CPU 也能跑。

### 2.2 损失函数（`train_rl`）
对每个 batch：

```
value_loss  = MSE(v_pred, z)                          # 价值头：预测终局胜负
policy_loss = - Σ_a  π_a · log_softmax(logits)_a       # 策略头：模仿 MCTS 分布（交叉熵）
loss        = value_loss + policy_loss                 # 等权相加
```
- 优化器：**Adam**，`lr=1e-3`，`weight_decay=1e-4`（L2 正则，抑制过拟合）。
- 训练/验证按 `val_split=0.1` 切分；小数据集有保护（至少 1 个验证样本）。

> 直觉：**value 头**学"这个局面我方赢面多大"，**policy 头**学"这个局面 MCTS 觉得该走哪"。
> 网络变准 → MCTS 用它做先验和叶子评估更准 → 产出更强的 `π` 和更可信的 `z` → 网络又更准。

### 2.3 MCTS 把"直觉"变"深思"（`mcts.py::mcts_search`）
每步决策跑 `num_simulations` 次模拟，每次：
1. **Selection**：沿 PUCT 最大的子节点下行
   ```
   score(a) = Q(a) + c_puct · P(a) · √N(parent) / (1 + N(a))      # c_puct=2.0
   ```
   - `Q(a)`=该动作子树平均价值，`P(a)`=网络策略先验，`N`=访问次数。
2. 每下行一层 = 推进一整回合（`_step_battle`）：**我方按选中动作**，**对手用 `NetworkPolicyAgent`（当前网络策略头，无搜索）**采样动作。
3. **Expansion & Evaluation**：到叶子用网络估值 `value`；若已终局则用真实结果 ±1/0。
4. **Backprop**：把叶子价值回传路径上所有节点（价值**始终从我方视角**，因对手被建模为固定策略，无需 negamax 翻号）。

根节点加 **Dirichlet 噪声**（`α=0.3`，强度 `root_noise=0.25`）保证探索；
最终输出 `π_a ∝ N(a)`（访问次数归一化）。

> **真自我博弈的关键**：搜索内对手是**当前网络**而非规则 AI（`RuleAgent`）。
> `NetworkPolicyAgent` 是"槽位驱动"的——永远为 `player_b` 决策、不持有会因状态回滚而失效的引用，
> 因此对 A 侧、B 侧（已交换）的搜索都通用且正确。

---

## 3. AI 如何自我强化（迭代循环，`main()`）

每一轮 `iteration` 做四件事：

1. **自我博弈产数据**：用 `best_model`（当前最强）跑 `--battles` 局，得到 `(X,P,v)`。
   - 温度退火：`T = temperature × 0.9^(iter-1)`，前期多探索、后期更确定。
2. **经验回放缓冲**：把本轮数据 `append` 进 `deque(maxlen=--buffer)`，
   合并**最近 N 轮**数据一起训练 → 稳定、不过度依赖单轮噪声。
3. **训练候选**：在 `model`（候选，初始=best 的副本）上用合并数据 `train_rl`。
4. **门控评估 + 晋升/回滚**（见第 4 节）：
   - 候选 vs best 对打，胜率 ≥ `--gate` → **晋升**（`best ← 候选`，存 `*_best.pt`）。
   - 否则 **回滚**（`候选 ← best`），保证**单调不退化**。

数据由 `best_model` 产生、训练作用于 `候选 model`，这正是 AlphaZero 的标准做法。
最终保存的 `--output` 是**最优模型**。

---

## 4. 如何评估 AI 能力

### 4.1 门控评估（`evaluate`，训练内置）
- **候选 vs 最优**对打 `--eval-games` 局，每步 `--eval-sims` 次 MCTS。
- **贪心**（`temperature=0`）、**无探索噪声**（`root_noise=0`）——评估要"发挥真实水平"。
- **交替先后手**：偶数局候选执 A、奇数局执 B，消除先后手偏置。
- 胜率 = `(胜 + 0.5×平) / 局数`；`≥ gate(默认0.55)` 才晋升。

> 这是**相对评估**（跟自己上一版比）。胜率 50% 表示没进步，>55% 表示确有提升。

### 4.2 测试（`test_selfplay.py`）
- `test_rollback_determinism`：同一快照重建并以相同动作重放两次，结果必须**完全一致**
  —— 这是 MCTS 可信的前提（确定性引擎 + 序列化无损）。
- `test_network_policy_agent_valid_action`：对手 agent 产出合法动作。
- `test_collect_rl_dual_perspective`：双视角采样、形状(446/10)、标签 ∈ {-1,0,1}。
- `test_advise_single_smoke` / `test_pimc_advise_smoke`：建议管线可跑通。

---

## 5. 关键指标一览

| 指标 | 出处 | 含义 / 怎么看 |
|---|---|---|
| `train_v_loss` / `val_v_loss` | `train_rl` | 价值头 MSE，应随训练**下降**；val 明显高于 train = 过拟合 |
| `train_p_loss` / `val_p_loss` | `train_rl` | 策略头交叉熵，越低说明越能模仿 MCTS |
| `val_acc` | `train_rl` | 价值**符号**预测准确率（赢/输方向是否判对），是直观的能力代理 |
| **候选胜率** | `evaluate` | 核心指标；>55% 晋升，长期停在 ~50% 说明触顶或学不动 |
| 样本/秒 | 自动日志 / `collect_rl_samples` | 吞吐，决定一轮要多久（CPU 上 sims 越大越慢） |
| 用时占比 | 自动日志 | 每轮拆分 `selfplay/train/eval/checkpoint/other`，用于判断优化方向 |
| 结果分布（赢/输/平） | 每轮打印 | 平局过多 → 可能频繁打满 `max_turns`，需排查 |

训练默认会在 `backend/engine/ai/log/` 写 3 类日志：

- `run_<时间戳>.log`：完整控制台输出。
- `run_<时间戳>.jsonl`：结构化指标（每轮一行），含平局率、门控胜率、loss、用时秒数和用时占比。
- `run_<时间戳>_summary.txt`：训练结束汇总表，包含指标表和“用时占比”表。

可用 `--log-dir` 改日志目录，或用 `--no-log` 关闭自动日志。

---

## 6. 奖励是怎么定义的

这是 **AlphaZero 式稀疏终局奖励**，不是逐步 reward shaping：

- **唯一奖励来自对局结果**：本方赢 `z=+1`、输 `z=-1`、平 `z=0`。
- **一局内所有状态共享同一个 `z`**（无折扣，γ=1），作为 value 头的回归目标。
- **没有任何中间奖励**（不奖励造成伤害、不惩罚掉血）——避免人为偏置，让网络自己发现
  "什么局面更可能赢"。
- 策略目标不是奖励，而是 **MCTS 访问分布 `π`**（搜索蒸馏）。

> 含义：value 头本质在做"给定局面，预测最终胜负期望"的回归；
> 长期胜负信号通过大量自我博弈反向塑造出对局面的价值判断。

---

## 7. 训练指令

### 7.1 完整参数（`python -m backend.engine.ai.train -h`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--mode` | `rl` | `rl` 自我博弈 / `supervised` 用 RuleAgent 监督价值头 |
| `--iterations` | `5` | RL 迭代轮数 |
| `--battles` | `200` | 每轮自我博弈局数 |
| `--sims` | `200` | 自我博弈每步 MCTS 模拟次数 |
| `--epochs` | `20` | 每轮训练 epoch 数 |
| `--batch-size` | `256` | |
| `--lr` | `1e-3` | 学习率 |
| `--hidden` | `256,128` | 主干隐藏层 |
| `--dropout` | `0.0` | |
| `--weight-decay` | `1e-4` | L2 正则 |
| `--buffer` | `5` | 经验回放保留最近 N 轮数据 |
| `--eval-games` | `20` | 门控对局数，`0`=关闭门控 |
| `--eval-sims` | `100` | 门控每步模拟次数 |
| `--eval-workers` | `1` | 门控评估并行 worker 数（`1`=串行） |
| `--gate` | `0.55` | 晋升胜率阈值 |
| `--root-noise` | `0.25` | 自我博弈根节点 Dirichlet 噪声 |
| `--temperature` | `1.0` | 自我博弈采样温度（随迭代退火） |
| `--output` | `checkpoints/model_rl.pt` | 最终（最优）模型路径；另存 `*_best.pt` 与每轮 `*_iterK.pt` |
| `--resume` | `""` | 从已有 `.pt` 继续 |
| `--device` | 自动 | `cuda` / `cpu` |
| `--workers` | `1` | 自我博弈并行 worker 数（`1`=串行） |
| `--batched-inference` | 关 | 多 worker 时主进程 CUDA 批量推理 |
| `--inference-batch-size` | `128` | 批量推理最大 batch |
| `--inference-timeout-ms` | `5` | 攒 batch 等待毫秒 |
| `--progress-every` | `10` | 每 N 局打印自我博弈进度 |
| `--max-turns` | `100` | 自我博弈单局回合上限（默认低于旧版 150，减少打满回合） |
| `--eval-max-turns` | `150` | 门控评估单局回合上限 |
| `--draw-margin` | `0.15` | 打满回合时，归一化局面分差低于此值才记 `z=0` |

**终局裁决（减少平局噪声）**：除正常击杀外，若单局打到 `--max-turns` / `--eval-max-turns` 仍未分胜负，会按双方存活数、队伍 HP 比例、魔力与在场能量计算局面分；分差 ≥ `--draw-margin` 则判胜负并写入样本 `z=±1`，否则记平局。训练日志会打印 `终局原因(局)`（如 `max_turns_a`、`decisive_b`）。

### 7.2 常用配方

冒烟自测（几分钟，确认管线能跑通）：
```bash
python -m backend.engine.ai.train --iterations 1 --battles 4 --sims 16 --epochs 5 --eval-games 4 --eval-sims 16
```

并行自我博弈冒烟（2 worker + 批量推理，确认多进程能跑通）：
```powershell
python -m backend.engine.ai.train --device cuda --iterations 1 --battles 4 --sims 4 `
  --epochs 1 --eval-games 0 --workers 2 --batched-inference `
  --inference-batch-size 64 --progress-every 1
```

**5060 Ti 16G 推荐并行长跑**（多核 CPU + 单卡批量推理；自我博弈和门控评估都可并行）：
```powershell
python -m backend.engine.ai.train --device cuda `
  --iterations 50 --battles 40 --sims 64 `
  --epochs 80 --batch-size 512 --hidden 512,256 `
  --buffer 8 --eval-games 16 --eval-sims 64 --eval-workers 8 --gate 0.55 `
  --workers 8 --batched-inference `
  --inference-batch-size 128 --inference-timeout-ms 5 `
  --progress-every 1 `
  --output checkpoints/model_rl.pt
```

> **并行原理**：`N` 个子进程各自跑局级 self-play 或门控评估（吃满 CPU），所有 `forward_with_mask` 经队列送到主进程合并 batch 在 CUDA 上执行。自我博弈使用单模型 `BatchedInferenceServer`；门控评估使用双模型 `BatchedModelInferenceServer`，请求里区分 `candidate` / `best`。`--workers` 和 `--eval-workers` 建议设为物理核心数附近（如 6~10）；Windows 使用 `spawn`，请始终通过 `python -m backend.engine.ai.train` 启动。

单 GPU / 较强 CPU 的正式训练（串行，兼容旧行为）：
```bash
python -m backend.engine.ai.train --iterations 30 --battles 60 --sims 200 \
  --epochs 15 --buffer 5 --eval-games 20 --eval-sims 100 --gate 0.55 \
  --output checkpoints/model_rl.pt
```

冷启动（先用规则 AI 监督价值头，再切 RL，收敛更快）：
```bash
# 1) 监督预热（只训练 BattleValueNet 价值头）
python -m backend.engine.ai.train --mode supervised --battles 500 --epochs 30 \
  --output checkpoints/value_pretrain.pt
# 2) 正式 RL（BattleNet 是双头网络，价值头权重不能直接 load，故 RL 从头/或 --resume 双头权重）
python -m backend.engine.ai.train --iterations 30 --battles 60 --sims 200
```
> 注意：监督模式产出的是**单头** `BattleValueNet`，与 RL 的**双头** `BattleNet` 结构不同，
> 不能直接 `--resume` 互通；监督模式主要用于验证"价值是否可学"这一前提。

继续训练：
```bash
python -m backend.engine.ai.train --resume checkpoints/model_rl_best.pt --iterations 20
```

### 7.3 部署 / 实战建议（`advise.py`）
```python
from backend.engine.ai.model import BattleNet
from backend.engine.ai.advise import advise_single, advise, make_determinizations

model = BattleNet.load("checkpoints/model_rl_best.pt")

# 完全信息（复盘）：
adv = advise_single(battle, model, factory, num_simulations=400)
print(adv.summary())          # 估计胜率 + Top3 动作

# 实战（对手板凳未知）→ PIMC：
dets = make_determinizations(battle, factory, bench_pool=对手可能板凳列表, k=20)
adv = advise(dets, model, factory, num_simulations=200)
print(adv.best_action, adv.summary())
```
PIMC 思路：采样 K 套对手隐藏配置 → 每套跑一次 MCTS（无噪声）→ 访问分布加权平均。

---

## 8. 如何判断"这套训练代码是否有效"

按下面顺序逐条确认，从"能不能跑"到"是不是真的在变强"：

### 8.1 正确性前提（必须先过）
```bash
pytest backend/engine/ai/test_selfplay.py -x --tb=short
```
- 回滚一致性测试通过 → MCTS 的状态快照/恢复可信，搜索结果有意义。
- 若回滚测试失败，**后面一切胜率都不可信**，先修引擎确定性/序列化。

### 8.2 价值是否可学（监督模式做对照）
先跑 `--mode supervised`：如果连"用规则 AI 数据预测胜负"的 `val_acc` 都上不去（比如 < 0.6），
说明 **446 维状态信息量不足或编码有 bug**，RL 也难成功。这是最便宜的可学性体检。

### 8.3 RL 是否在自我强化（核心）
看每轮的 **候选胜率**：
- 健康：早期常 >55% 触发晋升，随版本变强逐渐回落到 ~50%（说明对手也在变强）。
- 异常：长期 <50% 且频繁回滚 → 学习率/数据量/sims 配置不当，或奖励信号太稀疏。
- 同时看 `val_v_loss` 是否下降、`val_acc` 是否上升。

### 8.4 绝对水平基线（建议补充）
门控是**相对**评估（跟自己比），可能"菜鸡互啄也在涨胜率"。建议加一个**绝对基线**：
让训练好的模型 vs `RuleAgent` 打 N 局看胜率（规则 AI 水平固定，是很好的标尺）。
可参照 `evaluate` 写一个 `MCTSAgent` vs `RuleAgent` 的对打脚本——**当前仓库尚未内置该基线，
是评估有效性时值得优先补的一项**。

### 8.5 已知局限 / 影响有效性的因素（评估时要心里有数）
- **搜索内对手是"原始策略头"而非完整搜索**：是 PIMC/AlphaZero 在同时博弈下的常见近似，
  对手建模偏弱时，搜索可能高估己方。
- **单视角价值**：价值始终从我方视角、对手按固定策略推进，不是严格 minimax；同时出招的博弈
  本身存在不可消除的对手不确定性。
- **平局/打满回合**：`max_turns=150` 的对局记为平（`z=0`），过多平局会稀释训练信号。
- **队伍随机**：泛化好，但也让"是否变强"更难一眼看出（建议固定评估阵容集做对照）。
- **吞吐**：纯 CPU 下 `sims` 大时很慢；每步要重建 battle 做回滚，是主要开销。可用 `--workers` + `--batched-inference` 并行加速自我博弈阶段。
- **无持久化指标**：目前只打印 stdout，建议自行落盘 loss/胜率曲线以便横向对比。

---

## 9. 名词速查

| 名词 | 含义 |
|---|---|
| 自我博弈 self-play | AI 用同一网络左右手互搏产生训练数据 |
| MCTS | 蒙特卡洛树搜索；用网络当先验/估值，多次模拟后按访问次数给出动作分布 |
| PUCT | MCTS 选择公式，平衡利用 `Q` 与探索 `c_puct·P·√N/(1+N)` |
| 策略目标 π | MCTS 访问分布，策略头的监督标签 |
| 价值目标 z | 终局结果 ±1/0，价值头的回归标签 |
| 门控 gating | 候选须对打胜率 ≥ 阈值才替换最优模型，保证单调变强 |
| 经验回放 buffer | 混合最近 N 轮数据训练，提升稳定性 |
| PIMC | 完美信息蒙特卡洛；对未知信息多次"决定化"后求平均，用于实战部署 |
```
