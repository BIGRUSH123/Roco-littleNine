# -*- coding: utf-8 -*-
"""append_notes3.py — 训练诊断与 exp17/18 交付记录。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "native" / "PORTING_NOTES.md"

TEXT = """
## 训练诊断与模型交付（2026-09-19）：价值头记忆化是历史实验卡死的病根

### 诊断（基于 exp2-16 全部运行日志 + 锦标赛）
- **存量 checkpoint 锦标赛**（vs RuleAgent，配对种子，40 局，sims=64）：
  exp11 0.512 / exp12 0.487 / exp13 0.525 / formal_v1 0.487 —— 全部 ~50%，
  两周实验对 rule 无净进步。随机初始化模型 vs rule = 0.167（rule 很强，
  训练确实学到过东西，但卡在平台）。
- **病根 1 —— 价值头零泛化**：exp13 60 轮 val_v_loss 始终 0.98-1.42，
  而 ±1 目标下常数预测器 MSE=1.0 → 价值头在验证集上从未超过常数；
  train_v_loss 0.139→0.066 = 纯记忆化。MCTS 叶评估等于用噪声 → 搜索无法
  变强 → 自博弈数据质量锁死 → 全线平台。根因：lr 1e-3 × 10-15 epochs 在
  5 轮小缓冲上反复重训。
- **病根 2 —— 门控噪声**：100 局 σ≈5%，0.55 晋升线恰在 1σ 处，
  假晋升/假拒绝率高，与振荡史一致。
- 校准：exp13 best @sims=200 vs rule = 0.717（s64 时 0.525）→ 搜索能补偿
  策略，价值头修复后强度应随 sims 兑现。

### exp17_deliver（25 轮，4.36h，base=exp13 best）
- 配方：**lr 3e-4 余弦→3e-5、epochs 4、battles 280、buffer 8、weight-decay
  3e-4、dropout 0.1、temp-decay 0.97、leaf 128（吞吐 45 样本/s = exp16 的 3.2x）**
  + 用户未提交的 game_id 分组切分（val 指标不再泄漏）。
- 结果：**3 次晋升（第 7/10/17 轮）**；val_acc 峰值 0.85（exp13 平台 ≤0.68）；
  **val_v_loss 0.32-0.59（exp13 恒 ≥0.98）** —— 价值头首次泛化，病根修复确认。
- 终评：vs exp13_best 配对 200 局 = **51.75%**；vs rule @s64 = **0.625**
  （exp13 同设定 0.525）；vs rule @s200 = 0.688。
- 教训：晋升后门控分回落至 47-52% 属正常（基线抬高），跨迭代复利靠的是
  反复小幅晋升；单次 4h 只买到 +2~10%。

### exp18_deliver（进行中）：base=exp17 best，lr 2e-4、sims=150（数据质量↑）、
  eval 150 局 / gate 0.54（门控噪声↓）、16 轮 ≈ 5h。交付取两者最优。
"""

with io.open(P, "a", encoding="utf-8", newline="\n") as f:
    f.write(TEXT)
print("appended", len(TEXT), "chars")
