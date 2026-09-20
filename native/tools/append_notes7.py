# -*- coding: utf-8 -*-
"""append_notes7.py — RuleAgentV2（社区攻略经验重写）交付记录。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "native" / "PORTING_NOTES.md"

TEXT = """
## RuleAgentV2 —— 社区 PVP 攻略经验重写（2026-09-19）

### 背景
行为克隆（BC）预训练需要一个行为模式健康的"专家"。旧 RuleAgent 对局长
（6v6 内战 mean 68 回合）且无速度/斩杀/威胁概念。

### 检索到的社区经验（B站/游民星空/4399/3DM/腾讯新闻等）
1. 速度为王：先手权决定攻防次序；速度落后时应先控场。
2. 斩杀优先：快攻体系"一两个回合结束战斗"；本回合能杀立即杀。
3. 能量管理：初期避免高耗能大招，低耗试探；能量不足最强攻击先聚能。
4. 换宠轮换：首发控场 → 次发爆发 → 尾发续航；换人有 tempo 成本，只在
   被杀威胁/对位无效时换。
5. T0 工具人+推队：开印记/撒钉子磨血 → 推队宠拿强化后一招一个。
6. 属性克制内嵌于伤害公式（calc_damage 已含克制/印记/天气）。

### 实现（backend/sim/agent_v2.py，独立类不动旧 RuleAgent）
- 决策优先级：斩杀（可击杀→最低耗斩杀技）→ 被杀威胁换位（仅慢速+威胁+
  对位更优才换）→ 进攻（最高伤害，平手低耗）→ 强化推队（无有效输出且
  能量富余时用 stat 增益）→ 残血触发换位（对位伤害高 30% 才换）→ 聚能。
- 首发对位评分：伤害期望×2 + 速度先手 0.2 + 面板生存 0.3。
- 伤害/威胁全部走引擎 calc_damage（克制/天气/印记自动包含），
  速度用 effective_stat('speed')（含 buff）。

### 验证（native/tools/validate_v2.py，100 个 6v6 角色化对阵，配对种子）
- V2 vs 旧 RuleAgent：**53% 胜率**（53胜12平35负）。
- V2 内战对局长度：**mean 32 / median 28 回合，100% 自然分胜负、零打满**
  （旧版内战 mean 68），直方图集中 10-40 回合——与实战 2-30 回合对齐。
- pytest 69（AI+sim）全绿。

### 用途
作为 BC 预训练的专家策略：RuleAgentV2 vs V2 快速生成对局（无 MCTS，
秒级/局），每个决策点记录 (编码, V2 动作, mask, 终局价值) → 策略头交叉熵
+ 价值头 MSE 预训练 → 自博弈微调（AlphaStar/绝悟范式）。
"""

with io.open(P, "a", encoding="utf-8", newline="\n") as f:
    f.write(TEXT)
print("appended", len(TEXT), "chars")
