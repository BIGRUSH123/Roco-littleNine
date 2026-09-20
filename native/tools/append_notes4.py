# -*- coding: utf-8 -*-
"""append_notes4.py — exp18/19 结果与最终交付记录。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "native" / "PORTING_NOTES.md"

TEXT = """
### exp18/19 结果与最终交付（2026-09-19）
- **exp18（sims=150 训练）已中止**：门控 39-47% 全面退化。教训：**训练与
  门控的 sims 必须一致**——候选向 sims150 数据分布特化后在 sims100 门控下
  打不过基座。
- **exp19（热重启 lr2e-4，sims100）**：16 轮 1 晋升（第 12 轮 54.17%），
  但 200 局复评未保持：vs exp17_best 51.0%、vs exp13 48.75%、vs rule@s64
  0.525 —— 该晋升为噪声晋升。
- **最终交付 = exp17_deliver 第 17 轮晋升权重**，副本：
  `checkpoints/delivered/model_v1.pt`（含 README.md 证据与续训指南）。
  全部配对种子证据：vs rule@s64 0.625（基座 0.525）、vs 基座 200 局 51.75%、
  val_acc 0.85 / val_v_loss 0.32（历史平台 0.68 / ≥0.98）。
- 后续若继续冲强度：保持本配方 Sims 一致性 + eval≥150 局降门控噪声，
  或提高网络容量（1.5M 参数可能是 val_acc 0.85 后的下一个瓶颈）。
"""

with io.open(P, "a", encoding="utf-8", newline="\n") as f:
    f.write(TEXT)
print("appended", len(TEXT), "chars")
