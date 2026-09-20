# -*- coding: utf-8 -*-
"""向 PORTING_NOTES.md 追加 9.2 节（首领不可二次进化 + 道具掩码）。"""
from pathlib import Path

SECTION = """

## 9.2 首领形态不可二次进化 + 道具可用性掩码（2026-09-19）

规则确认（用户）：**首领精灵不能再用进化之力**。只有带『首领血脉』的
基础阶段精灵可以使用进化之力变成同编号首领形态
（例：首领血脉的迪莫 → 圣X迪莫）。

实现：
- `sim/battle_mechanics.py` 新增 `item_usable(team)`：道具可用性的唯一判定，
  被 `_resolve_item`（结算）与动作掩码共用。
  进化之力条件 = 首领血脉 + **非首领阶段**（`species.is_leader_stage()` 为假）
  + 同编号存在首领形态；愿力条件 = 元素血脉。
- `engine/ai/core/mcts.get_valid_actions`：动作 16（道具）仅在
  `battle.item_usable(team)` 为真时给出——修复此前"道具动作恒可用"导致
  MCTS 选中非法道具白费一回合的隐患（对愿力同样生效）。
- `api/main.py` `/sprites/{name}/evolution`：首领形态直接返回
  `can_evolve=False`。
- 测试：`backend/engine/test_leader_appearance.py` 3 项
  （外观条目构建 / 首领化保留外观 / 首领不可再进化且掩码=0）。
  全量 pytest 553 passed / 5 skipped（rust 未编译）。

Rust 同步待办（追加）：道具可用性判定（含阶段检查）与动作掩码 16 号位行为需一致。

训练待办：要让模型覆盖「首领血脉 + 进化之力」路径，组队 spec 需显式
`bloodline='首领'`（首领形态的 attributes 是元素，默认血脉不是首领）。
多首领家族（如迪莫有 圣光/圣水/圣火/圣草迪莫 4 种）当前取同外观首个匹配，
若游戏内是按元素/血脉决定具体形态，需补充规则。
"""

path = Path("native/PORTING_NOTES.md")
old = path.read_text(encoding="utf-8")
if "## 9.2 首领形态不可二次进化" in old:
    print("章节已存在，跳过")
else:
    path.write_text(old + SECTION, encoding="utf-8")
    print("appended, new size:", len(old) + len(SECTION))
