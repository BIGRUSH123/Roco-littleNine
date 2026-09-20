# -*- coding: utf-8 -*-
"""append_notes6.py — 6v6 对齐：格式确认、分布修复、exp21 启动。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "native" / "PORTING_NOTES.md"

TEXT = """
## 6v6 实战格式对齐（2026-09-19 下午）：训练分布重大修复

### 用户确认实战格式：自组队 6v6，先力竭 4 只精灵一方失败
- 规则侧已天然对齐：lives=4（每次力竭扣 1、归零判负）≡ 先力竭 4 只判负。
- **训练分布严重失配**：_random_teams 原 1-3v1-3（1/3 还是 1v1 快棋），而
  编码器 12 精灵槽、动作空间 10-14 换人槽都是按 6v6 设计的——3v3 训练下
  槽位 3-5/9-11 与换人动作 12-14 从未被训练。exp20（3v3 长跑）当即中止。

### 修改
- `_random_teams`：队伍大小固定 6（实战同分布；多样性来自精灵/技能/性格/
  IV/道具采样）。6v6 下"整队零攻击"概率≈(8.2%)^6≈0，无需加约束。
- `test_random_teams_use_one_shared_team_size` 更新为 6v6 语义（断言两队=6）。
- 6v6 py/rust 整局一致性抽查（dbg_6v6_parity.py，复用 run_python/run_rust）：
  **5/5 逐位一致**（含一局 150 回合打满平局）。
- pytest 1656 全绿。

### 冒烟（exp21_smoke，40 局×2 轮）
- 6v6 每局 ~161 样本（3v3 的 2.4 倍），自博 34-35 样本/s，管线全链路正常，
  第 2 轮即出现晋升。max_turns 60→80（6v6 对局更长，减少截断平局）。

### exp21_6v6（进行中）：base=delivered/model_v1，24 轮 ≈ 12h
- battles 180 / buffer 8 / epochs 4 / lr 3e-4 余弦 / leaf 128 / mirror 0.2 /
  eval 150 局 @s100 / gate 0.55 / max_turns 80。
- 这是**对齐实战格式的第一次训练**；此前所有历史模型（含 model_v1）都是
  1-3v1-3 分布下训出来的，6v6 强度需重新积累。
"""

with io.open(P, "a", encoding="utf-8", newline="\n") as f:
    f.write(TEXT)
print("appended", len(TEXT), "chars")
