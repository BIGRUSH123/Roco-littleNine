# -*- coding: utf-8 -*-
"""native/tools/append_notes12.py — 追加 PORTING_NOTES 第 10 节。

meta 阵容爬取 + 40 支原型队 + 动作空间 17→22（首领形态由玩家选）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8")

_NOTES = '''
## 10. meta 阵容爬取（rocopvp 共享阵容）+ 40 支原型队（2026-09-20）

数据源定位：rocopvp.tzrain.wiki / roco-pvp-asst.pages.dev / roco-showdown.com
是同一个 Next.js 部署，API 端点藏在 chunk 的路径字面量里：
  GET /api/popular/teams   → 87 支阵容（item.snapshot = {team, builds}）
  GET /api/popular/builds  → 444 个单精灵配置（含 tags/描述）
每 build 含 name(可被玩家改) / creatureId / selectedSkillNames / bloodline /
statMarks(extremeStat + plusStats×3 + minusStat) / gameTeamCode。

工具：`native/tools/scrape_meta_teams.py`（fetch/verify/build 三个子命令）
- fetch：指数退避（429/567/5xx，1→60s，6 次）抓两个端点 → 缓存
  `backend/engine/ai/data/scraped_teams.json`（入库，保证可复现）
- 精灵解析优先级：池内显示名 → SpriteDB（含外观回退）→ creatureId 投票表
  → 技能集合匹配。实测 478 名字 + 33 creatureId + 11 技能匹配 = 87/87 全解析
  （站点名字被玩家改过：`肉千棘盔` / `速度 音速犬` / `狼王队-卡瓦重（雪山附近的样子）`）
- 字段映射：plusStats(3) → iv_fixed；extremeStat(加)+minusStat(减) → nature_fixed；
  bloodline.attribute → bloodline（首领血脉 → '首领'）；gameTeamCode 的
  `魔法：X` 行 → item（进化之力/愿力；无该行时首领队 → 进化之力，其余 → 愿力）
- 技能合法性 = 池内技能 ∪ 所选血脉的血脉技能（站点阵容可携带血脉技能，如
  火神的 跺地 / 翠顶夫人的 水弹枪）；不合法的剔除并记账，不足 3 个从池内同类型补齐

选择 40 支（`build --target 40 --sprite-cap 4 --boss-teams 8 --flex-per-team 1`）：
按（修补数, 站点标签优先级 比赛/排位>体系>平衡>科研>娱乐, 点赞数）贪心，
限制单精灵最多 5 队 + 禁止重复组合 + 每队挂 1 个同角色替补（alt）：
99 只去重精灵 / 240 槽位（平均 2.42 次出场），首领血脉 20 队，道具 20/20，
元素与原型分散（排位 19 / 体系 7 / 首领进化流 5 / 平衡 2 / 比赛 1 / 娱乐 1）。

审计：`native/tools/audit_meta_teams.py` → 40 队 0 问题，
26 只首领血脉精灵全部可首领化（否则进化之力会被掩码屏蔽）。

队伍道具（魔法）随队伍传入对局：`meta_teams.item` → `item_from_team()` →
`_random_teams` 返回 (team_a, team_b, item_a, item_b)。首领队的进化之力
必须随队传入，否则「首领进化流」在数据里根本不出现（随机道具只有 50% 概率）。
`_random_teams` 返回值从 2 元组变 4 元组，所有调用点（evaluate_checkpoints /
rust_selfplay_hook / native 诊断工具 / 测试）已同步。

meta_teams.json 新字段：`iv_fixed` 3 项 = 精确拉满（爬取来源），2 项 = 第三项
随机（手写队伍的多样性来源）；`nature_fixed` 精确性格；`bloodline` 血脉透传；
`item` 队伍道具。

## 10.1 动作空间 17 → 22：首领形态由玩家选（2026-09-20）

规则确认（用户）：**首领形态是玩家选择的**（血脉=首领不能变、元素自带），
所以「进化之力变成哪个形态」必须进动作空间。

布局：0-9 技能 / 10-14 换宠 / 15 聚能 / **16 愿力** / **17-21 进化之力的
首领形态槽位**（最多 5 个候选，实测最多 4：迪莫 → 圣光/圣水/圣火/圣草迪莫）。

实现：
- `common/constants.py`：`ITEM_VARIANT_SLOTS=5` / `ITEM_VARIANT_ACTION_BASE=17`
- `common/sprite_db.py`：`leader_form_candidates(number, appearance)`——
  同外观 → 默认外观 → 其余（仅前两类都空时）按名排序；动作索引与候选下标同序
- `sim/action.py`：`Action.variant`（道具形态槽位）
- `sim/battle_mechanics.py`：`item_variants(team)`（掩码与编码器同源）+
  `_resolve_item(team, variant)`（越界/缺省取首位，兼容 API 与旧调用点）
- `engine/ai/core/mcts.py`：`NUM_ACTIONS=22`、掩码 17-21、`action_index_to_action`
  支持 variant；`mcts_parallel.NUM_ACTIONS` 改为从 mcts 导入（单一来源）
- `sim/agent_v2.py`：`_leader_form_action` —— 多候选家族按属性克制和挑形态
  （单候选退化 `Action(kind='item')`，行为与旧版一致）
- `engine/ai/bc_record.py`：`action_to_index` 支持 variant → 17+k
- 编码器/模型：新增 `form_elements (5,2)` + `form_avail (5,)` 输入块；
  模型 `item_head` 从 1 维扩到 6 维（16 愿力 + 17-21），融合层 1248 → 1328
- `api/schemas.py`：`ActionRequest.variant`、`ItemState.variants`（前端可选形态）
- `replay_buffer.check_action_width`：拒绝 17 维旧数据（动作空间扩展前生成），
  报错信息提示重新生成数据

测试：`test_leader_appearance.py` 增 2 项（多候选家族掩码 17-21 + 指定槽位
首领化到该形态 / 单候选缺省落首位）；`test_bc_pipeline.py` 增 2 项
（meta 精确 IV/性格/血脉/道具；随仓库交付的 meta_teams.json 数据完整性门禁）。
全量 pytest 557 passed / 5 skipped。

差分夹具**无需重录**：夹具走 `differential/recorder` 的 RuleAgent + 种子道具
（不受 V2 agent 与掩码改动影响），24 个夹具事件流逐字节一致。

Rust 同步待办（重要，追加）：
1. 动作空间 22：道具动作带 variant（17-21 = 首领形态槽位），掩码按
   `leader_form_candidates` 顺序放开；单候选族只放 17。
2. `_resolve_item(team, variant)` 的形态选择语义 + 越界回退首位。
3. 编码器候选形态块（5×2 元素 ID + 可用性），模型输入契约变更 →
   Rust 侧 batch 协议需同步（Python 侧由 encoder/evaluator 键集合驱动）。
4. `item_variants` 的排序必须与 Python 完全一致（同外观 → 默认外观 → 名称序），
   否则动作索引与形态错位。
5. `spec["leader_forms"]`（候选列表）已加入 `rust_selfplay_hook._build_sprite_spec`，
   Rust 侧需解析该字段（旧的单数 `leader_form` 保留兼容）。
'''

path = _ROOT / "native" / "PORTING_NOTES.md"
text = path.read_text(encoding="utf-8")
if "## 10. meta 阵容爬取" in text:
    print("已存在第 10 节，跳过")
else:
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + _NOTES, encoding="utf-8")
    print(f"已追加：{path}")
