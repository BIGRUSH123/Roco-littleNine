# IR_GUIDE — 回合制战斗 IR 指令参考

> **基于寄存器的领域虚拟机**，为回合制对战游戏的技能/特性编译到统一指令集而设计。
>
> 技能和特性编译到同一套 IR opcode 指令集，差异仅在于**触发方式**和**生命周期**：
> - **技能**: `effects[]` → Skill VM 即时执行
> - **特性**: `TraitToObserver` 编译器将 JSON 转换为 `Observer { cond, then, scope }`，事件触发后由 Skill VM 执行 `then[]`
>
> 运行时只有一种执行路径：RISC VM。

> **唯一表示（2026-09-22）**：`sim` 层不再持有任何「效果对象」。旧版 kind 形式
> （`sim/effects.py` 的 `SpecialName` / `StatEffect` / `SpecialEffect` / `ConditionalEffect` +
> `Skill.effects` + `SkillUse._collect_modifiers`）已整层删除——数据面 0 用量，
> 持久化效果统一用 `backend/vm/effect.py` 的 `StatBuffEffect`/`AbnormalEffect`/`MarkEffect`/`StateEffect`。
> 规则层要按技能效果做决策，读 `sim/skill_ir.py`（IR 只读画像），不要再按技能 JSON 手写判据。

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
| `sprite_left_of` | `str` | 离场方（`sprite_self` / `sprite_opp`） |

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
| `elements_used_count_self` | `int` | 使用过的不同系别**技能**数 | 写入点与 `distinct_elem:<系别>` 同处：首次使用某系别技能时给该精灵记 `used_elem:<系别>` 计数（随精灵换人保留），快照按计数条数取值 |
| `just_entered` | `bool` | 本回合入场 | 用于 `sprite_entered` 条件；与 `times_entered_self`（累计次数）不同 |
| `just_acted_self` | `bool` | 刚使用过技能 | 用于 `sprite_acted` 条件 |
| `skill_elements_self` | `frozenset` | 携带技能的元素集合 | 用于 `have_skill_of` 条件 |
| `stat_stages_self` | `dict[str, int]` | 属性阶段 | 正=增益，负=减益；与 `atk_self` 等基础值分离存储 |
| `energy_cost_sum_self` | `dict[str, int]` | 技能能耗合计 `{type/element/tag: total}` | 按类别/系别/标签分组求和；查询时用 `skill_type`/`element`/`tag` 子索引 |
| `zero_cost_skill_count_self` | `int` | 携带的0能耗技能数量 | — |
| `power_mult_self` | `float` | 威力倍率修饰 | 默认 1.0；来自 VM modifier 注入 |
| `damage_mult_self` | `float` | 伤害倍率修饰 | 默认 1.0 |
| `energy_cost_mult_self` | `float` | 能耗倍率修饰 | — |
| `combo_mult_self` | `float` | 连击倍率修饰（**只对本技能带连击词条（基础 `combo`≥2）时生效**，见 §八「连击语义」） | — |
| `life_drain_self` | `float` | 吸血比例 | — |
| `mark_bonus_own` | `float` | 己方印记伤害加成 | — |
| `bloodline_self` | `str` | 己方血脉 | 如 "首领" |
| `is_mixed_blood_self` | `bool` | 己方是否**混血**精灵 | 判定见 §五 `is_mixed_blood`；与 `bloodline_self`（血脉名字）不同：这是派生的布尔判定 |
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
| `is_mixed_blood_opp` | `bool` | 敌方是否**混血**精灵 | 同 `is_mixed_blood_self` |
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
| `counters_self` | `dict[str, int]` | 己方精灵级计数器 `{key: n}` | 与 `team_counters_own`（队伍级）不同：生命周随精灵，换人后保留；需配合 `name` 参数索引 |
| `counters_opp` | `dict[str, int]` | 敌方精灵级计数器 | — |
| `last_turn_element_own` | `dict[str, int]` | **上一回合**己方队伍各系别技能的使用次数 `{系别: 次数}` | 与 `team_counters_own["element:<系别>"]`（**累计、从不清零**）不同：这是回合末的**覆盖写**快照，只描述上一回合 |
| `last_turn_element_opp` | `dict[str, int]` | **上一回合**敌方队伍各系别技能的使用次数 | 同族；`of` 取 `team_opp` 时读它 |
| `last_turn_element_both` | `dict[str, int]` | **上一回合**双方各系别技能的使用次数合计 | `= own + opp` 逐键求和（不是「各有」而「双方合计是否有」：任一方用过即 >0） |
| `last_turn_energy_sum_own` | `int` | **上一回合**己方队伍使用技能的能耗之和 | 覆盖写；按**实际支付的能耗**累计 |
| `last_turn_energy_sum_opp` | `int` | **上一回合**敌方队伍使用技能的能耗之和 | — |
| `last_turn_energy_sum_both` | `int` | **上一回合**双方使用技能的能耗之和 | 基因编辑「基础能耗变为上回合双方使用的技能能耗之和」读它 |

#### 技能（当前发动的技能）

| 字段 | 类型 | 说明 | 与相似字段的区别 |
|------|------|------|-----------------|
| `power_self` | `int` | 技能基础威力 | 不含任何修正；修正后的威力由引擎计算 |
| `adjacent_power_sum` | `int` | 两侧相邻技能威力之和 | 见 §1.2「两侧技能威力」口径；与 `skill_filter:"adjacent"` 同一份「相邻」定义 |
| `adjacent_power_diff` | `int` | 两侧相邻技能威力之差的**绝对值** | 与 `adjacent_power_sum` 同源（缺一侧按 0）；六自由度「威力 + 两侧技能威力差的四分之一」读它 |
| `power_opp` | `int` | 对方当前技能基础威力 | — |
| `skill_type_self` | `str` | 本技能类型 | `"物攻"` / `"魔攻"` / `"动态攻击"` / `"防御"` / `"状态"` |
| `skill_type_opp` | `str` | 对方技能类型 | — |
| `element_self` | `str` | 本技能系别 | — |
| `element_opp` | `str` | 对方技能系别 | — |
| `element_advantage` | `float` | 属性克制系数 | `0.5`=抵抗, `1.0`=普通, `2.0`=克制 |
| `skill_tag_self` | `str` | 技能标签 | 如 `"迅捷"`、`"传动"` |
| `combo_self` | `int` | 当前连击数（= 本技能**释放次数**；已含技能自身修正与（门控后的）精灵级连击增益） | — |
| `energy_cost_self` | `int` | 当前技能能耗 | 已应用所有修正后的最终值 |
| `energy_cost_reduction_self` | `int` | 累计能耗减少量 | `= base - current`，≥0 |
| `energy_cost_opp` | `int` | 对方技能总能耗 | — |
| `skill_name_self` | `str` | 当前技能名称 | — |
| `prev_skill_type` | `str` | 上次技能类型 | 用于 `prev_skill_is` 条件 |
| `prev_damage_taken_self` | `bool` | 己方上回合是否受伤 | — |

##### 体重寄存器与「两侧技能威力」口径

- `weight_self` / `weight_opp`（`float`，kg）：精灵体重，来自
  `data/sprites/_weights.json`（sidecar，由 `backend/tools/gen_sprite_weights.py`
  从线上 nrc `Catalog.lua` 生成）。**取值口径：区间取中点**（如 `"77~85.5KG"` → 81.25）——
  这是模拟口径，游戏内个体体重在区间内浮动，本引擎不建模个体浮动。
  name/form → Catalog `title` 的对应规则（含首领形态/外观变体）见生成脚本 docstring
  与 sidecar `_meta`；`Sprite.weight` 是唯一读取点，缺数据为 `0.0`。
- **派生查询 `weight_diff`**（不存储在 Ctx 中，由 `resolve.py` 计算）：
  `of: "sprite_self"`（默认）= `weight_self − weight_opp`（**带符号**）；
  `of: "sprite_opp"` = 反向。需要「差越大」语义时在数据侧取绝对值：
  `{"q": "weight_diff", "of": "sprite_self", "abs": true}`（`abs` 是 §二 变换链的一环）。
  用例：砂糖弹球「双方体重差越大，本次技能威力越高」的档位链。
- **两侧技能威力**（`adjacent_power_sum` / `adjacent_power_diff`）：取**当前技能槽位**的
  左右邻居（`sprite.skills` 列表索引相减为 1，**不环绕**；边缘槽位缺的一侧按 0），
  威力取邻居槽**当前生效**技能的 `power`（= 基础威力 + 技能级 `_modifiers["power"]`，
  与 `power_self` 同口径），与 `skill_filter:"adjacent"` 共用同一份「相邻」定义
  （`backend/engine/snapshot.py:adjacent_powers`）。用例：六自由度
  `{"op":"power_mod","target":"skill_off_0","attr":"power","delta":"=round(@adjacent_power_diff / 4)"}`。
  两条快照路径一致：Python `build_ctx` 直接计算，Cython `build_ctx_cy` 调同一 helper；
  `fill_extended_registers` 亦会补齐（Cython 产物未重编译时仍正确）。

#### 战场

| 字段 | 类型 | 说明 |
|------|------|------|
| `weather` | `str` | 当前天气 |
| `turn` | `int` | 当前回合数 |
| `is_first` | `bool` | 本技能是否为本回合第一个行动 |
| `is_night` | `bool` | 王国是否入夜（世界状态，由对局配置写入 `GlobalEffects.night`） |
| `skill_index` | `int` | 技能在列表中的位置 (0-indexed) |

#### 计次器

| 字段 | 类型 | 说明 |
|------|------|------|
| `counter_values` | `dict[str, int]` | 命名计次器当前值 `{name: count}` |

### 1.3 ADDRESS_MAP — 寄存器寻址

- **实现**: `backend/vm/ctx.py:ADDRESS_MAP`（255 行起，模块导入时自动校验）
- **校验**: `backend/vm/ctx.py:_validate_address_map()` — 模块导入时自动运行
- **使用**: `backend/vm/resolve.py:resolve()` 通过 `ADDRESS_MAP[(of, q)]` 进行 O(1) 字段查找

`of` 合法值：`sprite_self`, `sprite_opp`, `team_own`, `team_opp`, `team_both`, `skill_off_0`, `skill_opp_current`, `battle`

完整映射见 `backend/vm/ctx.py`。以下为按 `of` 分组的可查询字段：

| of | 可查询的 q |
|----|-----------|
| `sprite_self` | `hp`, `hp_ratio`, `hp_max`, `energy`, `energy_cost`, `skills_energy_sum`, `abnormal_count`, `abnormal_stacks`, `times_entered`, `times_left`, `elements_used_count`, `positive_count`, `zero_cost_skill_count`, `priority`, `atk`, `def`, `sp_atk`, `sp_def`, `speed`, `adjacent_power_sum`, `adjacent_power_diff`, `weight`, `damage_reduced`, `damage_reduction`, `last_tick_damage`, `charged`, `is_charging`, `first_action`, `first_action_battle`, `bloodline`, `is_mixed_blood`, `elements`, `element_advantage`, `energy_cost_sum`, `power_mult`, `damage_mult`, `energy_cost_mult`, `combo_mult`, `life_drain`, `mark_bonus`, `energy_delta`, `heal_delta`, `lives`, `counter` |
| `sprite_opp` | `hp`, `hp_ratio`, `hp_max`, `energy`, `energy_cost`, `abnormal_count`, `abnormal_stacks`, `positive_count`, `last_tick_damage`, `atk`, `def`, `sp_atk`, `sp_def`, `speed`, `charged`, `damage_reduction`, `skills_energy_sum`, `power_mult`, `damage_mult`, `bloodline`, `is_mixed_blood`, `elements`, `is_charging`, `heal_delta`, `lives`, `counter`, `weight` |
| `team_own` | `mark_count`, `mark_stacks`, `skill_count`, `team_counter`, `devotion`, `fainted`, `burst_triggered_count`, `lives`, `elements`, `moe_stacks`, `last_turn_element` (需 `name`), `last_turn_energy_sum` |
| `team_opp` | `mark_count`, `mark_stacks`, `team_counter`, `devotion`, `fainted`, `lives`, `elements`, `last_turn_element` (需 `name`), `last_turn_energy_sum` |
| `team_both` | `mark_count` (双方合计), `last_turn_element` (需 `name`, 双方合计), `last_turn_energy_sum` (双方合计) |
| `battle` | `abnormal_stacks` (双方全场), `weather`, `is_night` |
| `skill_off_0` | `power_base`, `element`, `adjacent_power_sum`, `combo_current`, `energy_cost`, `counter_value`, `energy_cost_reduction` |
| `skill_opp_current` | `power_base`, `element`, `energy_total` |

> **派生查询（不存储在 Ctx 中）**: `hp_missing_ratio` (= `1.0 - hp_ratio`)、`is_fainted` 由
> `resolve.py:_resolve_dict_query()` 动态计算；`mark_count_both` (= own + opp)、
> `element_count` (= `len(team_elements_own/_opp)`，队伍不同系别数) 由 `resolve.py` 的
> 派生分支计算（编译期映射到 `team_elements_*` 字段，`sub_key_field` 标记）。
> `element_count` 的 `of` 取 `team_own`（默认）/ `team_opp`，用例：分光「己方队伍中精灵
> 每有1个不同的系别，额外获得魔攻+10%」→ `steps: {"q":"element_count","of":"team_own","offset":2}`。
> `weight_diff` (= `weight_self − weight_opp`，带符号；`of: "sprite_opp"` 反向) 同族，
> 见 §1.2「体重寄存器」。

---

## 二、值表达式 — `value`

所有需要数值的地方统一为值表达式。实现：`backend/vm/resolve.py:resolve()`

### Literal — 字面量

直接传递，不做转换。支持 `int`, `float`, `str`, `bool`。

### Query — 寄存器查询

- **格式**: `{ "q": "<query>", "of": "<source>", ... }`
- **实现**: `backend/vm/resolve.py:_resolve_dict_query()` — ADDRESS_MAP 查找 → `getattr()` → 子索引（dict 型寄存器）→ 变换链

**变换链**（按顺序应用）：`abs` → `per` → `scale` → `offset`

| 修饰 | 说明 |
|------|------|
| `abs` | `true` 时取绝对值（在 `per`/`scale`/`offset` **之前**）；用于「差越大」类语义（如 `weight_diff`） |
| `scale` | 乘以系数 |
| `offset` | 加上偏移 |
| `per` | 整除（每 N 算 1 步） |
| `default` | 回退值（raw 为 0/""/None 时使用） |

**需 `name` 参数的 dict 型查询**（定义于 `_NAMED_DICT_QUERIES`）：`counter_value`, `abnormal_stacks`, `devotion`, `mark_stacks`, `skill_count`, `team_counter`, `counter`, `last_turn_element`

> `counter` 是**精灵级**计数器（`{"q":"counter","of":"sprite_self","name":"<key>"}`），随精灵换人保留；
> `team_counter` 是队伍级计数器。二者生命周期不同，写入口分别是 `counter` op 与 `team_counter` op。
>
> `last_turn_element` 读**上一回合**的系别使用明细（本回合不累加）：
> `{"q":"last_turn_element","of":"team_own","name":"水"}` = 上一回合己方队伍使用水系技能的次数；
> `of` 取 `team_both` 时是双方合计。缺省 `name` 时返回 0，配 `default` 可做兜底。

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

### `target` 合法值（`backend/vm/compiler/passes/skill_validate.py:VALID_TARGETS`）

| 值 | 含义 |
|---|------|
| `sprite_self` | 当前在场己方精灵 |
| `sprite_opp` | 当前在场敌方精灵 |
| `team_own` / `own_team` | 己方队伍（后者为别名） |
| `team_opp` / `opp_team` | 敌方队伍（后者为别名） |
| `team_both` | 双方队伍 |
| `team_own_benched` | 己方场下全体精灵 |
| `team_own_all` / `team_opp_all` | **全队 6 只**（含替补与**力竭**者）——当前仅 `energize` 消费（`replayer._apply_energy_change`），小型打劫「敌方队伍中所有精灵失去1能量」用它；与 `team_own`（跳过力竭）/`team_own_benched`（保证跳过在场）的区别就在这里。其余 op 收到这两个值时走 `_target_sprite` 兜底（= 在场精灵），与 `team_own`/`team_opp` 在那些 op 上的既有行为一致 |
| `team_burst` | 迸发技能来源集合（`replay from` 用） |
| `skill_off_0` | 当前使用的技能 |
| `skill_opp_current` | 对方当前技能 |
| `battle` | 全局战场 |

> 敌方场下全体（`team_opp_benched`）与 `skill_at_1~4` 定位目标**未实现**，不可使用。

### 3A. 寄存器修改类

#### `stat_stage` — 属性阶段修正

修改精灵的 atk/def/sp_atk/sp_def/speed 阶段值。

- **实现**: `backend/vm/ops/mod.py:op_stat_stage()`
- **Mutation**: `StatChange`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `stat` | `"atk"` / `"def"` / `"sp_atk"` / `"sp_def"` / `"speed"` / `"speed_flat"` | 目标属性 |
| `steps` | `int` / Query / RefExpr | 阶段变化量（正=增益，负=减益） |
| `scope` | `str` | 生命周期，默认 `"battlefield"` |
| `source` | `str` | 效果来源（追踪/驱散用） |

- **与 `power_mod` 的区别**: `stat_stage` 修改精灵属性阶段（攻防速），每个 stage +10%；`power_mod` 修改技能属性（威力/能耗/连击/优先级），用 delta 加法
- **只管五维**（2026-09-22 起硬约束）：合法 `stat` = `atk`/`def`/`sp_atk`/`sp_def`/`speed`/`speed_flat`（或运行时求值的 `"=@…"`）。
  越界维度（`power` / `energy_cost` / `combo` / `combo_mult`）**一律改用 `power_mod`/`mult_mod`**：
  `stat_stage` 只产生 `StatBuffEffect`，而这三个维度的消费点读的是技能级 `_modifiers`
  （`bs.power` / `bs.energy_cost`）与精灵级 `_modifiers`（`combo`/`combo_set`/`combo_mult`），
  写在 `StatBuffEffect` 里**没有任何读取点**（静默失效）。
  数据面由 `backend/tests/test_stat_stage_to_power_mod.py` 双向 lint 守住（skills + traits 全库扫描）。
- **与 `mult_mod` 的区别**: `stat_stage` 是阶段累加，`mult_mod` 是直接倍率修正（如 `value: 2.0` 表示翻倍）
- **速度的两个单位**：`speed` 1 步 = **10 点**（`steps: 6` = 速度+60）；
  `speed_flat` 1 步 = **1 点**，用于不足 10 点的零头（变形活画「速度+5」→ `steps: 5`）。
  两者在 `effective_stat("speed")` 与快照 `speed_self/speed_opp` 中相加后，再乘 `_modifiers["speed"]` 的百分比修正。

#### `stat_random` — 随机属性增益/减益

把 N 层属性增益（或减益）**随机分配**到五维（`atk` / `def` / `sp_atk` / `sp_def` / `speed`），
每层各占 1 步。用于「获得随机 N 层属性增益/减益」（叠加态 / 做好事 / 吃独食）与
「随机 N 层属性减益」（暗涌印记同款语义）。

- **实现**: `backend/vm/ops/mod.py:op_stat_random()`
- **Mutation**: `StatRandom`
- **消费点**: `backend/engine/replayer.py:_apply_stat_random()` — 逐层 `random.choice(五维)`
  后按层调用 `stat_stage` 的同一条落地路径（写 `StatBuffEffect`）。
  随机源是全局 `random`：对局由 `random.seed(seed)` 播种（与 `morph.pick` 同一约定），
  因此录制/回放仍可复现；MCTS 回滚走 `save_mutable_state` 的效果快照。
- **与 `stat_stage` 的区别**: `stat_stage` 的 `stat` 是确定的单维；`stat_random` 只给总层数，
  分配由引擎随机决定。**无法用 `stat_stage` 组合表达随机分配，故单独设一条指令。**
- **与 `abnormal` 的区别**: 这里产生的是**属性增益/减益**（3014/3018，`StatBuffEffect`），
  不是异常层数。

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵（`sprite_self` / `sprite_opp`） |
| `layers` | `int` / Query | 总层数（每层 = 该维 +1 步；负值按 `direction` 处理前先取绝对值）。Query 走 `value` 通道（`stat_stage` 同款 `steps`+`value` 双字段），可用于「每使用过 N 次选择技能 → 3N 层」这类动态层数 |
| `direction` | `"positive"`(默认) / `"negative"` | 增益 / 减益 |
| `stats` | `list[str]` | 可选，参与随机分配的维度；缺省 = 物攻/物防/魔攻/魔防/速度五维 |
| `scope` | `str` | 生命周期，默认 `"battlefield"`；「永久」类文本写 `"permanent"` |
| `source` | `str` | 效果来源（追踪/驱散用） |

#### `stat_convert` — 属性增益 ⇄ 属性减益转换

把目标身上的**属性增益**整体翻转为同等层数的**属性减益**（或反向）。
用于「敌方的属性增益变为对应的属性减益」（掉包）。

- **实现**: `backend/vm/ops/mod.py:op_stat_convert()`
- **Mutation**: `StatConvert`
- **消费点**: `backend/engine/replayer.py:_apply_stat_convert()` — 就地翻转
  `StatBuffEffect.steps` 的符号（`atk/def/sp_atk/sp_def/speed/speed_flat`，含 `power/combo/priority/energy_cost` 等
  进入 `active_effects` 的可见修饰），并同步 `_modifiers` 取反；
  层数不变，只换正负 → 「化为对应的减益」。
- **与 `dispel` 的区别**: `dispel what:"positive"` 把增益**移除**；`stat_convert` 保留层数、只翻转符号。
- **与 `double` 的区别**: `double` 是层数 ×2，不改变正负。

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `from` | `"positive"`(默认) / `"negative"` | 要转换的方向 |
| `to` | `"negative"`(默认) / `"positive"` | 转换后的方向（缺省 = `from` 的反面） |
| `name` | `str` | 可选，只转换指定 `stat` 维（如 `"atk"`）；缺省 = 全部匹配维度 |
| `source` | `str` | 来源名（记录用；不改写已有 `StatBuffEffect.source`） |

#### `power_mod` — 技能属性修正

修改技能的 power / energy_cost / combo / priority 等属性。

- **实现**: `backend/vm/ops/mod.py:op_power_mod()`
- **Mutation**: `SkillMod`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标技能或精灵 |
| `attr` | `"power"` / `"energy_cost"` / `"combo"` / `"priority"` / `"energy_cost_mult"` / `"combo_mult"` / `"energy_cost_delta_mult"` / `"use_count_bonus"` / `"attach_abnormal"` | 目标属性 |
| `delta` | `int` / Query / RefExpr | 变化量 |
| `mode` | `"add"`(默认) / `"set"` / `"multiply"` / `"set_base"` | 语义，见下 |
| `skill_where` | `dict` | 技能筛选条件（`{"q": "energy_cost", "op": "gt", "value": 3}`） |
| `skill_filter` | `str` | 批量技能筛选：`"attack"` / `"defense"` / `"status"` / `"all"` / `"others"` / `"adjacent"` / `"bare_attack"` / `"bare_defense"` / `"bare_status"` |
| `name` | `str` | 按技能名精确筛选 |
| `element` | `str` | 按系别筛选；`"each"` 表示每种系别各取至多 `per_element` 个 |
| `per_element` | `int` | 配合 `element: "each"`，每种系别数量上限 |
| `scope` | `str` | 生命周期 |

- **与 `stat_stage` 的区别**: 见上
- **与 `mult_mod` 的区别**: `power_mod` 是加法修改（`delta`），`mult_mod` 是乘法修改（`value`）
- **`skill_filter` 语义**（实现：`backend/engine/modifiers.py:matches_skill_filter`，
  技能级落点与特性直连落点共用同一份实现）：
  | 值 | 命中集 | 数据用例 |
  |----|--------|----------|
  | `"all"` | 携带的全部技能 | 基因编辑 等 36 处 |
  | `"attack"` / `"defense"` / `"status"` | 按 `skill_type` 的类别（攻击=物攻/魔攻/动态攻击） | 重金属粉尘 / 壁垒 等 |
  | `"others"` | 除**本回合正在使用的技能**以外的携带技能（对家为目标时取**对手那一手**，见 `battle._turn_skills`） | 激怒「敌方除本回合使用的技能，其他技能能耗+3」 |
  | `"adjacent"` | 参考技能槽位**两侧**的技能（**不环绕**：0 号位只有 1 号位一侧） | 减压阀/联动装置/能量守恒/轴承支撑「两侧技能…」 |
  | `"bare_attack"` / `"bare_defense"` / `"bare_status"` | 无额外效果的纯类型技能（技能 JSON 的 `effects`/`choices`/`passive` 全为空；隐式伤害由 InjectHitPass 注入、不算额外效果） | 不移「携带的无额外效果的攻击技能，威力+30%」 |
  - **参考技能**：技能效果里 = 本次使用的那只技能；特性 Observer 直连效果 = 目标精灵
    本回合正在使用的技能（目标是对家时取对手那一手）。缺上下文（拿不到参考技能）时
    **不匹配**——此前未知 filter 一律「匹配全部」，会把 `adjacent`/`others` 静默放大成
    对全部技能生效。
  - **`element` 是技能级筛选**：只写 `element`（不带 `skill_filter` / `skill_where`）也走
    技能级落点（`power_mod`/`mult_mod` 同为技能槽 `_modifiers`，读取点是 Ctx 的
    `skill_mods`）；此前这种写法落到精灵级而没有读取点 → 静默空操作。
    系别匹配用技能**自带**系别，`"!幻"` 为排除语法。
- **`mode` 三种数值语义**（`power_mod` 与 `mult_mod` 共有，`power_mod` 另有 `set_base`）：
  - `"add"`（`power_mod` 默认）：在槽位现有增量的基础上累加 `value`；
  - `"set"`：把槽位增量**设为** `value`（对 `power`/`energy_cost` 而言最终值 = 技能自带基础值 + `value`，
    因此它设的是**增量**而不是绝对值）；
  - `"multiply"`：增量 × `value`。
- **`mode: "set_base"`**（绝对值语义，基因编辑「自己携带技能的基础能耗，变为上回合双方使用的技能能耗之和」）：
  把技能的**基础值本身**设为 `value` —— 引擎按 `增量 = value − base` 反算后写槽位增量，
  所以最终值恰好等于 `value`（与 `"set"` 的区别就在这个 `base` 项）。
  适用 `attr: "energy_cost"` / `attr: "power"`（这两条通道是「基础值 + 增量」结构）；
  其它 attr 退化为 `"set"`。`value` 支持 Query（如 `{"q":"last_turn_energy_sum","of":"team_both"}`）。
- **`attr: "use_count_bonus"`**: 精灵级「技能使用次数加成」，在下一次行动时被引擎消费——
  技能行动则把该次 `skill_used:<技能名>` 计数额外 +N（喂给「每使用过 N 次」类效果），
  聚能行动则直接丢弃（对齐「入场后首次行动」语义）。可复用于任何「使用次数 +N」效果。
- **`attr: "attach_abnormal"`**（**携带型**属性，3013 附加中毒）：给命中的**技能**挂一个
  「命中后追加 N 层中毒」标记，`value` = 层数。写在技能级 `_modifiers["attach_abnormal"]`
  （配 `skill_filter`/`skill_where`/`name` 选定技能）或精灵级
  `_modifiers["attach_abnormal"]`（不带筛选时）。
  **消费点**：`backend/engine/replayer.py:_apply_damage()` — 技能对**敌方**造成实际伤害后，
  按同一个 `max(精灵级, 技能级)` 口径（与 `life_drain` 一致）给受伤方追加该层数中毒；
  **每次行动只追加一次**（连击不会按段数叠加）。中毒层数走 `replayer._apply_abnormal_change`
  的同一条路（受毒系免疫、`max_stacks` 等约束）。
  应对分支「改为获得附加中毒N」用 `mode:"set"` 覆盖即可。
  **生命周期**：`attach_abnormal` 不在 `_PER_TURN_KEYS` 里，写在技能槽上即跨回合保留；
  因此**不**登记进 `_trait_direct_effects`（登记会每回合重放一次 → 层数叠加，
  见 `replayer._NO_DIRECT_MOD_PERSIST`）。
- **`attr: "energy_gain_delta"` + `on_next: true`（「下回合回复能量-N」的唯一写法，入梦）**：
  数据形如
  ```jsonc
  { "op": "power_mod", "target": "sprite_opp", "attr": "energy_gain_delta",
    "delta": -5, "on_next": true, "scope": "turn", "source": "入梦" }
  ```
  **相位口径**（与其它 `on_next` 不同，因为这里绑定的是**回合**而不是「下一次技能」）：
  1. 落地（入梦结算时）：把 `delta` 压入**目标精灵**的待生效队列
     （`Sprite._pending_energy_gain_delta`，多条累加）——**当回合不生效**；
  2. 武装：`Battle._phase_turn_start` 在**目标下一回合开始时**把它转入
     `Sprite._energy_gain_delta_turn`，于是该**整回合**内所有走
     `Sprite.gain_energy()` 的回复（聚能、印记/异常回能、`energize`…）都吃这个修正；
  3. 到期：`Battle._phase_turn_end` 把 `_energy_gain_delta_turn` 归零 → 再下一回合恢复正常。
  修正挂在**精灵**上（不是场上位置）：目标换人/返场后仍随精灵生效（换在场下时不回能，
  自然无影响）。实测：A 先手入梦 → B 下一回合聚能 `+5−5 = 0`，再下一回合恢复 `+5`。
  与 `flag_set flag:"cooldown"` 无相互作用（入梦的冷却 2 回合由后者独立管理）。

#### `mult_mod` — 倍率修正

修改伤害/威力倍率。

- **实现**: `backend/vm/ops/mod.py:op_mult_mod()`
- **Mutation**: `MultiplierMod`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `attr` | `"power_mult"` / `"damage_mult"` / `"damage_reduction"` / `"life_drain"`，以及基础属性倍率 `"atk"` / `"def"` / `"sp_atk"` / `"sp_def"` / `"speed"`，速度点数 `"speed_flat"` | 目标倍率（校验见 `skill_validate.py:VALID_MULT_ATTRS`） |
| `value` | `float` / Query | 倍率值（1.0=不变）；`attr:"speed_flat"` 时是**速度点数** |
| `mode` | `"add"` (默认) / `"set"` | `"add"`=累加，`"set"`=直接设置 |

- **与 `power_mod` 的区别**: `mult_mod` 是乘法倍率（`value`），`power_mod` 是加法增量（`delta`）
- **与 `stat_stage` 的区别**: `mult_mod` 直接修改最终倍率，`stat_stage` 修改属性阶段（间接影响）
- **`attr:"combo_mult"`** 是**加成分数**（`value: 1` = 连击数 +100%，即段数 ×2；`value: 0.5` = ×1.5）。精灵级（`target:"sprite_self"`，暴风眼）与本次使用（`target:"skill_off_0"`，灵光）同一口径；读取方 = `engine/modifiers.effective_combo_count` 与 `sim/battleskill.effective_combo`（`×(1 + combo_mult)`）
- **速度两条通道**（勿混用）：
  - `attr:"speed"` 是**百分比**倍率（`value: 0.2` = 速度+20%，用于「攻防速+20%」类文本）；
  - `attr:"speed_flat"` 是**点数**（`value: 30` = 速度+30，用于「速度+30」类文本，含 +5 这类零头）。
  两者都不经过 `_cached_stages["speed"]`，而是分别落在 `_modifiers["speed"]`（百分比）与
  `speed_flat` 阶段（点数）；`effective_stat("speed")` 与快照口径一致：`round((base + steps×10 + flat) × (1 + ratio))`。

#### `flag_set` — 布尔标记

设置/清除 boolean 标记。

- **实现**: `backend/vm/ops/mod.py:op_flag_set()`
- **Mutation**: `FlagSet`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `flag` | `str` | 标记名（见下表） |
| `value` | `bool` \| `int` | 开关；`flag:"cooldown"` 时按下方口径解释 |
| `name` | `str` | 配合 `immune` 标记，指定免疫的异常名 |
| `skill_filter` | `str` | 批量技能筛选（同 `power_mod`）：`"attack"` / `"defense"` / `"status"` / `"all"`；`flag:"cooldown"` 时用于选定要改冷却的技能 |
| `skill_where` | `dict` | 技能筛选条件（`tag` / `skill_type` / `element`），语义同 `power_mod` |
| `ttl` | `int` | 存活回合数；`flag:"cooldown"` 且 `value: true` 时作为冷却回合数 |

**flag 合法值**（以 `backend/vm/ops/mod.py` + 消费方实现为准）：

| flag | 含义 |
|------|------|
| `immune` | 免疫指定异常（需 `name`） |
| `survive` | 锁血不死 |
| `charged` / `pre_charged` | 蓄力/预蓄力 |
| `drive` | 传动 |
| `swift` | 迅捷 |
| `extra_action` / `extra_turn_end` | 额外行动/回合末额外触发一次（双向光速） |
| `turn_end_block` | **回合末触发抑制**（陨落）：任意一方场上精灵带此标记（值>0）时，本回合末的**双方**回合末效果全部不触发 |
| `heal_reverse` | 治疗反转 |
| `life_as_energy` | 生命代替能量（**与 `blood_price` 同一机制的两个拼写**，见下） |
| `blood_price` | 生命代替能量（石头大餐/盛宴/骗局；同上） |
| `ignore_mods` / `ignore_resistance` | 忽略修正/抵抗 |
| `cooldown` | 冷却中 |
| `charge_any_skill` | 蓄力中可用任意技能 |
| `usable_while_charging` | 蓄力中可用（技能 body 字段） |

- **`life_as_energy` / `blood_price` 口径**（消耗生命代替能量，`value` = **生命占最大生命的比例 / 1 点能量缺口**；
  两处 0.05 = 每缺 1 能量扣 5% 最大生命）：
  - 读入点是**能量 Gate**（`backend/sim/battle.py`）：能量不足时按
    `hp_cost = round(max_hp × value × 缺口)` 付生命，生命不够则本回合无法使用该技能；
    `Battle.can_pay_skill_energy_cost()`（AI 预判）与 `mcts` 的无 battle 分支同口径
    （`backend/sim/battle.py:sprite_hp_energy_price`）。
  - **技能自带声明在本技能首次使用时即生效**：`flag_set` 是技能效果、在 Gate **之后**落地，
    因此引擎还会读「本技能自带的 `flag_set`（同两个名字之一、数值字面量）」——
    否则 虚假破产 / 骗局 的首次使用永远无法代替（要先用一次才有 flag）。
  - 落地仍然是精灵级 `sprite._modifiers[<flag>]`（scope 决定存活期），所以一旦用过，
    之后的技能也按同一比率代替。

- **与 `stat_stage` / `mult_mod` 的区别**: `flag_set` 是布尔开关，不涉及数值，改变的是游戏规则行为
- **`flag:"drive"` 口径**（传动等级，写的是 `BattleSkill._transmission` 而不是 `_modifiers`）：
  - `target: "skill_at_N"`（翼轴/向心力/贪心算法）：把该槽位的传动等级**设为** `base.transmission + value`
    （在技能自带传动上累加，「传动X 可叠加」）；每回合由 `turn_start` 观察者重挂，因此是**持续**语义。
  - `target: "skill_off_0"`（轮班暗分支「本回合额外传动1」）：让**当前使用的技能**以传动等级
    `value` 参与一次**额外的**传动 pass（即本回合多移动 `value` 个槽位），
    随后把所有技能等级还原——后续回合的传动量不变。其余技能仍按各自的传动等级参与这次额外 pass
    （一次 pass 每人只移动 1 格）。
    回合开始的传动 pass 在本回合**行动选择之前**已经跑完，所以「本回合额外传动1」只能靠这次即时
    pass 体现。传动需要技能数 ≥ 2，否则是空操作（`value` 缺省按 1 处理）。
  - 其它 `skill_*`（无 `skill_at_N` 结构）与不带 `skill_` 前缀的 `target`：写精灵级
    `_modifiers["drive"]`（保留旧行为，无消费者）。
- **`flag:"cooldown"` 口径**（`backend/engine/replayer.py` 消费，写的是技能的 `BattleSkill.cooldown` 而不是
  `_modifiers`）：
  - `value: true` → 把选中技能的冷却**设为** `ttl`（缺省 1）；`value: false` → 清 0；
  - `value: <正数>` → 把冷却**设为**该值（「被应对技能冷却 2 回合」）；
  - `value: <负数>` → 在当前冷却上**加**该值并下限 0（「防御技能冷却 -1」）。
  - 选中技能：`target: "skill_opp_current"` = 对手本回合使用的那只技能；配
    `skill_filter` / `skill_where` 时 = 目标精灵身上命中的技能。
- **`turn_end_block` 抑制范围**（`backend/sim/battle.py:_phase_turn_end`）：
  `SkillResolver.turn_end()`（印记/异常/天气等回合末结算）与其 `extra_turn_end` 额外触发、
  `turn_end` 观察者触发、`post_abnormal_tick` 通知一并跳过；
  延迟效果队列（`at`/`defer`）、ttl 衰减、借用/愿力还原等**记账**不抑制。
  标记本身走 `scope` 生命周期，离场/回合切换即按 scope 清理。

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
| `target` | `target` | 目标精灵（`team_own` 等队伍值走队伍落点，见下） |
| `delta` | `int` / Query | 变化量（正=回复，负=扣除） |
| `overflow` | `bool` | `true` = **回复可突破 `max_energy` 上限**（盗魂铃「回复5能量（可突破上限）」）；扣除不受影响，缺省 `false` |

- **队伍落点**（`_apply_energy_change`）：
  - `team_own` / `team_own_benched` / `team_both`：逐只**跳过力竭**者；
  - `team_own_all` / `team_opp_all`：**全队 6 只**，含替补与力竭者，下限 0
    （`lose_energy` 取 `min(当前能量, 扣除量)`，不会为负）——小型打劫用它；
  - `team_opp` / `opp_team` 在 `energize` 上**不是**队伍落点（落到在场精灵）：
    「敌方队伍中所有精灵」必须写 `team_opp_all`。

- **回复量的精灵级修正**: `Sprite.gain_energy()` 会先加 `energy_gain_delta`（见 §3A
  `power_mod` 的 attr 列表；盗魂铃「在场时自己回复的能量-4」）——`energize`、印记/异常回能等
  所有走 `gain_energy` 的路径一并生效。**「下回合回复能量-N」**（入梦）走
  `power_mod attr:"energy_gain_delta" on_next:true`，相位见 §3A。

#### `revive` — 复活

复活力竭精灵。引擎处理：`hp = max(1, max_hp * hp_ratio)`，清除力竭标记，同队当前位力竭时自动上场。

- **实现**: `backend/vm/ops/mod.py:op_revive()`
- **与 `heal` 的区别**: `revive` 将力竭精灵复活并恢复 HP，`heal` 仅回复在场精灵的 HP，不对已力竭精灵生效

#### `aura` — 计数源光环

按**命名计数源**持续重算的属性修饰：`stat += per_unit × count(计数源)`。
用于「每有 X 便获得 Y」类持续型效果（印记种类数、增益种类数、致死预测……）。

- **实现**: `backend/vm/ops/aura.py:op_aura()`
- **Mutation**: `AuraInjection`
- **引擎服务**: `backend/engine/auras.py`（`COUNT_SOURCES` 注册表 + `refresh(battle)` 幂等重算）
- **与 `stat_stage` 的区别**: `stat_stage` 是一次性增减（`steps` 固定/查询一次）；`aura` 会在
  回合开始与每次行动后**重新计算**，条件消失时自动撤销

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵（光环挂在谁身上） |
| `stat` | `"atk"` / `"def"` / `"sp_atk"` / `"sp_def"` / `"speed"` | 目标属性（1 步 = 10%；speed 为 10 点） |
| `count` | `str` | 计数源名（见下表） |
| `per_unit` | `int` | 每个计数单位增加的步数（默认 1） |
| `count_params` | `dict` | 传给计数源的额外参数（可选） |
| `scope` | `str` | 生命周期，默认 `"battlefield"`（离场即撤销） |
| `affects` | `"self"`（默认）/ `"both"` | 声明归属：`both` = 对场上双方生效 |
| `source` | `str` | 来源名（同一 source + stat 为一条光环，用于幂等追踪） |

**计数源注册表**（`backend/engine/auras.py:COUNT_SOURCES`；新增计数源 = 注册一个函数，数据即可复用）：

| count | 含义 |
|-------|------|
| `mark_kinds_both` | 双方场上不同印记的**种类数** |
| `positive_kinds_both` | 双方场上不同增益的**种类数**（按 stat/异常名去重） |
| `lethal_forecast` | 敌方当前可用技能是否足以击败自己（1/0，估算公式同 `calc_damage`） |

> **幂等性**：已应用步数记录在 `sprite.counters["aura:<source>:<stat>"]`；重算即「目标值 − 已应用值」。
> 离场时随 scope 清除效果并把该计数归零，因此换人来回不会累积漂移。

#### `counter` — 精灵级计数器

读改精灵级计数器（跨回合持久、随精灵换人保留）。累积—重置模式（如「每回复 1 能量 +20%，攻击后重置」）的标准写法。

- **实现**: `backend/vm/ops/aura.py:op_counter()`
- **Mutation**: `CounterWrite`
- **读**: `{"q": "counter", "of": "sprite_self", "name": "<key>"}`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `key` | `str` | 计数器名 |
| `mode` | `"add"` (默认) / `"set"` | 累加 / 覆盖 |
| `delta` | `int` / Query | `mode="add"` 时的增量 |
| `value` | `int` / Query | `mode="set"` 时的目标值 |

- **与 `team_counter` 的区别**: 计数器挂在**精灵**上（`sprite.counters`），队伍级写入口是 `team_counter` op

### 3B. 状态效果类

#### `mark` — 印记

- **实现**: `backend/vm/ops/mark.py:op_mark()`
- **与 `abnormal` 的区别**: `mark` 施加在队伍上（`team_own`/`team_opp`），印记是队伍级效果；`abnormal` 施加在精灵上（`sprite_self`/`sprite_opp`），异常是精灵级效果

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `team_own` / `team_opp` | 目标队伍（`team_both` 仅 `enhance_all` 支持 = 双方各结算一遍） |
| `name` | `str` | 印记名称 |
| `stacks` | `int` / Query | 层数 |
| `action` | `"apply"`(默认) / `"dispel"` / `"steal"` / `"convert"` / `"convert_all"` / `"enhance_all"` | 动作 |

- **`action: "convert_all"`**: 把目标队伍**全部印记合并**为一枚 `name` 印记，层数 = 合并前总层数
  （典型写法 `"value": {"q": "mark_count_opp"}`；与 `convert` 不同，后者的来源是异常而非印记）
- **`action: "enhance_all"`**（许愿池「双方已有的印记层数+1」）：对目标队伍**已存在**的每一枚印记
  `stacks += delta`（`delta` 走 `stacks` / `value` 通道，默认 1）。
  与 `apply` 的区别：**不新建印记**（目标队伍没有印记时是空操作），也不按 `name` 挑选；
  `target: "team_both"` = 双方队伍各加一遍（先己方后对方，各自结算）。
  「已有的印记」= 施加时点已经在 `globals.mark_effects[team]` 里的条目，含正面与负面。
- **`name: "random_positive"` / `"random_negative"`**（薄纱环「随机获得1种正面/负面印记」）：
  `action:"apply"` 时不按固定名施加，而是从 `MARK_TEMPLATES` 的正面（`POSITIVE_MARK_NAMES`）/
  负面（`NEGATIVE_MARK_NAMES`）集合里随机取**一枚模板名**再施加（同一份模板仍然决定行为字段）。
  与 `devotion` 的 `name:"random"` 同一写法约定：随机源是全局 `random`，对局由 `random.seed(seed)`
  播种，因此录制的分支可复现；候选按名字**排序后**取（避免模板字典顺序带来的不可复现）。
  集合为空（无可用模板）时该次施加为空操作。


#### `abnormal` — 异常

- **实现**: `backend/vm/ops/abnormal.py:op_abnormal()`
- **与 `mark` 的区别**: 见上

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `sprite_self` / `sprite_opp` | 目标精灵 |
| `name` | `str` | 异常名称（如 "中毒"、"灼烧"） |
| `stacks` | `int` / Query | 层数 |

**异常模板**（`backend/engine/abnormal_config.py:ABNORMAL_TEMPLATES`）字段驱动行为，
新增异常无需改引擎：

| 字段族 | 作用 |
|--------|------|
| `tick_damage_pct` / `tick_element` / `decay_on_tick` / `tick_per_stack` / `max_stacks` | 回合末结算（中毒/灼烧/寄生/冻结…） |
| `threshold_stacks` / `threshold_damage_pct` / `threshold_element` / `threshold_consume` / `threshold_immune_element` | **层数阈值即时效果**（引电：获得 2 层立即受 25% 生命电系伤害并失去 2 层，电系免疫），由 `replayer._apply_abnormal_threshold()` 消费 |
| `release_on_action` / `release_on_damage` | **情报遮蔽状态的解除时机**（见下） |

##### 木桶 / 月陨星 — 情报遮蔽状态（3024 / 3025）

游戏内文本：「该状态下会隐藏精灵的信息，自己行动或被敌方攻击时解除。」

- **状态本体**：`ABNORMAL_TEMPLATES` 的 `木桶状态` / `月陨星状态` 两条模板
  （`release_on_action = release_on_damage = True`，无 tick），因此
  **可施加**（`abnormal` op / `inherit` 的 `effects`），层数 = 1。
- **解除时机**（引擎在事件点统一消费，只对**持有该状态**的精灵生效）：
  | 时机 | 引擎落点 |
  |------|----------|
  | 自己行动（行动完成） | `backend/sim/battle.py:_execute_skill_vm()` 执行尾（`remove_effect("charged","state")` 同一处） |
  | 被敌方攻击（受伤落地） | `backend/engine/replayer.py:_apply_damage()`（**只对受击方**，自我伤害不解） |
- **本引擎不实现观测遮蔽**：本引擎对局是完全信息（双方 agent 读同一份 state），
  「隐藏精灵的信息」没有任何可观测的战斗后果，因此只落地**获得/解除**两个时点；
  隐藏信息本身在数据注释里说明，不写代码。这使「状态在哪个回合消失」可被差分录制观察到。
- **施加方式**：`{"op": "abnormal", "target": "sprite_self", "name": "月陨星状态",
  "stacks": 1, "scope": "battlefield", "source": "观测者效应"}`（scope 走 `battlefield`：
  离场即随 scope 清除，符合「更换入场者才带此状态」的场景）。

#### `weather` — 天气

- **实现**: `backend/vm/ops/weather.py:op_weather()`

| 字段 | 类型 | 说明 |
|------|------|------|
| `weather` | `str` | 天气名称 |
| `turns` | `int` | 持续回合数 |
| `extend` | `bool` | `true` = **延长**语义而非重设（见下） |

- **`extend: true` 口径**（汇流：「雨天的回合数延长4回合」）：
  - 当前天气（`GlobalEffects.weather`，经 `normalize_weather` 归一）与 `weather` **相同** →
    `weather_turns += turns`（延长，不重置剩余回合）；
  - 当前**无天气** → 按 `set_weather` 起 `turns` 回合（兜底：技能不会空转）；
  - 当前是**其它天气** → 不生效（返回空事件），避免「延长」被读成「覆盖对手天气」。
- **应对分支取最大值而非叠加**：同一技能里既有无条件 4 回合又有应对 8 回合时，
  用 `when: counter_succeeded → then/else` 二选一表达（汇流即此写法），不要串联两条 `extend`。

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

#### `replace_skill` — 把对手当前技能替换为指定技能（本回合有效）

把**对手本回合正在使用的那只技能**替换为指定技能，替换在**本回合内生效**、
回合末自动还原。用于「应对状态时被应对的技能变为透射」（透镜实验）。

- **实现**: `backend/vm/ops/replace_skill.py:op_replace_skill()`
- **Mutation**: `ReplaceSkill`
- **消费点**: `backend/engine/replayer.py:_apply_replace_skill()` — 按名字定位对手的
  `BattleSkill`（与 `flag_set flag:"cooldown" target:"skill_opp_current"` **同一套定位**：
  对手队伍 `battle._turn_skills[opp_team]["name"]`），置 `replaced_by = Skill(目标技能)`，
  并把 `(team, slot)` 登记进 `battle._replaced_restore`；`backend/sim/battle.py:_phase_turn_end()`
  统一 `replaced_by = None` 还原。
- **生效时机**：应对方（如透镜实验，`counter: "状态"`）行动**必定先手**，所以替换发生在
  对手行动**之前**，对手那一手就会真的用出替换后的技能；`battle._get_skill()` 每次从
  `sprite.skills[index]` 现取，因此替换无需额外通知谁。
- **与 `morph`（巧变）的关系**：两者共用 `BattleSkill.replaced_by`，但**互不破坏**——
  - 巧变产物由 `_morph_temp` 标记、使用后由 `morph.revert_after_use()` 还原；本 op **不写**
    `_morph_temp`，因此不会把巧变状态误当成本 op 的产物；
  - 若目标槽位当时正挂着巧变产物，本 op 覆盖 `replaced_by` 后 `_morph_temp` 仍为真 →
    对手用完替换技能后槽位**还原为原技能**（巧变本就是一次性，语义不冲突）；
  - 回合末 `_replaced_restore` 只清 `replaced_by`，不碰 `_morph_temp`。
- **与 `borrow` 的区别**: `borrow` 替换的是**自己**当前技能槽（复制对手技能属性）；
  `replace_skill` 改的是**对手**的槽位。

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `"skill_opp_current"` | 目标定位（当前仅支持对手本回合技能） |
| `skill` | `str` | 替换后的技能名（数据里须存在同名技能 JSON） |
| `scope` | `str` | 生命周期，固定 `"turn"`（回合末还原） |

#### `double` — 翻倍

将指定类型效果的层数/步数 ×2。

- **实现**: `backend/vm/ops/double.py:op_double()`
- **与 `mult_mod` 的区别**: `double` 翻倍的是效果层数（如增益层数、异常层数），`mult_mod` 修改的是数值倍率（威力倍率、伤害倍率）

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标 |
| `what` | `"positive"` / `"negative"` / `"abnormal"` / `"mark"` | 翻倍类型 |
| `name` | `str` | 指定具体 abnormal/mark 名称 |

- **`what: "mark"` 口径**（二律背反「应对防御：额外使敌方星陨印记层数翻倍」）：
  `target` 是**队伍**（`team_own` / `team_opp` / `own_team` / `opp_team`），
  按 `name` 定位该队印记并把 `stacks ×2`（不指定 `name` 时翻倍该队全部印记）；
  目标队伍须按 `self.team` 换算，`team_opp` = 对手队。
  消费点 `backend/engine/replayer.py:_apply_double()`。

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

#### `starfall_trigger` — 手动触发星陨印记

把「何时触发、以什么伤害类型触发」交给数据，**结算复用自然路径**
`GlobalEffects.trigger_starfall()`（消耗印记层数 + `X² + 24X − 24` 幻系伤害，
`X` = 触发前层数），不复制公式。用于引力偏转「减伤80%，应对攻击：以魔法伤害触发敌方的星陨效果」。

- **实现**: `backend/vm/ops/starfall.py:op_starfall_trigger()`
- **Mutation**: `StarfallTrigger`
- **消费点**: `backend/engine/replayer.py:_apply_starfall_trigger()` → `battle.globals.trigger_starfall()`
- **与自然结算的关系**: 攻击技能（非幻系）命中后的自动引爆炸在同一函数
  （`battle._execute_skill_vm` 第 6.7 步），因此同样层数/同样攻防键下两者**伤害同值**；
  本 op 只是把触发时机从「攻击命中」挪到数据指定的位置（如应对成功分支）。
- **攻守方向**: `target` 是**印记持有方**（= 防守方，吃伤害），触发方（`self.self`）是攻击方。
  `sprite_opp`（默认）= 打敌方队伍持有的星陨印记；`sprite_self` = 触发自己队持有的。
- **攻防键**由 `damage_type` 决定：`物攻`→atk/def，`魔攻`→sp_atk/sp_def，
  `动态攻击`→按触发方（攻击者）的物/魔攻高低判定（与自然结算同一份
  `Skill.get_atk_def_keys` 口径）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `sprite_self` / `sprite_opp` | 印记**持有方**（默认 `sprite_opp`） |
| `damage_type` | `"物攻"` / `"魔攻"` / `"动态攻击"` | 触发用的伤害类型（默认 `魔攻`） |

- **空操作**：目标队伍没有星陨印记（或层数 ≤0）时返回 0 伤害，不产生事件。

#### `skill_rotate` — 跨精灵技能轮转

把「轮转哪一队、转几位」交给数据，落地由可复用 pass
`backend/sim/battle_mechanics.py:BattleMechanicsMixin.rotate_team_skills()` 完成。
用于过山车「使己方队伍中的所有精灵携带的技能跨精灵向下移动1个位置」。

- **实现**: `backend/vm/ops/skill_rotate.py:op_skill_rotate()`
- **Mutation**: `SkillRotate`
- **消费点**: `backend/engine/replayer.py:_apply_skill_rotate()`

**轮转口径**（实现与边界规则）：

1. **序列构造**：按「精灵顺序（`player.team` 顺序）× 槽位顺序」拼接**参与槽位**，
   得到 `[S0.0, S0.1, S1.0, …]`；**位置锁定**的槽位被跳过、也不占位：
   - 主轴（`_transmission == -1`）——与传动 pass 同一语义：位置不动；
   - 临时/被替换槽位（`replaced_by is not None`：借用 / 愿力 / 巧变产物 / `replace_skill`）
     —— 这些槽位的回合末还原登记是 `(队伍, 槽位号)`，移动槽位对象会让还原命中错误的槽位；
   - `is_temporary`（`gain_skills` 等临时技能）。
2. **整体轮转 `offset` 位**（默认 1，向下移动）：`rot[i] = seq[i - offset]`，
   末尾的 `offset` 个技能回到序列开头；**各精灵槽位数可以不同**——按各精灵**原槽位数**
   分段写回，因此不需要补齐、技能总数不变。
   - 3 只 × 2 槽的例：`[A,B,C,D,E,F]` → `[F,A,B,C,D,E]` = 每只的技能列表整体下移 1 位，
     最后一只的末位技能回到第一只的第一槽位。
   - 参与槽位 < 2（或 `offset % n == 0`）时为空操作。
3. **轮转的是 `BattleSkill` 槽位对象整体**（不是底层 `Skill` 数据）：冷却 / `replaced_by` /
   `_morph_temp` / 技能级 `_modifiers` / `_transmission` 随对象一起移动。
4. **通知**：移动到新槽位的每个技能各触发一次 `skill_position_changed` 通知
   （`_fire_skill_position_changed`，与传动 pass 一致），因此
   `cond: "skill_position_changed"` 的观察者会看到跨精灵移动。

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `team_own`（默认）/ `team_opp`（也接受 `sprite_self`/`sprite_opp` 作为同义写法） | 被轮转的队伍 |
| `offset` | `int` | 轮转位数（默认 1 = 向下移动 1 个位置） |

> **不联动**：本 op **不**触发机械变式（20159「若回合内自己携带的技能位置发生变化，
> 该技能能耗永久-1」，`sim/globals.py` 里由**传动 pass** 单独消费）——
> **用户 2026-09-22 确认：过山车带来的跨精灵移动不触发机械变式。**

#### `charge` — 蓄力

- **实现**: `backend/vm/ops/charge.py:op_charge()`
- **技能写法**：`{"when": {"cond": "charged"}, "then": [...], "else": [{"op": "charge"}]}`——未蓄力时走 `else`（本回合只开始蓄力、**0 伤且不付能耗**），下一回合门控把状态升成 `charged` 后走 `then`（正常结算，能耗在释放回合支付）。全库 5 条：升龙咆哮 / 吹炎 / 怨力打击 / 龙之利爪 / 龙吟。
- **门控**：`sim/battle.py::_gate_charge_vm`（蓄力中禁止聚能、释放蓄力技能、`usable_while_charging` 放行、`pre_charged` 跳过首次蓄力）。「蓄力中禁止聚能」**只挡聚能**——此前漏了 `action.kind == 'gather'` 限定，把释放动作也挡下，精灵被永久锁死在蓄力中（第 2 回合起全是「蓄力中无法聚能」，0 伤；2026-09-22 修）。
- **估伤口径**：`sim/resolver._would_charge` 与门控同判据（复用 `Battle._skill_has_charge`）——未蓄力时估伤为 **0**，蓄力中（释放回合）为满值。

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

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `"sprite_opp"`(默认) / `"leaving"` / `"entering"` | 交换的**对家**：与 `replayer.self` 配对的那一只 |
| `what` | `str` | 交换内容（见上表） |

- **交换双方 = `replayer.self` ↔ `target`**：
  - `"sprite_opp"`（默认）：`replayer.opp`。技能内使用（`假冒`/`恶念交换`/`欺诈契约`/`隐藏条款`）与
    `post_enemy_leave` 语境（敌方离场）都成立 —— 后者的 `self` 是**己方场上精灵**、`opp` 是
    **敌方换入者**，正好是「自己与更换入场的精灵」这一对。
  - `"leaving"`：`replayer._leaving`，即**刚离场的那只**（仅 `post_enemy_leave` 提供；
    无该语境时为空操作）。
  - `"entering"`：本次的**换入者**。`post_enemy_leave` 下就是 `replayer.opp`（与默认等价，
    但把「与更换入场的精灵」写进数据，意图可审计）；`post_leave` 下引擎不提供（空操作）。
- **与内联实现的关系**：`瞳中倒影` 的「**自己**离场时与换入者交换血量百分比」由
  `sim/battle_mechanics.py:_resolve_switch` 内联（那是唯一知道「换入者是谁」的位置），
  数据面不要为这一半再写 observer（会重复交换）；数据面只负责「**其他精灵**离场时」这一半。

#### `reset` — 重置

消除永久增量，将指定 stat 还原到基础值。

- **实现**: `backend/vm/ops/reset.py:op_reset()`
- **消费点**: `backend/engine/replayer.py:_apply_reset()`
- **与 `dispel` 的区别**: `reset` 重置技能属性（如能耗），`dispel` 移除效果（增益/减益/印记/异常）

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标技能 |
| `stat` | `str` | 要重置的属性（如 `"energy_cost"`） |

- **落地口径**：技能槽的最终值 = 技能自带基础值 + `_modifiers[stat]` 增量，所以「还原到基础值」
  = 清掉该增量，并同时清掉 `sprite._modifiers["skill.<技能名>.<stat>"]` 的永久登记
  （`scope:"permanent"` 的 `skill_off_0` 修正会在那里留一份，否则下一回合
  `_load_permanent_skill_mods_for_sprite()` 又把增量装回来）。
- `target`：`skill_off_0`（默认）= 本次使用的技能槽；`skill_at_N`（1-indexed）= 第 N 个技能槽；
  其余值 = 精灵级 `_modifiers[stat]` 增量。
- 用例：气沉丹田「每次应对后本技能能耗-3，使用后能耗重置」——本次使用已按折后能耗支付，
  `reset` 在效果阶段执行，清掉累计的 -3 供后续使用。

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
| `counter` | `{name, threshold, reset}` | 可选计数器（threshold=N 每 N 次命中触发；reset=true 触发后清零） |
| `reset` | `str` | `""` / `"turn"`；`"turn"` 表示计数/触发每回合开始清零（每回合限次；配合 `threshold:1` 与 `counter.reset:false` 实现"每回合各1次"） |
| `once` | `bool` | 每场战斗仅触发一次（整点报时 等） |
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
- **消费点**: `backend/engine/replayer.py:_apply_inherit_effects_mutation()`

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `str` | 继承目标（解析为 `inherit_target`，默认 `"enemy_new"`；`"ally_new"` = 己方新入场者） |
| `scope` | `str` | 取源精灵身上该 scope 的效果（`inherit_stat_effects: true` 时改为取全部 `StatBuffEffect`） |
| `via_pending` | `bool` | `true` = 不立刻给谁，而是压入 `battle.pending_effects[team]`，由**下一个入场者**在入场流程里领取（`battle_mechanics._apply_pending_entry_effects`） |
| `effects` | `[RiscIROp]` | **显式效果列表**：非空时以它为准（不再从源精灵拷效果），每条经 `effect_factory.from_dict` 变成 `EffectObject` 后同样走 `via_pending` / 立即施加两条路 |
| `inherit_stat_effects` | `bool` | `true` = 继承全部属性增益（六维/连击/威力/吸血） |

- **`effects` 是「离场后换入者以 X 状态登场」的标准写法**（木桶戏法 / 观测者效应）：
  ```jsonc
  { "op": "observer", "cond": { "cond": "sprite_left", "of": "sprite_self" },
    "listen": "post_leave", "scope": "persistent",
    "then": [
      { "op": "inherit", "source": "self", "via_pending": true,
        "effects": [ { "op": "abnormal", "name": "木桶状态", "stacks": 1,
                       "scope": "battlefield", "source": "木桶戏法" } ] }
    ] }
  ```
  只声明 `effects` 时不需要读源精灵状态，因此**不依赖**离场精灵身上真的带着该效果。

#### `burst_grant` — 迸发注入

给命中的**技能槽**挂上「下次迸发时会额外执行」的效果列表（1010 迸发：`first_action` 为真时
`sim/battle.py` 执行 `bs._burst_effects`）。

- **实现**: `backend/vm/ops/burst_grant.py:op_burst_grant()` → `BurstGrant`
- **消费点**: `backend/engine/replayer.py:_apply_burst_grant()` 把效果写进匹配的
  `BattleSkill._burst_effects`（并置 `_modifiers["burst"]`）
- **与 `replay from:"team_burst"` 的区别**: `replay` 是**立即执行**本队已触发过的迸发效果；
  `burst_grant` 是**授予**（挂到技能槽上，等下一次迸发时执行）

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 接受授予的精灵（默认 `sprite_self`） |
| `skill_filter` / `skill_where` | 同 `power_mod` | 选定被授予的技能（如 `"attack"` = 携带的攻击技能） |
| `then` | `[RiscIROp]` | **显式**注入的迸发效果列表（默认来源） |
| `from` | `"explicit"`(默认) / `"triggered"` | `"triggered"` = 从**本队已触发过的迸发**里取回效果，忽略 `then` |
| `count` | `int` / `"all"` | 仅 `from:"triggered"` 生效：取最近 N 条（默认 1）；`"all"` 或 ≤0 = 全部 |
| `source` | `str` | 来源名（事件文案用） |

- **`_burst_effects` 的唯一表示是 IR**：`[RiscIROp]`（与技能 `effects[]`、observer `then` 同格式）。
  `sim/battle.py` 的迸发执行点把它直接交给 VM（`execute_effects`），因此三条写入路径**都必须在
  写入前编译**：
  1. 技能显式 `then` —— 编译期由 `SkillParsePass._parse_burst_grant` 编译，`op_burst_grant` 原样传下去；
  2. `from: "triggered"` —— 池子里存的就是「该技能的完整效果列表」（已编译 IR），原样取回；
  3. **特性 direct-mods 通道**（`trait_loader._apply_burst_grant_direct`，生物电/电流刺激/超负荷）——
     这条通道运行期直接解释特性 JSON、**不经过编译器**，所以它自己负责 `compile_effects_batch(then)`
     后再写入（2026-09-23 修：此前写的是未编译的 dict，与 IR 条目混在同一列表里，
     同源去重按 dict 读 `source` 就崩在 `WhenBlock` 上）。
  写入端去重（同 `source` 替换）读 `source` 时用属性访问（IR 字段）；
  池子里的顶层条目可能是 `WhenBlock`（**没有 `source` 字段**）→ 读不到就当作"不同源"保留。
- **可执行检查**：`backend/vm/executor.py:assert_ir_effects(effects, where=...)` 在
  写入/登记边界断言"列表里没有未编译 dict"（非 `-O` 时生效），调用点：迸发池登记、
  `_skill_history` 登记、`burst_grant` 两条写入路径、observer 注册。
  技能侧效果的唯一出口 `BattleVMEngine._get_effects()` 也保证**出口一律 IR**
  （拿到 raw dict 记录会就地编译；已是 IR 时原样返回，保住按 `id(tuple)` 命中的排序缓存）。
  回归测试：`backend/tests/test_effect_representation.py`。

- **`from: "triggered"` 口径**（踏雷「携带的攻击技能下回合获得1个已触发过的迸发效果，
  应对防御：改为获得所有触发过的迸发效果」）：
  - 来源池是 `BattleVMEngine._burst_effects[team]` —— 每个「以迸发身份使用过的技能」记一条
    `(技能名, 该技能的完整效果列表)`，按**触发时间升序**存放（`engine/battle.py:execute()` 在
    `is_first` 时登记）。
  - `count: N` 取**最近 N 条**（池子的尾部，最新触发的优先），`count:"all"`（或 ≤0）取全部。
    同一技能重复触发会占多条，本 op **不去重**（池子是什么就授什么）；
    「获得 1 个」在只有一个候选时就是那一个。
  - 取到的效果**原样**追加到每个命中技能槽的 `_burst_effects`，与显式 `then` 追加的写法一致
    （多条叠加、不会被覆盖）。
  - 池子为空（本队还没触发过迸发）→ 空操作。
  - 时间语义：本 op 在**技能执行期间**写槽位，槽位上的迸发效果在**下一次**该技能以迸发身份行动时
    执行；踏雷自带 `return`（回合末返场）会把 `first_action` 置回真，因此「下回合」的首次行动
    即触发（不需要额外的 `delay` 声明）。

#### 其他持久化 opcode

| opcode | 实现 | 说明 |
|--------|------|------|
| `transform` | `backend/vm/ops/transform.py:op_transform()` | 形态变换（`species` + 可选 `skills`） |
| `team_counter` | `backend/vm/ops/team_counter_write.py:op_team_counter_write()` | 队伍计数器写入（`key`, `delta`, `target_team`） |
| `lives` | `backend/vm/ops/lives_change.py:op_lives_change()` | 队伍魔力值增减（`delta`, `target_team`） |
| `trait_interaction` | `backend/vm/ops/trait_interaction.py:op_trait_interaction()` | 特性交互（`action: "suppress"`） |
| `devotion` | `backend/vm/ops/mod.py:op_devotion()` | 队伍奉献注册（`name`/`then`/`ttl`；原 `mod stat=devotion` 已专用化） |
| `replay_branch` | `backend/vm/ops/replay_branch.py:op_replay_branch()` | 重放当前「选择」技能的另一支/相同一支（`which: "other"/"same"`；有求必应/一意孤行） |
| `burst_grant` | `backend/vm/ops/burst_grant.py:op_burst_grant()` | 迸发注入 |
| `gain_skills` | `backend/vm/ops/gain_skills.py:op_gain_skills()` | 获得技能 |
| `effect_delta` | `backend/vm/ops/effect_delta.py:op_effect_delta()` | 效果层数增量 |

> 旧版 `mod`（mega-opcode）、`count`（observer 别名）、`schedule`（defer 别名）已删除：数据统一为
> `stat_stage`/`power_mod`/`mult_mod`/`flag_set`/`heal`/`energize`/`devotion` + `observer`/`defer`。

### 3E. 机制授予类

这三个 op 不直接改变战斗数值，而是**给技能/行动挂上可复用的机制**，由引擎侧服务在执行点消费。

#### `element_convert` — 技能属性转换

在场时把命中的技能属性转换为另一属性（如 展翅「携带的普通系技能变为翼系技能」）。

- **实现**: `backend/vm/ops/aura.py:op_element_convert()`
- **Mutation**: `ElementConvert`
- **落地**: 写 `BattleSkill._element_override`；引擎在构建 Ctx 时用生效属性计算伤害与克制

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `from` | `str` | 源属性 |
| `to` | `str` | 目标属性 |
| `skill_filter` / `skill_where` | — | 技能筛选（同 `power_mod`） |
| `scope` | `str` | 生命周期，默认 `"battlefield"`（离场撤销） |
| `affects` | `"self"`（默认）/ `"both"` | `both` = 对场上双方生效 |
| `source` | `str` | 来源名 |

#### `morph` — 巧变授予

命中的技能**使用后变为** `category` 类别的技能（巧变）。

- **实现**: `backend/vm/ops/aura.py:op_morph()`
- **Mutation**: `MorphGrant`
- **引擎服务**: `backend/engine/morph.py`（`POOL_BUILDERS` 池注册表 + 确定性选池）
- **落地**: 在 `BattleSkill._morph_source` 记下来源；技能结算后把该槽 `replaced_by` 置为池中新技能

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵（技能所属） |
| `category` | `str` / `dict` | 池类别：内置名 `"same_element"`，或 spec dict（`{"element": "恶", "skill_type": "attack"}` / `{"contains": "冻结"}`，由 `filter` 池按 element/skill_type/描述子串筛选） |
| `skill_filter` / `skill_where` | — | 技能筛选（`all` = 该精灵全部技能） |
| `scope` | `str` | 生命周期，默认 `"battlefield"` |
| `affects` | `"self"`（默认）/ `"both"` / `"team"` | `both` = 场上双方携带的技能同时获得（魔术帽）；`team` = **授予者所在队伍全体**（含场下）携带的技能获得（齐鸣），场下精灵在其上场/使用时按需求值，无额外持久状态 |
| `source` | `str` | 来源名（也是「谁授予的」的判定依据） |

> `affects` 对 `element_convert` / `grant_choice` 同样生效（三者共用
> `backend/engine/mechanisms.py:_grantors` 的授予者查找：自身声明 + 对方场上 `both` 声明 +
> **同队任一精灵的 `team` 声明**）。

**技能自带巧变**：技能 JSON 可用顶层 `qiaobian` 字段声明自带的「巧变：X」
（11 个 wiki 标为巧变的技能已登记），开战时由 `morph.register_skill_morphs()`
按技能名挂同一条 morph 声明，与特性授予走同一机制；`filter` 池的 spec 亦支持
`{"name": "虫鸣"}`（齐鸣「巧变：虫鸣」）。

**巧变语义**（来源：wiki.biligame.com/nrc/巧变 「游戏内介绍」）：
    使用后会变为**指定范围内的随机技能**，且**能耗-1**。**使用该随机技能后会变回原技能。**
落地：授予技能结算后 → 槽位 `replaced_by` = 池中随机技能 + 标记 `_morph_temp`（该槽能耗-1）；
该随机技能被使用后 → `revert_after_use()` 还原原技能。池按 id 升序后 `random.choice`
（对局由 `random.seed(seed)` 播种，录制/回放可复现）。

#### `morph`（技能顶层字段）— 变身

与巧变（`qiaobian`）**不是同一机制**，只是共用 `BattleSkill.replaced_by` 这一个字段：

| | 巧变 `qiaobian` | 变身 `morph` |
|---|---|---|
| 形态 | 顶层 `qiaobian`：池名 / spec dict | 顶层 `morph`：`{"from", "mode", "exclude_self", "exclude_owned", "energy_delta"}` |
| 触发 | **使用后**变化（`morph.apply_after_use`） | **每回合开始时**重掷（`morph.apply_henshin`，由 `Battle._phase_turn_start` 调用） |
| 能耗 | 产物能耗-1（`_morph_temp`） | 无（只有 `energy_delta` 声明的修正，见下） |
| 还原 | 产物被使用后还原原技能 | 不还原：每回合重掷覆盖（回合末不清理，下回合再掷） |
| 池 | `same_element` / `filter` | `team_own` |

**池（`from: "team_own"`）** = 己方队伍中**其他精灵**的技能槽（取当前有效技能名，去重、按技能 id 升序，
再 `random.choice`，与巧变同一确定性约定）。`exclude_owned: true` 再从池中剔除**施法者已携带**的技能
（复写「自己未携带的技能」）。`mode` 目前仅 `"random"`。

**能耗修正 `energy_delta`**：产物技能本回合的能耗加该值（复写 -2）。落地在
`Battle.skill_energy_cost()`：仅当槽位由变身写入（`bs.base.morph` 存在且 `_morph_temp` 为假）时生效。

**来源与边界**（wiki.biligame.com/nrc「借用」/「复写」）：
- 借用「每回合随机变成己方队伍中其他精灵的技能」→ `from: team_own`（可抽到自己也有的技能）。
- 复写「每回合随机变成自己未携带的技能，且该技能能耗-2」→ `exclude_owned: true` + `energy_delta: -2`。
- 只有**场上（active）**精灵在回合开始时重掷；换上的精灵从下个回合开始吃变身。
- 池为空（单只精灵队伍、或队友无技能）→ 不替换，该槽保持原技能。

#### `grant_choice` — 分支授予

给命中的技能（或聚能行动）**追加一个可选分支**（如 异类「翼系攻击技能获得选择：能耗+1，攻击时吸血50%」）。

- **实现**: `backend/vm/ops/aura.py:op_grant_choice()`
- **Mutation**: `ChoiceGrant`
- **落地**: 技能 → `BattleSkill._granted_choices`（与技能自带 `choices` 合并，授予分支追加在后）；
  聚能 → 精灵级记录，聚能分支号为 1
- **动作编码**: 数据分支占 `0..n-1`，授予分支追加在 `n` 之后（AI 默认选 0，即原行为）

| 字段 | 类型 | 说明 |
|------|------|------|
| `target` | `target` | 目标精灵 |
| `action` | `""`(默认) / `"gather"` | 作用于技能 / 作用于聚能行动 |
| `name` | `str` | 分支组名（展示用） |
| `choices` | `list` | 分支数组，格式同技能 `choices`：`[{"name": ..., "cond": {...}?, "effects": [...]}]` |
| `skill_filter` / `skill_where` / `element` | — | 技能筛选（`element` 支持 `"!幻"` 排除语法） |
| `scope` | `str` | 生命周期，默认 `"battlefield"` |
| `affects` | `"self"`（默认）/ `"both"` | `both` = 对场上双方生效 |
| `source` | `str` | 来源名 |

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

- **`skill_use.skill_type` 支持类别别名**：`"attack"`（物攻/魔攻/动态攻击）、`"defense"`（防御）、`"status"`（状态），
  也可写精确值（`"魔攻"` 等）

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

#### 血脉

| cond | 参数 | 访问路径 | 说明 |
|------|------|----------|------|
| `is_mixed_blood` | `of`（默认 `sprite_self`） | `ctx.is_mixed_blood_self` / `ctx.is_mixed_blood_opp` | 目标是否**混血**精灵（色散「对混血精灵造成伤害+50%」） |

- **混血判定**（游戏内文本 3015：「非本系血脉的精灵，包括非本系的系别血脉、首领血脉、奇异血脉、污染血脉等」）：
  - `sprite.bloodline` 是**特殊血脉**（`首领` / `污染` / `奇异`，见 `common/constants.SPECIAL_BLOODLINES`）→ 混血；
  - `sprite.bloodline` 是系别，但**不在**该精灵种族系别 `species.elements` 里 → 混血；
  - 血脉为空 → 按**非混血**处理（拿不到血脉时不误判）；
  - 其余（血脉 = 自身某一系别）→ 非混血。
- 判定在 `backend/engine/snapshot.py:build_ctx()` 预计算为 `is_mixed_blood_self/opp`
  （条件求值只读寄存器，故不在 cond 里现算）；纯函数实现在
  `backend/engine/bloodline.py:is_mixed_blood()`，供引擎与数据两侧复用。
  **Cython 路径**（`snapshot_cy.build_ctx_cy`，签名固定、不认识新寄存器）由
  `snapshot.py:fill_extended_registers()` 在构造后补齐，两条路径行为一致。
- 也可用 Query 直接读名字：`{"q": "bloodline", "of": "sprite_self"}`（配合 `compare` 的 `in`/`contains`）。

#### 回合边界

| cond | 参数 | 访问路径 |
|------|------|----------|
| `turn_end` | — | `ctx.event.turn_end` |
| `turn_start` | — | 永远为 `True` |
| `always` | — | 永远为 `True` |

#### 世界状态

| cond | 参数 | 访问路径 | 触发点 |
|------|------|----------|--------|
| `is_night` | — | `ctx.is_night` | `post_entry` / `turn_start` / `turn_end` |

- **`is_night`**：王国入夜（世界状态）。`GlobalEffects.night` 决定，**默认 False（确定性）**；
  在线服务由 API 传 `night=kingdom_is_night()`（每日 23:00–次日 4:00 为夜晚，见
  `backend/sim/globals.py`）。用于「入夜后…」类特性（安眠）。

#### 引擎写入的通用计数寄存器

以下计数器由引擎在**通用时机**写入，数据面通过 `{"q": "team_counter", ...}` 读取，可被任意特性复用：

| 计数器 | 写入时机 | 含义 |
|--------|----------|------|
| `turn_match` | 回合末结算（turn_end 观察者触发前） | 本回合双方使用的技能在**系别/类型/能耗**上相同的项数（0–3）；每回合覆盖写入 |
| `last_turn_element:<系别>` | 同上（回合末，覆盖写） | **上一回合**该队使用该系别技能的次数；与累计的 `element:<系别>` 不同，本键每回合被重写（上一回合没用过 → 0），是「上回合有没有用过 X 系技能」的唯一可靠来源 |
| `last_turn_energy_sum` | 同上（回合末，覆盖写） | **上一回合**该队使用技能的能耗之和；与累计的 `energy_spent` 不同（后者只增不减）。数据面一般读寄存器 `last_turn_energy_sum`（见 §1.2）而非这个计数器 |
| `enemy_action` | 敌方聚能或换人时 | 敌方累计聚能 + 换人次数（写在**对方**队伍上） |
| `element:<系别>` | 每次技能结算后 | 该队累计使用该系别技能的次数 |
| `distinct_elem:<系别>` | 首次使用某系别技能时 | 该队使用过的**不同**该系别技能数（大火球/大雪球） |
| `counter_success` | 应对成功时 | 该队应对成功次数 |
| `defense_skill` / `status_skill` | 技能结算后 | 该队使用的防御/状态技能次数 |
| `choice_used:<分支名>` / `choice_pair` | 「选择」技能结算后 | 分支使用次数 / 明暗各一次的对数（猫精灵的礼物） |
| `choice_skill_used`* | 「选择」技能结算后 | **精灵级**（`sprite.counters`，不是队伍计数器）：「选择」技能使用次数；做好事/吃独食读它（×3 层随机属性增益/减益，用完清零） |
| `energy_spent` | 技能耗能后 | 该队累计消耗能量（整点报时） |
| `used_elem:<系别>`* | 首次使用某系别技能时 | **精灵级**（`sprite.counters`，不是队伍计数器）：该精灵用过该系别技能；寄存器 `elements_used_count_self` = 该精灵此类键的条数（旧玩具） |

#### 上回合系别寄存器（跨回合连携）

| cond | 参数 | 访问路径 | 说明 |
|------|------|----------|------|
| `last_turn_had_element` | `element`, `of` | `ctx.last_turn_element_own` / `_opp` / `_both` | **上一回合**是否有精灵使用过该系别技能 |

- **`of` 取值**：`"team_both"`（默认，双方任一精灵用过即成立）/ `"team_own"` / `"team_opp"`。
  「上回合**双方有精灵使用**X系技能」的游戏内文本（奇点/信息素/掠影/星火/月影交错/麦芒/冷光源/热成像
  共 8 条同款写法）按**存在性**落地：搜索范围是双方精灵，任一方用过即成立（写成「各有」那种
  更严格读法时，用 `and` 组合 `of:"team_own"` 与 `of:"team_opp"` 两条即可表达）。
- **与 `team_counter` 的区别**：`element:<系别>` 是**累计**计数且从不清零（`使用草系技能后…` 这类
  「本场累计 N 次」用它）；`last_turn_had_element` 只描述**上一回合**，上一回合没用过就一定是假。
  两者都在回合末更新，唯一时机差别是后者每回合被覆盖重写。
- **本回合内不会变化**：寄存器在回合末写入，因此「本回合使用过 X 系技能」这类条件用不到它
  （本回合内已用过的技能读 `team_counter` 或 `ctx.element_self`）。
- 读明细也可直接用 Query：`{"q": "last_turn_element", "of": "team_both", "name": "水"}`
  （= 上一回合双方水系技能使用次数，见 §2）。

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

**`HAVE_EVAL`**（`backend/vm/cond.py:266`）
| `trait_path` | `path`, `op`, `value` | `_eval_trait_path()` |

**`have` 的 `what` 合法值**（定义于 `backend/vm/cond.py:HAVE_EVAL`，266 行起）：

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

`CONDITION_TRIGGERS`（`backend/vm/cond.py:157`）定义每个条件的触发点，用于自动推导 Observer 的 `listen` 字段。`infer_triggers()` 函数递归遍历条件树：
- `or`: 并集（任一子条件可能独立匹配）
- `and`: 并集（在所有子条件关注的触发点都检查）
- `not`: 内部条件的触发点（取反不改变触发时机）
- 纯状态类条件（`last_turn_had_element`、`have`、`is_mixed_blood` 等）没有专属事件，
  默认挂 `post_entry` / `post_skill` / `turn_start`（数据写 `listen` 时按需要收窄即可）。

---

## 六、Observer 模型

特性的 JSON 存储格式为 `effects[]`（少数旧特性使用 `triggers[]`），由 `TraitToObserver` 编译器在加载时转换为 Observer 对象。**运行时只有 Observer。**

- **编译入口**: `backend/engine/trait_loader.py:TraitLoader.load_for_sprite()`
- **注册**: `ObserverRegistry.register()` — Observer 按 `listen` 钩子点分组注册
- **触发**: `backend/engine/battle.py` — 事件发生时 fire 对应 hook → 遍历 Observer → cond 求值 → Skill VM 执行 `then[]`

**关键点**: `Observer.then` 就是 Skill IR opcode 数组，引擎执行时走同一条 `executor.py` 路径，不区分来源。

### 触发点 (hook)

完整定义见 `backend/engine/observer.py:TRIGGER_POINTS`。

| hook | 说明 |
|------|------|
| `pre_calc` | 技能结算前（Ctx 刚构建） |
| `pre_modifier` | L0→L1：技能修饰符计算前 |
| `pre_defend` | L1→L2：受伤结算前（防守方特性） |
| `pre_resolve` | **行动顺序确定后、行动结算前**（`is_first` / `is_second` 在此可用；先后手条件效果挂这里） |
| `post_entry` | 精灵入场 |
| `post_leave` | 精灵离场（Observer 注销） |
| `post_enemy_leave` | 敌方离场+新精灵入场（ctx 为我方视角） |
| `post_skill` | 技能执行后 |
| `post_damage` | 受到伤害后 |
| `post_switch` | 精灵切换后 |
| `post_counter` | 应对成功后 |
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

### 授予型机制的落地与清理（`aura` / `element_convert` / `morph` / `grant_choice`）

这四类 op 的 mutation 由 `JournalReplayer` 落到**引擎侧服务**（`backend/engine/auras.py`、`backend/engine/morph.py`），
再由 `Battle` 在通用时机消费：

| 机制 | 落点 | 消费时机 | 清理 |
|------|------|----------|------|
| `aura` | `sprite.active_effects` 中的 `AuraEffect` + `sprite.counters["aura:<source>:<stat>"]` 追踪已应用步数 | 回合开始、每次行动后、每次换人后（`auras.refresh()` 幂等重算） | 随 scope 清除效果，`Sprite.clear_effects()` 同步把追踪计数归零 |
| `element_convert` | `BattleSkill._element_override` | Ctx 构建（伤害/克制计算读生效属性） | 同上，非在场时撤销覆写 |
| `morph` | `BattleSkill._morph_source`（巧变：授予型） | 技能结算完成后（`morph.apply_after_use()`） | 同上清空 `_morph_source`（已变化的技能槽保留，直到再次变化） |
| `morph`（变身） | `BattleSkill.replaced_by`（技能顶层 `morph` 字段，非授予） | **回合开始**（`morph.apply_henshin()`，见「变身」小节） | 不清理：每回合重掷覆盖 |
| `grant_choice` | `BattleSkill._granted_choices` / 精灵级聚能分支 | 行动选择与分支解析（`_execute_skill_vm`） | 同上 |

> 这些都是**引擎通用能力**：新增同类特性只需写数据，不需要改引擎。
> 若需要新的计数源或新的巧变池类别，各自注册一个函数即可（见 §三 3A `aura` / 3E `morph`）。


### 引擎钩子（非 IR，引擎层拦截）

需要在**伤害/能量落地前**改写引擎行为的特性，不走 Skill VM。机制：
`backend/sim/traits/trait_engine.py:register_hook(name, callback, trait_name)`，
回调实现放在 `backend/sim/traits/_hooks.py`（由 `sim/traits/__init__.py` import 完成注册）。

| hook 点 | 触发位置 | 已注册特性 |
|---------|---------|-----------|
| `on_fatal_damage` | `engine/replayer.py:_apply_damage` 伤害落地前；回调返回 True 表示已拦截 | 不死鸟（每场1次：锁1血 + 敌方15层灼烧） |
| `post_entry` | `sim/traits/__init__.py:dispatch_entry` | 无数据注册（保留扩展点） |
| `after_transmission` | `backend/sim/pipeline.py` | 无数据注册（保留扩展点） |
| `turn_end_bench_check` | `backend/sim/battle.py` 回合末 | 星地善良（引擎内联查找，非 hook 回调） |

> 其余引擎特化（不经过 hook 机制，按特性名内联）：
> - `石头大餐/盛宴`：`blood_price` 修正值在能量 Gate 内联消费（`sim/battle.py`）
> - `焰色反应`：`sim/resolver.py` 灼烧衰减分支内联
> - `瞳中倒影`：`sim/battle_mechanics.py` 换宠流程内联
> - `盘根木`：`dispatch_entry` 首次入场 HP 10% 内联

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
| `feeds` | `str` | 拓扑排序 token | — | 技能 |
| `needs` | `str` | 拓扑排序 token | — | 技能 |
| `cooldown` | `int` | 冷却（次） | `0` | 技能 |
| `on_next` | `bool` | 延迟到"下一次"生效（**「延后生效」的唯一表示**；旧的 `delay` 字段已删除，回合末生效用 `defer{turns,at,then}`） | `false` | power_mod |
| `if_type` | `str` | 配合 `on_next`，限定技能类型 | — | power_mod |
| `mode` | `str` | `"set"` (默认) / `"add"` / `"multiply"` | `"set"` | mult_mod/power_mod |

### 「选择」分支（choices）

技能顶层可声明 `"choices": [{"name": "明", "cond": {...}?, "effects": [...]}]`（S2–S4 的「选择：A或B」机制）：

- 有 `choices` 时，`effects[]` 仅为共性兜底；结算使用所分支的 `effects`
- 分支可带 `cond`（如"应对状态时"）——结算时条件成立则自动生效；动作指定该分支但条件不成立时回退到第一个无条件分支
- **分支 `cond` 的求值 ctx**（`battle._execute_skill_vm`）带上本次应对的瞬时标志
  （`counter_succeeded` / `was_countered`）与对手技能（`opp_is_attack` 可读），
  否则「应对状态时…」这类分支（驱赶/撒花/透镜实验）永远判不成立而被强制回退到 0 号分支
- 引擎按 `Action.branch` 选择分支；未指定时取 0 号分支（条件分支自动裁决）
- 分支使用情况写入队伍计数器 `choice_used:{分支名}`；同一技能明暗各用 1 次计 1 组 `choice_pair`（猫精灵的礼物）
- `replay_branch` op 可重放本次分支的另一支/相同一支（有求必应/一意孤行）
- 编译：`SkillCompiler._compile_choices()` 对每个分支跑完整 pass 管线（含 HitOp 注入）；动作序列化 `>>>ACTION` 行尾以 `@分支名` 标注

### 技能 body 字段

| 字段 | 含义 | 默认 |
|------|------|------|
| `element` | 系别（支持 Query；修饰符 element 过滤支持 `"!幻"` 排除语法） | `"普通"` |
| `tag` | 机制标签（`"迅捷"` / `"传动"`） | 无 |
| `combo` | **基础释放次数**（游戏描述「N连击」）。**写了这个键 = 该技能带「连击词条」**，`"combo": 1` 也算——原文「1连击」的技能（追打/乘胜追击/音波弹/落石/啃咬/多维击打）就是靠它吃连击加成；不写键 = 无词条、恒 1 次、不吃精灵级连击增益 | 无键（=无词条，1 次） |
| `use_devotion` | 触发队伍奉献 | `false` |
| `usable_while_charging` | 蓄力中可用 | `false` |
| `transmission` | 传动等级：`-1` = **主轴（位置不参与传动，= 旧 `position_locked` 的语义）** / `0` = 普通 / `1+` = 传动等级。旧字段 `main_axis` 已于 2026-09-22 删除（主轴只由本字段表示） | `0` |
| `morph` | **变身**：`{"from": "team_own", "mode": "random", "exclude_self": true, "exclude_owned": true, "energy_delta": -2}` — **每回合开始时**把该槽位替换为池中随机技能（借用/复写）。与「巧变」（`qiaobian`）不是同一机制，见下「变身」小节 | 无 |
| `passive` | 被动效果数组 | `[]` |
| `counter` | 应对类型：`"攻击"` / `"防御"` / `"状态"` | 无 |

### 连击（combo）语义

`combo` 描述的是**技能释放次数**，与技能描述中其余文本不在同一层级：其余文本描述的是**每一次释放**的效果。

| 规则 | 说明 |
|------|------|
| 释放次数 | 有效连击数 `N = 技能 combo 字段 + 技能自身连击修正 + （门控后的）精灵级连击增益`，再乘 `combo_mult`，恒 ≥1。`N` 同时是**独立命中次数**（见「伤害结算」行） |
| 效果按次结算 | 技能的所有效果（stat_stage / power_mod / mult_mod / heal / abnormal / mark）**一律按每次释放各结算一次**。没有数据 flag：由 op 与 target 决定（见下一行），避免「数据声明」与「引擎规则」两套判据打架（旧字段 `per_hit` 已删除） |
| 自技能参数只算一次 | 修改**自己本技能参数**的 `power_mod`/`mult_mod`（`target: "skill_off_0"`，attr ∈ power/power_mult/energy_cost/priority/combo/combo_set/combo_mult/use_count_bonus）整次使用**只结算一次**——连击数是释放次数，这些参数描述的是「单次释放」的威力/能耗/段数（如引雷「迸发：本次技能威力+20」是 2 段各 55 威力，不是 55+75） |
| 连击增益门控 | `combo`/`combo_set`/`combo_mult` 的**精灵级**修正（`target: "sprite_self"` / `"sprite_opp"`，如暴风眼「连击数+100%」、耀眼「敌方连击数-4」）**只对带连击词条的技能生效**。词条判据 = **技能 JSON 写了 `combo` 键**（值可以是 1，即原文「1连击」）；技能自身的连击文本（`target: "skill_off_0"`、`skill.<技能名>.combo` 永久修正、队伍奉献）不受门控。落地：`sim.skill.Skill.combo_keyword` ← `'combo' in data`（两个加载器都写），`BattleSkill.combo_keyword` 委托它，`engine/snapshot.py`/`snapshot_cy.pyx` 据此门控 |
| 伤害结算 | **连击 = N 次独立命中**（2026-09-22 落地）：`op_hit` 只算**单段**伤害（`combo_count=1`，各自 `round`、各自 `max(1, …)`、各自套属性克制/减伤/天气/应对），段数写进 `Damage.hits`；同回合连击修正（`combo`/`combo_set`/`combo_mult`）**改写段数**而不是折进伤害值，最后由 `engine.modifiers.expand_combo_hits`（在 `JournalReplayer.replay` 入口统一展开）变成 N 条独立 `Damage`。旧口径「单次伤害事件 × N」已废弃（只在末尾取整一次、且只触发一次受击结算） |
| 受击类钩子 | 观察者钩子（`post_damage` 系：扎手/诅咒/微型斥候/绞轮/排气）**每次技能攻击只触发一次，不含连击段数**——数据原文写「每受到1次技能攻击（**不含连击**）」。技能自身的「每次连击」效果（连续毒针加中毒、星链加印记、三连破加属性）走 op 展开（`op_abnormal`/`op_mark`/`op_stat_stage` 按 `ctx.combo_self` 复制 mutation），与 N 条伤害事件**同时**按段生效 |
| `combo_mult` 口径 | **加成分数**：`1` = 「+100%」（×2），读取方一律 `round(段数 × (1 + combo_mult))`。精灵级（暴风眼）与日志级（灵光「本次连击数翻倍」，`power_mod attr:"combo_mult" delta:1`）同一口径；`add` 通道基准是 `0.0`（不是 ratio 通道的 1.0）——基准写错会把 +100% 读成 ×3（2026-09-22 修）。此前日志级 `combo_mult` **没有任何读取方**，灵光的翻倍整条静默失效 |
| 预测口径 | `sim/resolver.calc_damage`（AI 估伤）与引擎同口径：`combo` 字段 + 技能修正 + 门控后的精灵级增益与倍率；并逐项对齐实战输入——技能级/精灵级 `power_mult`/`damage_mult` 按 `1+(精灵级-1)+(技能级-1)` 相加、减伤取**防御方**精灵级、技能自身写在 `effects[]` 里的同回合 `power_mod`/`mult_mod` 按 `engine/modifiers.adjust_damage` 的次序**先算伤害再后乘取整**（`power_add` 折成 `(power+add)/power`，**保留浮点**）；段数同理：单段值算完取整后再乘同回合修正后的段数（`combo_base_count` → 同回合 `combo`/`combo_set` → `×(1+combo_mult)`）。另外四种「本次使用打不出来」一律估 0：**蓄力未完成**（`_would_charge`）、**重定向到自己**（`_would_redirect_to_self`，灾厄）、**付不起能耗**（引擎 `can_pay_skill_energy_cost`）、**技能不存在**。`choices` 分支按引擎规则镜像（分支 0；cond 不成立 → 第一个无条件分支）。同回合修正的 Ctx 建在**支付后**状态上（引擎先付能耗再执行 effects）。`when` 条件（含 `skill_at`）用引擎自己的 `vm/cond.compile_cond` 求值，求不出就不计（保守） |
| 预测层口径核验（2026-09-22） | 全库 595 技能（365 个攻击技能）中性对局逐技能对拍：**不一致 0 条**（此前 15 条：蓄力 4、choices 6、支付次序 1、取整 1、redirect 1、不可支付 1、命中描述 1）。回归测试见 `backend/tests/test_charge_semantics.py` 与审计报告（对账文档 §17） |
| 行动合法性（唯一判据） | `sim/battle_mechanics.Battle.action_legality(team, action) → ActionCheck(ok, status, code, turn_consumed)`：引擎门控（`_execute_skill_vm` / `_resolve_switch`）、动作掩码（`ai/core/mcts.get_valid_actions`）、规则 agent 的候选过滤（`_attack_table`）**共用这一份**。`status`：`ok` / `illegal` / `state_skip`（眩晕等：合法但被吃掉，照规则消耗回合）。**选招循环**（`Battle._select_action`）：`illegal` → **拒绝、不推进回合、重新征询 agent**（上限 8 次，超限用掩码兜底 `_first_legal_action`），被拒的尝试记进 `ActionRecord.rejected`；`state_skip` 不重选。回合记录字段见 `RoundRecord`/`ActionRecord`（`status`/`code`/`turn_consumed`/`rejected`，随 `round_record_to_dict` 序列化）。门禁测试：`backend/tests/test_action_legality.py`（掩码合法集 == 引擎判据合法集，逐决策点核对 + 拒收/兜底/记账） |
| `Damage.hits` | `vm/journal.py` 的 Damage 字段：`0` = 单次结算（自伤/反噬/特性追加，不参与段数改写）；`>=1` = 本次命中的段数（`op_hit`/借用伤害写 `ctx.combo_self`，`adjust_damage` 按同回合修正改写）。`amount` 一律是单段伤害 |

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

#### 砂糖弹球 (体重差档位表 — `when`/`else_if` 链 + 派生查询 + `abs`)

来源: `data/skills/砂糖弹球.json`（档位表来自 nrc 技能 `effect_details`，即游戏内「体重差与威力」工具提示档位表）

```json
{
  "name": "砂糖弹球", "power": 20,
  "effects": [
    { "when": { "cond": "compare", "q": "weight_diff", "of": "sprite_self",
                "abs": true, "op": "lt", "value": 4 },
      "then": [ { "op": "power_mod", "target": "skill_off_0", "attr": "power",
                  "mode": "set", "delta": 0 } ],
      "else_if": [
        { "when": { "cond": "compare", "q": "weight_diff", "abs": true, "op": "lt", "value": 14 },
          "then": [ { "op": "power_mod", "target": "skill_off_0", "attr": "power",
                      "mode": "set", "delta": 20 } ] }
      ],
      "else": [ { "op": "power_mod", "target": "skill_off_0", "attr": "power",
                  "mode": "set", "delta": 100 } ] }
  ]
}
```

**档位**（取 `|weight_diff|`，区间左闭右开，最后一段含等号）：`0–4`→20 / `4–14`→40 /
`14–30`→60 / `30–60`→80 / `60–120`→100 / `≥120`→120。
`mode:"set"` 设的是**增量**，故 `delta = 档位 − 基础威力(20)`。

**涉及 opcode**: `when`/`else_if`/`else` 条件链, `compare` + 派生查询 `weight_diff`（`abs` 取绝对值）,
`power_mod` `mode:"set"`
