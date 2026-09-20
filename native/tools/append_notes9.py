# -*- coding: utf-8 -*-
"""向 native/PORTING_NOTES.md 追加 appearance 数据模型章节。"""
from pathlib import Path

SECTION = """

## 9. form/appearance 维度拆分（2026-09-19）

### 背景

精灵数据原先把"形态阶段"和"外观"都塞在 `form` 字段：
`''`（基础）/ `'首领形态'`（首领）/ `'夏天的样子'`（外观）。
外观变体文件（`鸭吉吉国王（急急急鸭）` 等）form 写外观名 →
引擎 `'首领' in form` 判断失灵（首领外观不被识别）、
池子的"前形态排除"规则被自引用 `pre_species` 误触发
（11 个编号的普通形态被连带剔除）。

### 新数据模型

- `form`：阶段标记，`''`（基础）或 `'首领形态'`（首领）
- `appearance`：外观名，`''`（默认外观）或「夏天的样子」等
- 两维度独立：任何阶段都可以有多个外观；首领外观 form='首领形态'

迁移脚本 `native/tools/migrate_appearance_field.py`（已对 640 个文件执行，幂等）：
同 name 组内存在 form='首领形态' 的文件 → 该组全部标为首领阶段，
原 form 值移入 appearance。旧格式数据由
`SpriteDB._normalize_form_appearance` 读取时自动规范化（向后兼容）。

### 代码改动

- `common/models.py`：`SpeciesStats.appearance` + `display_name()` 用外观 +
  `is_leader_stage()`
- `common/sprite_db.py`：索引键 = `名字（外观）`；`get(name, appearance)`；
  `get_by_stage`；`list_appearances`（旧名 list_forms 兼容）；
  `lookup_by_number` 优先基础阶段；`save` 写 appearance
- `sim/factory.py`：`build_sprite(..., appearance=)`（form 参数保留兼容）；
  `build_player` 读 spec 的 appearance 字段
- `sim/battle_mechanics.py`：`_find_leader_form(number, appearance)` 优先同外观、
  回退默认外观；进化之力首领化**保留当前外观**
- `sim/battle.py`：`lookup_species/lookup_species_by_number` 参数语义改为 appearance
- `engine/serializer.py`：快照 species 引用带 appearance（旧快照回退读 form）
- `engine/differential/recorder.py`：状态摘要加 species_form/species_appearance
- `engine/ai/data/sprite_random_pool.py`：新池规则（见下）
- `api/main.py`：/api/sprites 按（名字,外观）独立条目（原来按名字去重会吞掉外观）

### 池规则 v3（sprite_random_pool._build_pool）

1. 首领阶段（`'首领' in form`）不入选；
2. `evolved_from` = 非首领条目的 pre_species 集合；编号在其中且**非**
   「同编号第二形态」的基础阶段剔除；
3. 同编号第二形态（pre_species == 自身 number）= 该编号最终形态，保留；
4. 池键 = `名字（外观）`，每个外观是独立条目。
结果：191 → 206（修规则前）→ **274 个条目**（外观独立后）。
注意：只有外观文件、没有默认外观的精灵（皇家狮鹫、遁地鼠等 12 个名字）
池键从「名字」变为「名字（外观）」——不是丢失。

### Rust 同步待办（重要）

- `native/roco-core` 的物种数据结构加 `appearance` 字段（serde 默认空串兼容）；
- 首领化（进化之力）目标查询需带同外观优先逻辑
  （对应 py 的 `_find_leader_form(number, appearance)`）；
- 快照序列化字段与 py 对齐（species_ref 增加 appearance）；
- 对拍夹具已按新池重录（种子 1-24），Rust 同步后重跑对拍门。

### 其他待办

- 用户侧精灵数据生成器需要输出 `appearance` 字段（当前旧格式仍可被兼容读取）；
- 前端若需展示外观名/阶段，可从 /api/sprites 的新条目名解析，
  或后续在 payload 里显式加 appearance/form 字段。
"""

path = Path("native/PORTING_NOTES.md")
old = path.read_text(encoding="utf-8")
if "## 9. form/appearance 维度拆分" in old:
    print("章节已存在，跳过")
else:
    path.write_text(old + SECTION, encoding="utf-8")
    print("appended, new size:", len(old) + len(SECTION))
