# IR_GUIDE — 回合制战斗 IR 指令参考

> **基于寄存器的领域虚拟机**，为回合制对战游戏的技能/特性编译到统一指令集而设计。
>
> 技能和特性编译到同一套 IR opcode 指令集，差异仅在于**触发方式**和**生命周期**：
> - **技能**: `effects[]` → Skill VM 即时执行
> - **特性**: `TraitToObserver` 编译器将 JSON 转换为 `Observer { cond, then, scope }`，事件触发后由 Skill VM 执行 `then[]`
>
> 运行时只有一种执行路径：RISC VM。

## 三层模型

| 层 | 对应 | 职责 | 实现 |
|----|------|------|------|
| 描述层 | JSON 数据 (`data/skills/*.json`, `data/traits/*.json`) | 游戏策划可读的效果描述 | — |
| IR 层 | opcode 指令集 | 统一的 `op + target + value` 组合 | 本文档 |
| 引擎层 | Skill VM + Observer 引擎 | 解释执行 / 事件触发 | `backend/vm/executor.py`, `backend/engine/trait_loader.py` |

---

## 一、寄存器组 — Ctx + EventContext

`Ctx` 是回合快照寄存器组。回合开始时由 `backend/engine/snapshot.py:build_ctx()` 构建，**本回合内所有指令只读寄存器**。事件瞬时标志存放在独立的 `EventContext` 子对象中。

### 1.1 EventContext — 事件瞬时上下文

- **实现**: `backend/vm/ctx.py:EventContext`
- **说明**: 仅在 Observer 触发时有效，描述"刚刚发生了什么"。在 VM when-block 处理期间所有字段为默认值。

| 字段 | 类型 | 说明 |
|------|------|------|
| `counter_succeeded` | `bool` | 本次应对成功 |
| `was_countered` | `bool` | 本次被应对 |
| `prev_counter_succeeded` | `bool` | 上次行动应对成功 |
| `target_fainted` | `bool` | 目标力竭 |
| `self_koed` | `bool` | 己方力竭 |
| `opp_switched` | `bool` | 敌方切换 |
| `self_switched` | `bool` | 己方切换 |
| `turn_end` | `bool` | 回合结束信号 |
| `skill_position_changed` | `bool` | 技能被换位 |
| `devotion_triggered` | `bool` | 奉献触发 |
| `last_tick_abnormal` | `str` | 最后 tick 的异常名 |
| `last_tick_target` | `str` | 最后 tick 的目标 (`sprite_self` / `sprite_opp`) |
| `abnormal_changed_name` | `str` | 刚变化的异常名 |
| `abnormal_changed_target` | `str` | 刚变化的目标 |
| `abnormal_applied_name` | `str` | 刚施加的异常名 |
| `abnormal_applied_target` | `str` | 刚施加的目标 |
| `skills_energy_changed_of` | `str` | 能耗变化方 |
| `positive_changed_of` | `str` | 增益变化方 |
| `positive_changed_stat` | `str` | 增益变化的 stat 名 |
| `positive_changed_steps` | `int` | 增益变化的 steps |
| `energy_changed_of` | `str` | 能量变化方 |
| `heal_of` | `str` | 治疗方 |
| `damage_taken_of` | `str` | 受伤方 |

> **EventContext 与 Ctx 快照字段的区别**: EventContext 字段是瞬时事件标志，仅在 Observer 触发时有效。Ctx 快照字段（如 `hp_self`、`energy_opp`）是回合级持久状态，整个回合可读。条件评估时，事件条件通过 `ctx.event.X` 访问（见 `backend/vm/cond.py:COND_EVAL`），状态条件直接读 Ctx 字段。

### 1.2 Ctx — 战斗状态快照

- **实现**: `backend/vm/ctx.py:Ctx`
- **构建**: `backend/engine/snapshot.py:build_ctx()`
- **寻址**: `backend/vm/resolve.py:resolve()` 通过 ADDRESS_MAP 将 `(of, q)` 映射到 Ctx 字段

#### 己方精灵

| 字段 | 类型 | 说明 | 与相似字段的区别 |
|------|------|------|-----------------|
| `hp_self` | `int` | 当前 HP | 绝对值，与 `hp_self_ratio`（比例值）互补 |
| `hp_self_ratio` | `float` | 当前 HP 比例 [0.0, 1.0] | 用于阈值比较（`hp_below`）；`hp_missing_ratio` 由 `resolve.py` 动态计算为 `1.0 - hp_self_ratio`，不存储在 Ctx 中 |
| `hp_self_max` | `int` | 最大 HP | — |
| `energy_self` | `int` | 当前能量 | 与 `energy_cost_self`（当前技能能耗）不同：前者是精灵能量池，后者是技能消耗 |
| `atk_self` | `int` | 物攻基础值 | 不含 stat_stage 加成；阶段加成由 `stat_stages_self["atk"]` 单独存储 |
| `def_self` | `int` | 物防基础值 | — |
| `sp_atk_self` | `int` | 魔攻基础值 | — |
| `sp_def_self` | `int` | 魔防基础值 | — |
| `speed_self` | `int` | 速度基础值 | — |
| `priority_self` | `int` | 行动优先级修饰 | 仅影响行动顺序，与 `speed_self`（速度值）不同 |
| `damage_reduction_self` | `float` | 减伤系数 [0.0, 1.0] | `0.0`=无减伤，`1.0`=免疫；与 `damage_reduced_self`（本回合已减免伤害量，累计值）不同 |
| `abnormal_count_self` | `int` | 异常种类数 | 与 `abnormal_stacks_self`（按名称的层数字典）不同：前者是种类计数，后者是明细 |
| `abnormal_stacks_self` | `dict[str, int]` | 异常层数明细 | 需配合 `name` 参数索引；查询时通过 `_NAMED_DICT_QUERIES` 子索引 |
| `positive_count_self` | `int` | 增益数量 | — |
| `charged_self` | `bool` | 蓄力已完成 | 与 `is_charging_self`（蓄力中，未释放）互为互斥状态 |
| `is_charging_self` | `bool` | 正在蓄力中（未完成） | `charged_self`=已蓄力可释放，`is_charging_self`=正在蓄力过程中 |
| `first_action_self` | `bool` | 本场战斗首次行动 | 与 `first_action_battle_self`（本回合首次行动）的粒度不同 |
| `first_action_battle_self` | `bool` | 本回合首次行动 | 比 `first_action_self` 更细粒度，每回合重置 |
| `times_entered_self` | `int` | 累计入场次数 | — |
| `times_left_self` | `int` | 累计离场次数 | 每次脱离/换下 +1 |
| `elements_used_count_self` | `int` | 使用过的不同系别技能数 | — |
| `just_entered` | `bool` | 本回合入场 | 用于 `sprite_entered` 条件；与 `times_entered_self`（累计次数）不同 |
| `just_acted_self` | `bool` | 刚使用过技能 | 用于 `sprite_acted` 条件 |
| `skill_elements_self` | `frozenset` | 携带技能的元素集合 | 用于 `have_skill_of` 条件 |
| `stat_stages_self` | `dict[str, int]` | 属性阶段 | 正=增益，负=减益；与 `atk_self` 等基础值分离存储 |
| `energy_cost_sum_self` | `dict[str, int]` | 技能能耗合计 `{type/element/tag: total}` | 按类别/系别/标签分组求和；查询时用 `skill_type`/`element`/`tag` 子索引 |
| `zero_cost_skill_count_self` | `int` | 携带的0能耗技能数量 | — |
| `power_mult_self` | `float` | 威力倍率修饰 | 默认 1.0；来自 VM modifier 注入 |
| `damage_mult_self` | `float` | 伤害倍率修饰 | 默认 1.0 |
| `energy_cost_mult_self` | `float` | 能耗倍率修饰 | — |
| `combo_mult_self` | `float` | 连击倍率修饰 | — |
| `life_drain_self` | `float` | 吸血比例 | — |
| `mark_bonus_own` | `float` | 己方印记伤害加成 | — |
| `bloodline_self` | `str` | 己方血脉 | 如 "首领" |
| `elements_self` | `tuple[str, ...]` | 己方精灵种族系别 | 如 `("水", "冰")` |
| `damage_reduced_self` | `int` | 本回合被减免的伤害量 | 累计值；与 `damage_reduction_self`（减伤系数）不同 |
| `last_tick_damage_self` | `int` | 最近一次 tick 受到的伤害 | — |
| `energy_delta_self` | `int` | 本次事件的能量变化量 | 瞬时值，事件级别 |
| `heal_delta_self` | `int` | 本次事件的治疗变化量 | 瞬时值 |
| `damage_taken_this_turn` | `int` | 本回合受到伤害的次数 | 累计值，用于 `on_damage_taken` 条件 |

#### 敌方精灵

| 字段 | 类型 | 说明 | 与相似字段的区别 |
|------|------|------|-----------------|
| `hp_opp` | `int` | 敌方当前 HP | — |
| `hp_opp_ratio` | `float` | 敌方 HP 比例 | — |
| `hp_opp_max` | `int` | 敌方最大 HP | — |
| `energy_opp` | `int` | 敌方当前能量 | — |
| `atk_opp` | `int` | 敌方物攻 | — |
| `def_opp` | `int` | 敌方物防 | — |
| `sp_atk_opp` | `int` | 敌方魔攻 | — |
| `sp_def_opp` | `int` | 敌方魔防 | — |
| `speed_opp` | `int` | 敌方速度 | — |
| `damage_reduction_opp` | `float` | 敌方减伤系数 | — |
| `abnormal_count_opp` | `int` | 敌方异常种类数 | — |
| `abnormal_stacks_opp` | `dict[str, int]` | 敌方异常层数明细 | — |
| `positive_count_opp` | `int` | 敌方增益数量 | — |
| `charged_opp` | `bool` | 敌方已蓄力 | — |
| `skill_elements_opp` | `frozenset` | 敌方技能元素集合 | — |
| `skill_element_count_self` | `int` | 己方携带的不同系别技能数 | — |
| `skill_element_count_opp` | `int` | 敌方携带的不同系别技能数 | — |
| `stat_stages_opp` | `dict[str, int]` | 敌方属性阶段 | — |
| `skills_energy_sum_opp` | `int` | 敌方全技能能耗之和 | — |
| `power_mult_opp` | `float` | 敌方威力倍率 | — |
| `damage_mult_opp` | `float` | 敌方伤害倍率 | — |
| `last_tick_damage_opp` | `int` | 敌方最近一次 tick 伤害 | — |
| `heal_delta_opp` | `int` | 敌方本次事件治疗变化量 | — |
| `prev_damage_taken_opp` | `bool` | 敌方上回合是否受伤 | — |
| `bloodline_opp` | `str` | 敌方血脉 | — |
| `elements_opp` | `tuple[str, ...]` | 敌方精灵种族系别 | — |

#### 双方队伍

| 字段 | 类型 | 说明 | 与相似字段的区别 |
|------|------|------|-----------------|
| `mark_count_own` | `int` | 己方队伍印记总层数 | 与 `mark_stacks_own`（按名称明细）不同：前者是总数，后者是字典 |
| `mark_stacks_own` | `dict[str, int]` | 己方印记明细 | — |
| `mark_count_opp` | `int` | 敌方队伍印记总层数 | — |
| `mark_stacks_opp` | `dict[str, int]` | 敌方印记明细 | — |
| `mark_count_both` | `int` | 双方印记总层数 | `= mark_count_own + mark_count_opp`；由 snapshot 构建时计算 |
| `skill_count_own` | `dict[str, int]` | 己方队伍携带各技能的精灵数 `{技能名: 数量}` | 需配合 `name` 参数索引 |
| `skill_element_counts_self` / `skill_element_counts_opp` | `dict[str, int]` | 携带各系别技能数 `{系别: 数量}` | 不在 ADDRESS_MAP，仅通过 RefExpr `self.skills[element=X].count` 访问；与 `skill_element_count_self`（不同系别总数）不同 |
| `team_counters_own` | `dict[str, int]` | 己方队伍计数器 `{key: count}` | — |
| `team_counters_opp` | `dict[str, int]` | 敌方队伍计数器 | — |
| `team_elements_own` | `frozenset` | 己方队伍所有精灵的系别集合 | 用于 `team_has_element` 条件 |
| `team_elements_opp` | `frozenset` | 敌方队伍系别集合 | — |
| `devotion_own` | `dict[str, int]` | 己方奉献池 `{名称: 层数}` | 需配合 `name` 参数索引 |
| `devotion_opp` | `dict[str, int]` | 敌方奉献池 | — |
| `abnormal_stacks_battle` | `dict[str, int]` | 双方全场异常总层数 | 跨双方求和 |
| `fainted_own` | `int` | 己方力竭数 | — |
| `fainted_opp` | `int` | 敌方力竭数 | — |
| `lives_own` | `int` | 己方魔力值 | 默认 5 |
| `lives_opp` | `int` | 敌方魔力值 | 默认 5 |
| `burst_triggered_count_own` | `int` | 己方队伍已触发的迸发种类数 | — |
| `moe_team_stacks` | `int` | 己方队伍萌化总层数（不含自身） | — |

#### 技能（当前发动的技能）

| 字段 | 类型 | 说明 | 与相似字段的区别 |
|------|------|------|-----------------|
| `power_self` | `int` | 技能基础威力 | 不含任何修正；修正后的威力由引擎计算 |
| `adjacent_power_sum` | `int` | 两侧相邻技能威力之和 | — |
| `power_opp` | `int` | 对方当前技能基础威力 | — |
| `skill_type_self` | `str` | 本技能类型 | `"物攻"` / `"魔攻"` / `"动态攻击"` / `"防御"` / `"状态"` |
| `skill_type_opp` | `str` | 对方技能类型 | — |
| `element_self` | `str` | 本技能系别 | — |
| `element_opp` | `str` | 对方技能系别 | — |
| `element_advantage` | `float` | 属性克制系数 | `0.5`=抵抗, `1.0`=普通, `2.0`=克制 |
| `skill_tag_self` | `str` | 技能标签 | 如 `"迅捷"`、`"传动"` |
| `combo_self` | `int` | 当前连击数 | — |
| `energy_cost_self` | `int` | 当前技能能耗 | 已应用所有修正后的最终值 |
| `energy_cost_reduction_self` | `int` | 累计能耗减少量 | `= base - current`，≥0 |
| `energy_cost_opp` | `int` | 对方技能总能耗 | — |
| `skill_name_self` | `str` | 当前技能名称 | — |
| `prev_skill_type` | `str` | 上次技能类型 | 用于 `prev_skill_is` 条件 |
| `prev_damage_taken_self` | `bool` | 己方上回合是否受伤 | — |

#### 战场

| 字段 | 类型 | 说明 |
|------|------|------|
| `weather` | `str` | 当前天气 |
| `turn` | `int` | 当前回合数 |
| `is_first` | `bool` | 本技能是否为本回合第一个行动 |
| `skill_index` | `int` | 技能在列表中的位置 (0-indexed) |

#### 计次器

| 字段 | 类型 | 说明 |
|------|------|------|
| `counter_values` | `dict[str, int]` | 命名计次器当前值 `{name: count}` |

### 1.3 ADDRESS_MAP — 寄存器寻址

- **实现**: `backend/vm/ctx.py:ADDRESS_MAP` (254-366行)
- **校验**: `backend/vm/ctx.py:_validate_address_map()` — 模块导入时自动运行
- **使用**: `backend/vm/resolve.py:resolve()` 通过 `ADDRESS_MAP[(of, q)]` 进行 O(1) 字段查找

`of` 合法值：`sprite_self`, `sprite_opp`, `team_own`, `team_opp`, `team_both`, `skill_off_0`, `skill_opp_current`, `battle`

完整映射见 `backend/vm/ctx.py`。以下为按 `of` 分组的可查询字段：

| of | 可查询的 q |
|----|-----------|
| `sprite_self` | `hp`, `hp_ratio`, `hp_max`, `energy`, `energy_cost`, `skills_energy_sum`, `abnormal_count`, `abnormal_stacks`, `times_entered`, `times_left`, `elements_used_count`, `positive_count`, `zero_cost_skill_count`, `priority`, `atk`, `def`, `sp_atk`, `sp_def`, `speed`, `adjacent_power_sum`, `damage_reduced`, `damage_reduction`, `last_tick_damage`, `charged`, `is_charging`, `first_action`, `first_action_battle`, `bloodline`, `elements`, `element_advantage`, `energy_cost_sum`, `power_mult`, `damage_mult`, `energy_cost_mult`, `combo_mult`, `life_drain`, `mark_bonus`, `energy_delta`, `heal_delta`, `lives` |
| `sprite_opp` | `hp`, `hp_ratio`, `hp_max`, `energy`, `energy_cost`, `abnormal_count`, `abnormal_stacks`, `positive_count`, `last_tick_damage`, `atk`, `def`, `sp_atk`, `sp_def`, `speed`, `charged`, `damage_reduction`, `skills_energy_sum`, `power_mult`, `damage_mult`, `bloodline`, `elements`, `is_charging`, `heal_delta`, `lives` |
| `team_own` | `mark_count`, `mark_stacks`, `skill_count`, `team_counter`, `devotion`, `fainted`, `burst_triggered_count`, `lives`, `elements`, `moe_stacks` |
| `team_opp` | `mark_count`, `mark_stacks`, `team_counter`, `devotion`, `fainted`, `lives`, `elements` |
| `team_both` | `mark_count` (双方合计) |
| `battle` | `abnormal_stacks` (双方全场), `weather` |
| `skill_off_0` | `power_base`, `element`, `adjacent_power_sum`, `combo_current`, `energy_cost`, `counter_value`, `energy_cost_reduction` |
| `skill_opp_current` | `power_base`, `element`, `energy_total` |

> **派生查询（不存储在 Ctx 中）**: `hp_missing_ratio` (= `1.0 - hp_ratio`) 和 `is_fainted` 由 `resolve.py:_resolve_dict_query()` 动态计算。

---

## 二、值表达式 — `value`

所有需要数值的地方统一为值表达式。实现：`backend/vm/resolve.py:resolve()`

### Literal — 字面量

直接传递，不做转换。支持 `int`, `float`, `str`, `bool`。

### Query — 寄存器查询

- **格式**: `{ "q": "<query>", "of": "<source>", ... }`
- **实现**: `backend/vm/resolve.py:_resolve_dict_query()` — ADDRESS_MAP 查找 → `getattr()` → 子索引（dict 型寄存器）→ 变换链

**变换链**（按顺序应用）：`per` → `scale` → `offset`

| 修饰 | 说明 |
|------|------|
| `scale` | 乘以系数 |
| `offset` | 加上偏移 |
| `per` | 整除（每 N 算 1 步） |
| `default` | 回退值（raw 为 0/""/None 时使用） |

**需 `name` 参数的 dict 型查询**（定义于 `_NAMED_DICT_QUERIES`）：`counter_value`, `abnormal_stacks`, `devotion`, `mark_stacks`, `skill_count`, `team_counter`

**需 `skill_type`/`element`/`tag` 参数的查询**：`energy_cost_sum`

### RefExpr — 路径表达式

- **格式**: `"=@path.field"` (特性 JSON 中用 `=@` 前缀)
- **实现**: `backend/vm/resolve.py:_resolve_formula_string()` — 单引用走 `_resolve_trait_ref()`，算术表达式走 eval
- **编译**: `TraitToObserver` 编译器在加载时将 `=@` 表达式编译为 `RefExpr(root, path, multiplier, offset)`
- **与 Query 的区别**: RefExpr 支持点路径访问（如 `self.effects[name=灼烧].stacks`）和算术表达式（`@a - @b`），Query 只支持单次 ADDRESS_MAP 查表
- **支持的路径前缀**: `self.*`, `target.*`, `skill.*`, `opponent_skill.*`, `player_*`, `opponent_*`, `battle.globals.*`

---

## 三、指令集

> 每条指令产生一种 mutation 类型。实现：`backend/vm/executor.py:process_one()` 根据 `op` 分发到 `backend/vm/ops/*.py`。

### `target` 合法值

| 值 | 含义 |
|---|------|
| `sprite_self` | 当前在场己方精灵 |
| `sprite_opp` | 当前在场敌方精灵 |
| `team_own` | 己方队伍 |
| `team_opp` | 敌方队伍 |
| `team_both` | 双方队伍 |
| `team_own_benched` | 己方场下全体精灵 |
| `team_opp_benched` | 敌方场下全体精灵 |
| `skill_off_0` | 当前使用的技能 |
| `skill_at_1` ~ `skill_at_4` | N 号位技能 |
| `skill_opp_current` | 对方当前技能 |
| `battle` | 全局战场 |

### 3A. 寄存器修改类

#### `stat_stage` — 属性阶段修正

修改精灵的 atk/def/sp_atk/sp_def/speed 阶段值。

- **实现**: `backend/vm/ops/mod.py:op_stat_stage()`
- **Mutation**: `StatChange`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `stat` | `"atk"` / `"def"` / `"sp_atk"` / `"sp_def"` / `"speed"` | 目标属性 |
| `steps` | `int` / Query / RefExpr | 阶段变化量（正=增益，负=减益） |
| `scope` | `str` | 生命周期，默认 `"battlefield"` |
| `source` | `str` | 效果来源（追踪/驱散用） |

- **与 `power_mod` 的区别**: `stat_stage` 修改精灵属性阶段（攻防速），每个 stage +10%；`power_mod` 修改技能属性（威力/能耗/连击/优先级），用 delta 加法
- **与 `mult_mod` 的区别**: `stat_stage` 是阶段累加，`mult_mod` 是直接倍率修正（如 `value: 2.0` 表示翻倍）

#### `power_mod` — 技能属性修正

修改技能的 power / energy_cost / combo / priority 等属性。

- **实现**: `backend/vm/ops/mod.py:op_power_mod()`
- **Mutation**: `SkillMod`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标技能或精灵 |
| `attr` | `"power"` / `"energy_cost"` / `"combo"` / `"priority"` / `"energy_cost_mult"` / `"combo_mult"` / `"energy_cost_delta_mult"` | 目标属性 |
| `delta` | `int` / Query / RefExpr | 变化量 |
| `skill_where` | `dict` | 技能筛选条件（`{"q": "energy_cost", "op": "gt", "value": 3}`） |
| `skill_filter` | `str` | 批量技能筛选：`"attack"` / `"defense"` / `"status"` / `"all"` / `"others"` / `"adjacent"` / `"bare_attack"` / `"bare_defense"` / `"bare_status"` |
| `name` | `str` | 按技能名精确筛选 |
| `element` | `str` | 按系别筛选；`"each"` 表示每种系别各取至多 `per_element` 个 |
| `per_element` | `int` | 配合 `element: "each"`，每种系别数量上限 |
| `scope` | `str` | 生命周期 |

- **与 `stat_stage` 的区别**: 见上
- **与 `mult_mod` 的区别**: `power_mod` 是加法修改（`delta`），`mult_mod` 是乘法修改（`value`）

#### `mult_mod` — 倍率修正

修改伤害/威力倍率。

- **实现**: `backend/vm/ops/mod.py:op_mult_mod()`
- **Mutation**: `MultiplierMod`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `attr` | `"power_mult"` / `"damage_mult"` / `"damage_reduction"` / `"life_drain"` | 目标倍率 |
| `value` | `float` / Query | 倍率值（1.0=不变） |
| `mode` | `"add"` (默认) / `"set"` | `"add"`=累加，`"set"`=直接设置 |

- **与 `power_mod` 的区别**: `mult_mod` 是乘法倍率（`value`），`power_mod` 是加法增量（`delta`）
- **与 `stat_stage` 的区别**: `mult_mod` 直接修改最终倍率，`stat_stage` 修改属性阶段（间接影响）

#### `flag_set` — 布尔标记

设置/清除 boolean 标记。

- **实现**: `backend/vm/ops/mod.py:op_flag_set()`
- **Mutation**: `FlagSet`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `flag` | `str` | 标记名（见下表） |
| `value` | `bool` | 开关 |
| `name` | `str` | 配合 `immune` 标记，指定免疫的异常名 |

**flag 合法值**：

| flag | 含义 |
|------|------|
| `immune` | 免疫指定异常（需 `name`） |
| `freeze_immune` | 免疫冻结 |
| `survive` | 锁血不死 |
| `charged` / `pre_charged` | 蓄力/预蓄力 |
| `drive` | 传动 |
| `swift` | 迅捷 |
| `extra_action` / `extra_turn_end` | 额外行动/回合末 |
| `heal_reverse` | 治疗反转 |
| `life_as_energy` | 生命代替能量 |
| `ignore_mods` / `ignore_resistance` | 忽略修正/抵抗 |
| `cooldown` | 冷却中 |
| `no_self_damage` | 免疫自伤 |
| `tick_reduce` | 回合末 tick 减次 |
| `abnormal_tick_invert` | 异常 tick 方向逆转 |
| `unlimited_abnormal` | 异常层数不限 |
| `charge_any_skill` | 蓄力中可用任意技能 |
| `usable_while_charging` | 蓄力中可用 |

- **与 `stat_stage` / `mult_mod` 的区别**: `flag_set` 是布尔开关，不涉及数值，改变的是游戏规则行为

#### `heal` — HP 操作

回复或扣除 HP。

- **实现**: `backend/vm/ops/mod.py:op_heal()`
- **Mutation**: `Heal`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `ratio` | `float` | HP 比例（0.5=半血） |
| `value` | `int` / Query | 固定数值（负=伤害） |

- **与 `energize` 的区别**: `heal` 修改 HP，`energize` 修改能量

#### `energize` — 能量操作

回复或扣除能量。

- **实现**: `backend/vm/ops/mod.py:op_energize()`
- **Mutation**: `EnergyChange`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `delta` | `int` / Query | 变化量（正=回复，负=扣除） |

#### `revive` — 复活

复活力竭精灵。引擎处理：`hp = max(1, max_hp * hp_ratio)`，清除力竭标记，同队当前位力竭时自动上场。

- **实现**: `backend/vm/ops/mod.py:op_revive()`
- **与 `heal` 的区别**: `revive` 将力竭精灵复活并恢复 HP，`heal` 仅回复在场精灵的 HP，不对已力竭精灵生效

### 3B. 状态效果类

#### `mark` — 印记

- **实现**: `backend/vm/ops/mark.py:op_mark()`
- **与 `abnormal` 的区别**: `mark` 施加在队伍上（`team_own`/`team_opp`），印记是队伍级效果；`abnormal` 施加在精灵上（`sprite_self`/`sprite_opp`），异常是精灵级效果

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `team_own` / `team_opp` | 目标队伍 |
| `name` | `str` | 印记名称 |
| `stacks` | `int` / Query | 层数 |

#### `abnormal` — 异常

- **实现**: `backend/vm/ops/abnormal.py:op_abnormal()`
- **与 `mark` 的区别**: 见上

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `sprite_self` / `sprite_opp` | 目标精灵 |
| `name` | `str` | 异常名称（如 "中毒"、"灼烧"） |
| `stacks` | `int` / Query | 层数 |

#### `weather` — 天气

- **实现**: `backend/vm/ops/weather.py:op_weather()`

| 字段 | 类型 | 说明 |
|------|------|------|
| `weather` | `str` | 天气名称 |
| `turns` | `int` | 持续回合数 |

#### `dispel` — 驱散

- **实现**: `backend/vm/ops/dispel.py:op_dispel()`
- **与 `steal` 的区别**: `dispel` 直接移除目标效果，`steal` 将目标效果转移到己方

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `what` | `"positive"` / `"negative"` / `"mark"` / `"abnormal"` | 驱散类型 |
| `name` | `str` | 指定具体 mark/abnormal 名称；不填=全部 |
| `limit` | `int` | 驱散层数上限 |
| `type_limit` | `int` | 驱散种类上限 |

#### `steal` — 偷取

- **实现**: `backend/vm/ops/steal.py:op_steal()`
- **与 `dispel` 的区别**: 见上

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 偷取来源 |
| `what` | `"positive"` / `"mark"` / `"energy"` | 偷取类型 |
| `name` | `str` | 指定名称；不填=全部 |
| `amount` | `int` | 偷取量（仅 `what: "energy"`） |

#### `tick` — 异常结算

触发一次指定异常的伤害结算。

- **实现**: `backend/vm/ops/tick.py:op_tick()`
- **与 `abnormal` 的区别**: `abnormal` 是施加异常，`tick` 是立即结算一次异常伤害（不改变异常层数，除非异常类型规定消耗）

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `name` | `str` | 异常名称 |

#### `double` — 翻倍

将指定类型效果的层数/步数 ×2。

- **实现**: `backend/vm/ops/double.py:op_double()`
- **与 `mult_mod` 的区别**: `double` 翻倍的是效果层数（如增益层数、异常层数），`mult_mod` 修改的是数值倍率（威力倍率、伤害倍率）

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `what` | `"positive"` / `"negative"` / `"abnormal"` / `"mark"` | 翻倍类型 |
| `name` | `str` | 指定具体 abnormal/mark 名称 |

### 3C. 战斗流控类

#### `hit` — 独立伤害

独立造成一次伤害，不依赖技能自身的 `power`/`skill_type`。

- **实现**: `backend/vm/ops/hit.py:op_hit()`
- **与技能隐式伤害的区别**: `hit` 在技能效果中显式声明额外伤害，有独立的 power/type/element

| 字段 | 类型 | 说明 |
|------|------|------|
| `power` | `int` | 基础威力 |
| `type` | `"物攻"` / `"魔攻"` | 伤害类型 |
| `element` | `str` | 系别（默认继承技能 element） |

#### `charge` — 蓄力

- **实现**: `backend/vm/ops/charge.py:op_charge()`

#### `escape` — 换宠

- **实现**: `backend/vm/ops/escape.py:op_escape()`
- **与 `return` 的区别**: `escape` 是换另一个精灵上场，`return` 是同一个精灵脱出再上场

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `sprite_self` / `sprite_opp` | `sprite_self`=自己脱离，`sprite_opp`=强制敌方脱离 |
| `inherit` | `bool` | 下个入场精灵继承增益 |
| `urgent` | `bool` | 紧急脱离：提到伤害之前执行 |

#### `return` — 返场

回合结束时离开战场并重新入场（同一个精灵）。

- **实现**: `backend/vm/ops/return_.py:op_return()`
- **与 `escape` 的区别**: 见上

#### `lock` — 锁定

禁止换宠。

- **实现**: `backend/vm/ops/lock.py:op_lock()`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `turns` | `int` | 持续回合数 |

#### `interrupt` — 打断

立即终止敌方当前技能的剩余效果执行。

- **实现**: `backend/vm/ops/interrupt.py:op_interrupt()`

#### `exchange` — 交换

- **实现**: `backend/vm/ops/exchange.py:op_exchange()`

| `what` 值 | 说明 |
|-----------|------|
| `"hp_ratio"` | 交换生命比例 |
| `"effects"` | 交换增益减益 |
| `"skills"` | 交换技能 |
| `"adjacent_skills"` | 交换当前技能两侧技能位置 |

#### `reset` — 重置

消除永久增量，将指定 stat 还原到基础值。

- **实现**: `backend/vm/ops/reset.py:op_reset()`
- **与 `dispel` 的区别**: `reset` 重置技能属性（如能耗），`dispel` 移除效果（增益/减益/印记/异常）

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标技能 |
| `stat` | `str` | 要重置的属性（如 `"energy_cost"`） |

#### `redirect` — 重定向

将本技能伤害目标重定向到指定对象。

- **实现**: `backend/vm/ops/redirect.py:op_redirect()`

#### `replay` — 重放历史技能

从精灵技能使用历史中筛选并重放技能。

- **实现**: `backend/vm/ops/replay.py:op_replay()`
- **与 `borrow` 的区别**: `replay` 重放自己或队伍的历史技能，`borrow` 复制对方当前技能的属性来替代本技能

| 字段 | 类型 | 说明 |
|------|------|------|
| `from` | `"sprite_self"` / `"team_burst"` | 技能来源 |
| `skill_filter` | `dict` | 筛选条件（`tag` / `skill_type` / `element`） |

#### `borrow` — 借用技能

复制目标技能的全部属性（威力、技能类型、effects 等）来替代本技能。

- **实现**: `backend/vm/ops/borrow.py:op_borrow()`
- **与 `replay` 的区别**: 见上

| 字段 | 类型 | 说明 |
|------|------|------|
| `from` | `"skill_opp_current"` | 借用来源 |

### 3D. 持久化/复合类

#### `observer` — 注册持久化条件→动作绑定

Observer 是 IR 第一公民，显式声明触发条件、可选计数器和生命周期。替代旧 `count`。

- **编译**: `backend/vm/compiler/trait_to_observer.py:TraitToObserver.compile()` — JSON → Observer
- **执行**: `backend/engine/battle.py` — 事件触发 → `ObserverRegistry.fire()` → Skill VM 执行 `then[]`
- **与普通 effect 的区别**: 普通 effect 在当前技能执行时一次性运行；`observer` 注册持久化监听器，跨回合触发

| 字段 | 类型 | 说明 |
|------|------|------|
| `cond` | `Condition` | 触发条件 |
| `then` | `[RiscIROp]` | 命中时执行的 IR（与技能 effects[] 相同格式） |
| `listen` | `str` | 触发点（编译器从 cond 推断，见 `backend/vm/cond.py:infer_triggers()`） |
| `counter` | `{name, threshold, reset}` | 可选计数器 |
| `scope` | `str` | `"battlefield"` / `"persistent"` / `"permanent"`；observer 缺省为 `"persistent"`（见 `trait_to_observer.py:_compile_one`） |

#### `defer` — 延迟执行

声明"N 回合后执行"。替代旧 `schedule`。

- **实现**: `backend/vm/ops/schedule.py:op_schedule()`
- **与 `observer` 的区别**: `defer` 是一次性延迟执行，`observer` 是持久化条件监听（可多次触发）

| 字段 | 类型 | 说明 |
|------|------|------|
| `turns` | `int` | 延迟回合数 |
| `at` | `"turn_start"` / `"turn_end"` | 执行时机 |
| `then` | `[RiscIROp]` | 到期时执行的 IR |

#### `inherit` — 效果继承

离场时将效果传递给入场精灵。替代旧 `inherit_effects`。

- **实现**: `backend/vm/ops/inherit_effects.py:op_inherit_effects()`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `str` | 继承目标（解析为 `inherit_target`，默认 `"enemy_new"`） |
| `effects` | `[RiscIROp]` | 要传递的效果列表 |

#### 其他持久化 opcode

| opcode | 实现 | 说明 |
|--------|------|------|
| `transform` | `backend/vm/ops/transform.py:op_transform()` | 形态变换（`species` + 可选 `skills`） |
| `team_counter` | `backend/vm/ops/team_counter_write.py:op_team_counter_write()` | 队伍计数器写入（`key`, `delta`, `target_team`） |
| `lives` | `backend/vm/ops/lives_change.py:op_lives_change()` | 队伍魔力值增减（`delta`, `target_team`） |
| `trait_interaction` | `backend/vm/ops/trait_interaction.py:op_trait_interaction()` | 特性交互（`action: "suppress"`） |
| `count` | `backend/vm/ops/count.py:op_count()` | 旧版计次（编译器转换为 observer） |
| `schedule` | `backend/vm/ops/schedule.py:op_schedule()` | 旧版延时（编译器转换为 defer） |
| `burst_grant` | `backend/vm/ops/burst_grant.py:op_burst_grant()` | 迸发注入 |
| `gain_skills` | `backend/vm/ops/gain_skills.py:op_gain_skills()` | 获得技能 |
| `effect_delta` | `backend/vm/ops/effect_delta.py:op_effect_delta()` | 效果层数增量 |

---

## 四、控制流 — `when`

条件分支。编译器将 `when` 转换为内部 `WhenBlock`，语义完全相同。

- **实现**: `backend/vm/executor.py` — `WhenBlock` 的 match/case 分发
- **条件评估**: `backend/vm/cond.py:eval_one()`

```jsonc
// if-else
{ "when": { "cond": "<condition>" },
  "then": [ /* effects */ ],
  "else": [ /* effects */ ] }

// if-elseif-else
{ "when": { "cond": "..." },
  "then": [ /* effects */ ],
  "else_if": [
    { "when": { "cond": "..." }, "then": [ /* effects */ ] }
  ],
  "else": [ /* effects */ ] }
```

- **与 `observer` 的区别**: `when` 是技能效果内的即时条件分支（一次性），在技能执行时求值一次。`observer` 是持久化条件监听器（跨回合），在每次注册事件发生时求值。

---

## 五、条件系统

- **实现**: `backend/vm/cond.py:COND_EVAL` — 每个条件是一个纯函数 `(ctx, cond) -> bool`
- **逻辑组合**: `and`/`or`/`not` 递归调用 `eval_one()`
- **二级 dispatch**: `have` 条件 → `HAVE_EVAL` 子表（`backend/vm/cond.py:257-279`）

### 条件列表

#### 应对/响应类

| cond | 参数 | 访问路径 | 说明 |
|------|------|----------|------|
| `counter_succeeded` | — | `ctx.event.counter_succeeded` | 本次应对成功 |
| `self_was_countered` | — | `ctx.event.was_countered` | 本次被应对 |
| `prev_counter_succeeded` | — | `ctx.event.prev_counter_succeeded` | 上次行动应对成功 |

- **`counter_succeeded` vs `prev_counter_succeeded`**: 前者是当前技能的应对结果（瞬时），后者是上回合/上次行动的应对结果

#### 蓄力/行动状态

| cond | 参数 | 访问路径 | 说明 |
|------|------|----------|------|
| `charged` | — | `ctx.charged_self` | 蓄力已完成 |
| `is_charging` | — | `ctx.is_charging_self` | 正在蓄力中 |
| `burst` | — | `ctx.first_action_self` | 迸发（首次行动） |
| `first_action` | — | `ctx.first_action_self` | 本场战斗首次行动 |
| `first_action_battle` | — | `ctx.first_action_battle_self` | 本回合首次行动 |

- **`charged` vs `is_charging`**: `charged`=蓄力完成可释放，`is_charging`=正在蓄力过程中（互斥状态）
- **`first_action` vs `first_action_battle`**: 前者是本场战斗首次（永久一次），后者是本回合首次（每回合重置）

#### KO / 伤害

| cond | 参数 | 访问路径 |
|------|------|----------|
| `on_ko` | — | `ctx.event.target_fainted` |
| `on_self_ko` | — | `ctx.event.self_koed` |
| `on_damage_taken` | `of` | `ctx.damage_taken_this_turn > 0` |
| `damage_restraint` | — | `ctx.element_advantage >= 2.0` |
| `prev_damage_taken` | `of` | `ctx.prev_damage_taken_self/opp` |

- **`on_ko` vs `on_self_ko`**: `on_ko`=我方击杀了对方，`on_self_ko`=我方被击杀

#### 切换

| cond | 参数 | 访问路径 |
|------|------|----------|
| `opp_switched` | — | `ctx.event.opp_switched` |
| `self_switched` | — | `ctx.event.self_switched` |
| `sprite_left` | `of` | `ctx.event.self_switched` / `ctx.event.opp_switched` |

- **`opp_switched` vs `self_switched` vs `sprite_left`**: 前两者是具体方向的事件标志，`sprite_left` 通过 `of` 参数统一两个方向

#### 技能类型检查

| cond | 参数 | 访问路径 |
|------|------|----------|
| `opp_is_attack` | — | `ctx.skill_type_opp in ("物攻", "魔攻", "动态攻击")` |
| `prev_skill_is` | `what` / `skill_type` | `ctx.prev_skill_type` |

#### 回合顺序

| cond | 参数 | 访问路径 |
|------|------|----------|
| `is_first` | — | `ctx.is_first` |
| `is_second` | — | `not ctx.is_first` |

#### HP / 能量阈值

| cond | 参数 | 访问路径 |
|------|------|----------|
| `hp_below` | `ratio`, `of` | `hp_ratio < ratio` |
| `energy_le` | `value`, `of` | `energy <= value` |
| `energy_eq` | `value`, `of` | `energy == value` |
| `energy_depleted` | `of` | `energy == energy_cost_self` |

- **`energy_le` vs `energy_eq` vs `energy_depleted`**: `energy_le`=低于或等于阈值，`energy_eq`=精确等于，`energy_depleted`=能量恰好等于当前技能能耗（即刚好用完）

#### 天气

| cond | 参数 | 访问路径 |
|------|------|----------|
| `weather_is` | `weather` | `ctx.weather` |

#### 技能位置

| cond | 参数 | 访问路径 |
|------|------|----------|
| `skill_at` | `position` | `ctx.skill_index` |
| `skill_position_changed` | — | `ctx.event.skill_position_changed` |

#### 技能使用/元素

| cond | 参数 | 访问路径 | 说明 |
|------|------|----------|------|
| `skill_use` | `element`, `skill_type`, `tag`, `energy_cost` | `_skill_use_matches()` | 仅 count/observer 用 |
| `have_skill_of` | `of`, `element` | `skill_elements` | 是否拥有指定系别的技能 |

#### 入场/行动/状态变化事件

| cond | 参数 | 访问路径 |
|------|------|----------|
| `sprite_entered` | `of` | `just_entered` |
| `sprite_acted` | `of` | `just_acted_self` |
| `on_abnormal_tick` | `of`, `name` | `ctx.event.last_tick_*` |
| `on_abnormal_changed` | `of`, `name` | `ctx.event.abnormal_changed_*` |
| `on_abnormal_applied` | `of`, `name` | `ctx.event.abnormal_applied_*` |
| `on_skills_energy_changed` | `of` | `ctx.event.skills_energy_changed_of` |
| `on_positive_changed` | `of` | `ctx.event.positive_changed_of` |
| `on_energy_changed` | `of` | `ctx.event.energy_changed_of` |
| `on_heal` | `of` | `ctx.event.heal_of` |

- **`on_abnormal_tick` vs `on_abnormal_changed` vs `on_abnormal_applied`**: `tick`=异常回合末结算伤害时，`changed`=异常层数变化时（增减），`applied`=主动施加异常时

#### 回合边界

| cond | 参数 | 访问路径 |
|------|------|----------|
| `turn_end` | — | `ctx.event.turn_end` |
| `turn_start` | — | 永远为 `True` |
| `always` | — | 永远为 `True` |

#### 泛用比较

| cond | 参数 | 访问路径 |
|------|------|----------|
| `compare` | `q`, `of`, `op`, `value` | `compare_op(resolve(ctx, cond), op, resolve(value))` |

#### 其他

| cond | 参数 | 访问路径 |
|------|------|----------|
| `devotion_triggered` | — | `ctx.event.devotion_triggered` |
| `team_has_element` | `element` | `ctx.team_elements_own` |
| `have` | `what`, `of`, `name` | `HAVE_EVAL` 子表（见下） |
| `trait_path` | `path`, `op`, `value` | `_eval_trait_path()` |

**`have` 的 `what` 合法值**（定义于 `backend/vm/cond.py:HAVE_EVAL` 257-279 行）：

| what | 额外参数 | 说明 |
|------|---------|------|
| `abnormal` | `of`, `name` | 目标精灵拥有指定异常（层数 > 0） |
| `mark` | `of`, `name` | 目标队伍拥有指定印记（层数 > 0；`of` 默认 `team_own`） |
| `stat_positive` | `of`, `stat` | 目标精灵指定 stat 阶段为正 |
| `stat_negative` | `of`, `stat` | 目标精灵指定 stat 阶段为负 |
| `any_stat_positive` | `of` | 目标精灵任一 stat 阶段为正 |
| `any_stat_negative` | `of` | 目标精灵任一 stat 阶段为负 |
| `counter` | `name` | 命名计次器当前值 > 0 |

#### 逻辑组合

| cond | 参数 | 说明 |
|------|------|------|
| `and` | `conditions: [...]` | 全部为真 |
| `or` | `conditions: [...]` | 任一为真 |
| `not` | `condition: {...}` | 取反 |

### 条件→触发点映射

`CONDITION_TRIGGERS`（`backend/vm/cond.py:148-202`）定义每个条件的触发点，用于自动推导 Observer 的 `listen` 字段。`infer_triggers()` 函数递归遍历条件树：
- `or`: 并集（任一子条件可能独立匹配）
- `and`: 并集（在所有子条件关注的触发点都检查）
- `not`: 内部条件的触发点（取反不改变触发时机）

---

## 六、Observer 模型

特性的 JSON 存储格式为 `effects[]`（少数旧特性使用 `triggers[]`），由 `TraitToObserver` 编译器在加载时转换为 Observer 对象。**运行时只有 Observer。**

- **编译入口**: `backend/engine/trait_loader.py:TraitLoader.load_for_sprite()`
- **注册**: `ObserverRegistry.register()` — Observer 按 `listen` 钩子点分组注册
- **触发**: `backend/engine/battle.py` — 事件发生时 fire 对应 hook → 遍历 Observer → cond 求值 → Skill VM 执行 `then[]`

**关键点**: `Observer.then` 就是 Skill IR opcode 数组，引擎执行时走同一条 `executor.py` 路径，不区分来源。

### 触发点 (hook)

| hook | 说明 |
|------|------|
| `post_entry` | 精灵入场 |
| `post_leave` | 精灵离场（Observer 注销） |
| `post_enemy_leave` | 敌方离场+新精灵入场（ctx 为我方视角） |
| `pre_calc` | 技能结算前（L0 修饰器注入） |
| `post_skill` | 技能执行后 |
| `post_damage` | 受到伤害后 |
| `post_switch` | 精灵切换后 |
| `post_ko` | 精灵力竭后 |
| `post_abnormal_tick` | 异常 tick 后 |
| `post_abnormal_change` | 异常层数变化后 |
| `post_abnormal_apply` | 异常施加后 |
| `post_energy_change` | 能量变化后 |
| `post_positive_change` | 增益数量变化后 |
| `post_heal` | 治疗后 |
| `turn_end` | 回合末结算 |
| `turn_start` | 回合开始 |

### Scope 与生命周期

| scope | 含义 | 离场 | 力竭 | 回合末 |
|------|------|:----:|:----:|:----:|
| `turn` | 仅当前回合有效 | 清除 | 清除 | 清除 |
| `battlefield` | 在场有效 | 清除 | 清除 | 保留 |
| `persistent` | 跨回合持久（受 ttl 控制） | 保留 | 清除 | ttl-1 |
| `permanent` | 永久 | 保留 | 保留 | 保留 |

### 引擎钩子（非 IR，引擎层拦截）

部分特性需要修改引擎行为，不走 Skill VM，通过 `register_hook()` 注册：

| hook 点 | 示例特性 |
|---------|---------|
| `max_energy_override` | 多人宿舍（10→15） |
| `before_apply_mark` | 吟游之弦（印记共存） |
| `before_consume_starfall` | 守望星（星陨消耗减半） |
| `on_energy_short` | 石头大餐（HP 代偿） |
| `on_fatal_damage` | 惊吓（免疫致死） |
| `post_counter` | 腾挪/保卫/好象坏象（裂口组变换） |
| `turn_end_bench_check` | 星地善良（自动上场） |
| `after_transmission` | 机械变式（传动后减能耗） |

---

## 七、执行时序 — `feeds` / `needs`

effect 声明"我往哪个池子放东西 / 我消费哪个池子的结果"。引擎拓扑排序。

- **实现**: `backend/vm/sort.py:sort_effects()`

| Token | `feeds` | `needs` |
|-------|---------|---------|
| `"cost"` | 修改能耗 → Gate 前 | — |
| `"power"` | 修改威力 → 威力确定前 | — |
| `"mult"` | 修改伤害倍率 → 伤害前 | — |
| `"result"` | — | 需要伤害结果 → 伤害后 |
| `"counter"` | — | 需要反击结束 → 最后 |
| `"turn_end"` | — | 回合末结算 |

```
feeds:cost → Gate(付能耗) → feeds:power → 威力确定 → feeds:mult
  → 伤害 → result 就绪
  → 默认(无声明) + needs:result
  → 反击 → counter 就绪 → needs:counter
  → 回合末 → needs:turn_end
```

---

## 八、通用字段

### Effect 级

| 字段 | 类型 | 含义 | 默认 | 适用 |
|------|------|------|------|------|
| `scope` | `str` | `"turn"` / `"battlefield"` / `"persistent"` / `"permanent"` | `"battlefield"` | 全部 |
| `source` | `str` | 效果来源名（追踪/驱散用） | — | 全部 |
| `ttl` | `int` | 存活回合数（仅 `persistent` 时生效） | 永久 | stat_stage/power_mod/flag_set |
| `per_hit` | `bool` | 每次连击触发 | `false` | mult_mod/flag_set |
| `feeds` | `str` | 拓扑排序 token | — | 技能 |
| `needs` | `str` | 拓扑排序 token | — | 技能 |
| `delay` | `int` | 延迟 N 回合生效 | `0` | 技能 |
| `cooldown` | `int` | 冷却（次） | `0` | 技能 |
| `on_next` | `bool` | 延迟到"下一次"生效 | `false` | power_mod |
| `if_type` | `str` | 配合 `on_next`，限定技能类型 | — | power_mod |
| `mode` | `str` | `"set"` (默认) / `"add"` / `"multiply"` | `"set"` | mult_mod/power_mod |

### 技能 body 字段

| 字段 | 含义 | 默认 |
|------|------|------|
| `element` | 系别（支持 Query） | `"普通"` |
| `tag` | 机制标签（`"迅捷"` / `"传动"`） | 无 |
| `use_devotion` | 触发队伍奉献 | `false` |
| `usable_while_charging` | 蓄力中可用 | `false` |
| `position_locked` | 不被交换移动 | `false` |
| `morph` | 变身 `{"from": "team_own", "mode": "random"}` | 无 |
| `passive` | 被动效果数组 | `[]` |
| `counter` | 应对类型：`"攻击"` / `"防御"` / `"状态"` | 无 |

---

## 九、RISC 原则：遵循与偏离

### 遵循 RISC 的设计

| 原则 | 实现 |
|------|------|
| **统一寄存器组** | Ctx 是回合级只读寄存器快照，所有指令通过 ADDRESS_MAP 统一寻址 |
| **纯函数执行** | `(Ctx, opcodes[]) -> Journal[Mutation]`，无副作用，确定性 |
| **统一 IR** | 技能 effects[] 和特性 Observer.then[] 编译为同一套 RISC opcode 数组 |
| **单级间接寻址** | ADDRESS_MAP 提供 O(1) `(of, q) → field_name` 查找，导入时自动校验 |
| **纯条件分发** | COND_EVAL 每个条件一个纯函数，通过 `ctx.event.X` 访问事件上下文 |
| **一条指令 = 一种 mutation** | `stat_stage`→StatChange, `heal`→Heal, `energize`→EnergyChange, `flag_set`→FlagSet |

### 偏离 RISC 的设计（及领域理由）

| 偏离 | 描述 | 理由 |
|------|------|------|
| `skill_where`/`skill_filter` 保留 | 批量技能筛选仍在指令内 | 拆为循环会导致 IR 膨胀；回合制技能数 ≤4 |
| `when` 允许嵌套 | 控制流可嵌套 | 回合制对战的 if-else 天然嵌套，强制基本块过度复杂 |
| `observer` 内嵌 counter | Observer 可选配计数器 | 阈值计数是回合制"基本原子"，拆分无收益 |
| 保留 `defer` | 延时执行仍为 IR 指令 | 延时是回合制核心机制，但简化为声明式 |
| 专用 opcode 存在 | `transform`/`lives`/`trait_interaction` 等 | 领域 VM 允许专用 opcode — 无法用通用 op 组合表达 |

这不是通用 CPU，而是**回合制对战游戏的领域 VM**。目标不是最小化指令数，而是：
1. 游戏策划的 JSON 描述能 1:1 映射到 IR
2. 编译器和运行时足够简单，可审计正确性
3. 单条 IR 指令对应游戏中一个可理解的操作

---

## 附录：真实数据示例

以下示例直接从 `data/skills/` 和 `data/traits/` 中复制，确保与当前实现一致。

### 技能示例

#### 嗜痛 (减伤 + 受伤时双攻提升)

来源: `data/skills/嗜痛.json`

```json
{
  "id": 10322,
  "name": "嗜痛",
  "element": "普通",
  "skill_type": "防御",
  "energy_cost": 2,
  "counter": "攻击",
  "effects": [
    { "target": "sprite_self", "op": "mult_mod", "attr": "damage_reduction", "value": 0.8 },
    { "when": { "cond": "on_damage_taken" },
      "then": [
        { "op": "stat_stage", "target": "sprite_self", "stat": "atk", "steps": 4 },
        { "op": "stat_stage", "target": "sprite_self", "stat": "sp_atk", "steps": 4 }
      ]
    }
  ]
}
```

**涉及 opcode**: `mult_mod` (减伤), `when` (条件分支), `stat_stage` (属性阶段)

#### 四维降解 (动态能耗)

来源: `data/skills/四维降解.json`

```json
{
  "id": 10155,
  "name": "四维降解",
  "element": "幻",
  "skill_type": "魔攻",
  "power": 100,
  "energy_cost": 7,
  "effects": [
    { "target": "skill_off_0", "op": "power_mod", "scope": "persistent",
      "attr": "energy_cost",
      "delta": { "q": "mark_count", "of": "team_opp", "scale": -1, "name": "any" }
    }
  ]
}
```

**涉及 opcode**: `power_mod` (技能属性修正), Query 动态值 (`mark_count` → 敌方印记数)

### 特性示例

#### 不移 (无条件永久修饰)

来源: `data/traits/不移.json`

```json
{
  "id": 20003,
  "name": "不移",
  "effects": [
    { "op": "mult_mod", "target": "sprite_self", "scope": "permanent",
      "skill_filter": "bare_attack", "attr": "power_mult", "value": 1.3 }
  ]
}
```

**涉及 opcode**: `mult_mod` (倍率修正), `skill_filter: "bare_attack"`, `scope: "permanent"`

#### 偏振 (Observer + 动态条件)

来源: `data/traits/偏振.json`

```json
{
  "id": 20010,
  "name": "偏振",
  "effects": [
    { "op": "observer",
      "cond": { "cond": "have_skill_of", "of": "sprite_opp",
        "element": { "q": "element", "of": "skill_off_0" } },
      "then": [
        { "op": "mult_mod", "target": "sprite_opp", "attr": "damage_reduction",
          "value": 0.4, "mode": "add" }
      ],
      "listen": "pre_calc", "scope": "battlefield"
    }
  ]
}
```

**涉及 opcode**: `observer` (持久化条件), `have_skill_of` + Query 嵌套, `mult_mod` mode=add

#### 仁心 (异常 tick 触发治疗)

来源: `data/traits/仁心.json`

```json
{
  "id": 20006,
  "name": "仁心",
  "effects": [
    { "op": "observer",
      "cond": { "cond": "on_abnormal_tick", "of": "sprite_opp", "name": "灼烧" },
      "then": [
        { "op": "heal", "target": "sprite_self",
          "value": { "q": "last_tick_damage", "of": "sprite_opp" } }
      ]
    }
  ]
}
```

**涉及 opcode**: `observer` + `on_abnormal_tick`, `heal` + Query (`last_tick_damage`)
