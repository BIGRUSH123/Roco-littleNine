# -*- coding: utf-8 -*-
"""向 native/PORTING_NOTES.md 追加 BC 管线章节。"""
from pathlib import Path

SECTION = """

## 8. BC 预训练管线（池无关部分，2026-09-18）

目标：AlphaStar/绝悟范式——规则专家数据行为克隆预训练 → 自博弈微调，
解决从零自博弈前几轮的随机起步与换人拖招。

### 已交付（不依赖精灵池内容）

- `backend/sim/agent_v2.py` 参数化：`SpriteStrategy`/`TeamStrategy` 数据类 +
  `RuleAgentV2(team, player, strategy=None)`。每队可声明 精灵→(首发候选 lead /
  必保 preserve / 能量预算 energy_hold / 换宠阈值覆盖)；strategy=None 时行为
  与旧版逐字节一致（默认阈值即原常量）。
- `backend/engine/ai/data/meta_teams.py`：meta_teams.json 加载（mtime 缓存，
  `ROCO_META_TEAMS` 可覆盖路径）、`validate_meta_teams`（精灵/技能必须在池内）、
  `spec_from_team`（第三项 IV + 性格逐局扰动）、`strategy_from_team`（阈值抖动
  增加对局多样性）。文件不存在 → 一切自动退化为纯随机采样。
- `backend/engine/ai/bc_record.py`：`RecordingAgent`（合法掩码内录制
  (编码状态, 动作索引, 掩码)，动作/掩码冲突时跳过录制不喂脏标签）+
  `run_recorded_battle`（agent 工厂注入 player，双视角样本，`_random_item` 与
  train 一致）。
- `native/tools/gen_bc_data.py`：CLI。meta_frac 概率用原型队对战（双方各抽，
  含镜像比例），其余 `_random_teams` 角色化随机；输出 npz（float16/int16 压缩）
  + json sidecar（含 reason_counts / team_game_counts / 整队留出提示）。
  实测 20 局/秒级 → 2500 局约 2-3 分钟。
- `backend/engine/ai/bc_pretrain.py`：加载 npz → 整队留出切分（留出最后
  `--holdout-teams` 支 meta 队的全部对局 + 随机对局按 game 抽样）→ 复用
  `train_rl`（value MSE + masked policy CE，目标为专家 one-hot）→ 按验证集
  policy top-1 回调存最优权重 `bc_init.pt`。
- `train.py`：`_random_teams` 以 `_META_FRAC`（默认 0.6，`--meta-frac`/
  `ROCO_META_FRAC` 控制）概率两侧改用 meta 队 spec——BC 与自博弈共享分布；
  `train_rl` 新增 `val_indices`/`on_epoch` 参数（自博弈路径不受影响）；
  `--bc-init` 在自博弈启动前加载 BC 权重（`--resume` 优先级更高）。rust hook
  复用 `train._random_teams`，meta 混合自动生效。
- 测试：`backend/engine/ai/tests/test_bc_pipeline.py` 6 项（运行时从池取精灵，
  不硬编码队名），含 gen→pretrain→加载 小规模闭环。全量 pytest 501 passed。

### 池子定稿后剩余步骤

1. 用 `explore_pool.py`/`pool_summary.py` 重导池子摘要，按战术原型
   （快攻推队/均衡轮转/消耗坦克/强化爆发）选 4-6 队，写
   `backend/engine/ai/data/meta_teams.json`（格式见 meta_teams.py docstring）。
2. `python native/tools/gen_bc_data.py --games 2500 --meta-frac 0.6
   --out checkpoints/bc_data.npz`（校验失败会拒绝生成）。
3. `python -m backend.engine.ai.bc_pretrain --data checkpoints/bc_data.npz
   --out checkpoints/bc_init.pt`，看留出队 policy top-1 是否 ≥ 训练队（不崩即泛化 OK）。
4. 自博弈微调：`python -m backend.engine.ai.train --run-name bc_v1 --bc-init
   checkpoints/bc_init.pt --meta-frac 0.6` + exp17 配方（lr 3e-4, epochs 4,
   buffer 8, eval-games 150-200, gate 0.55, sims 100, leaf 128）。
"""

path = Path("native/PORTING_NOTES.md")
old = path.read_text(encoding="utf-8")
if "## 8. BC 预训练管线" in old:
    print("章节已存在，跳过")
else:
    path.write_text(old + SECTION, encoding="utf-8")
    print("appended, new size:", len(old) + len(SECTION))
