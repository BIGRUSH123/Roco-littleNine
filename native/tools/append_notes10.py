# -*- coding: utf-8 -*-
"""向 PORTING_NOTES.md 追加首领入池章节。"""
from pathlib import Path

SECTION = """

## 9.1 首领形态入池（2026-09-19）

战斗合法性确认：首领形态可直接参战，因此进入训练池。

- 池规则 v4（`sprite_random_pool._build_pool`）：
  1. 首领阶段不再排除，首领及其外观都作为独立条目入池；
  2. 前形态仍剔除：`evolved_from` = 非首领条目的 `pre_species`
     （跳过自引用、跳过首领条目）——首领化是"同编号形态切换"而非进化链，
     基础形态与首领形态都要保留；
  3. 同编号第二形态（`pre == number`）保留。
- 数据修正：全库扫描只有 2 组未标首领的第二形态——暮风隐者、满月砣
  （经用户对照 B洛克 Wiki 确认为首领），已标 `form='首领形态'`；
  圣X迪莫等首领形态原本已标记。工具：`native/tools/fix_leader_marks.py`。
- 池规模：206（修规则前）→ 274（外观独立）→ **344 条**（首领入池，净增 70 零移除）。
- 注意：首领形态的 `attributes` 是元素列表（如 深渊罗隐 ['地','恶']），
  血脉默认取首元素而非『首领』；若要训练模型学习「进化之力」首领化路径，
  组队 spec 需显式给 `bloodline='首领'`。
- 夹具已重录（种子 1-24），全量 pytest 552 passed / 5 skipped（rust 未编译）。
"""

path = Path("native/PORTING_NOTES.md")
old = path.read_text(encoding="utf-8")
if "## 9.1 首领形态入池" in old:
    print("章节已存在，跳过")
else:
    path.write_text(old + SECTION, encoding="utf-8")
    print("appended, new size:", len(old) + len(SECTION))
