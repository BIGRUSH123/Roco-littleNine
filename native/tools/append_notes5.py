# -*- coding: utf-8 -*-
"""append_notes5.py — 换边增强验证结论 + exp20 长跑启动。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "native" / "PORTING_NOTES.md"

TEXT = """
## 换边增强验证（2026-09-19）：已天然存在，无可挖收益
- 探针 `native/tools/dbg_swap_sym.py`：spec_0001 实战 8 个阶段（turn 1-20），
  encode(B视角) vs swap(players)+encode(A视角) —— **全部 10 数组严格对称**
  （atol 1e-6，零失配）。
- 结论：自博弈为同步决策制，每个决策点 MCTSAgent 双方各自搜索并记录，
  A/B 双视角样本天然成对且编码一致——"换边增强"想要的信息已经在训练集里，
  样本级增强为零增益。假设证伪，未做无谓改动。
- 探针同时确认了价值头此前泛化失败与视角不对称无关（对称性完好）。

## exp20_long（进行中）：当前配方长跑主线
- base=delivered/model_v1（exp17 best），lr 3e-4 余弦→3e-5，40 轮 ≈ 9h，
  battles 350、buffer 12、epochs 4、leaf 128、**eval 200 局（σ≈3.5%）、
  gate 0.55**、mirror 0.2、temp-decay 0.98。
- 验收看三点：晋升次数（期望 5-8）、val_v_loss 是否稳定 <0.6、
  终局 vs rule @s64 相对 0.625 的增量。
"""

with io.open(P, "a", encoding="utf-8", newline="\n") as f:
    f.write(TEXT)
print("appended", len(TEXT), "chars")
