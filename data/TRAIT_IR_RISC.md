# 特性 IR — RISC 基元设计

## 设计原则

特性 = 持久化条件监听器（Observer）。入场时注册，事件发生时 eval，命中则执行。

```
特性（描述层） → 触发器组合（IR层） → 引擎执行（观察者模型）
```

特性与技能共享底层 opcode、条件表、值表达式，但执行模型不同：

| 维度 | 技能 | 特性 |
|------|------|------|
| 触发 | 玩家选择行动 | 事件钩子（17个hook点） |
| 生命周期 | 瞬时（单次使用） | 持久（跨回合，scope控制） |
| 核心机制 | effects数组顺序执行 | Observer = cond + then + scope |
| 条件 | COND_EVAL命名条件 | COND_EVAL + PathCond路径条件 |
| 独有opcode | charge/borrow/replay | mutate_effect/schedule/transform等 |

---

## 三层模型

| 层 | 对应 | 职责 |
|----|------|------|
| 描述层 | 高级语言 | "入场时双攻+30%" |
| **IR层** | **指令集** | **hook点 + 条件 + effects 组合** |
| 引擎层 | 观察者引擎 | 注册Observer → 事件触发 → eval → 执行 |

---

## 执行模型 — 观察者 + 寄存器

### Observer 模型

每个触发器编译为一个 Observer：

```
Observer = { cond, then, scope }
  cond:   何时触发（条件表达式）
  then:   触发后执行什么（effects 数组）
  scope:  存活多久（battlefield / persistent / permanent）
```

引擎在13个触发点遍历所有 Observer，条件命中则执行 then-block：

```
回合流程:
  1. 入场 → fire("post_entry")    → Observer 注册
  2. 选技 → fire("pre_calc")      → L0 修饰器注入
  3. 伤害 → fire("post_damage")   → 受击反应
  4. 技能后 → fire("post_skill")  → 技能后效果
  5. 切换 → fire("post_switch")   → 离场/入场反应
  6. KO   → fire("post_ko")       → 击倒反应
  7. 回合末 → fire("turn_end")    → 回合末结算
  ... 等13个触发点
```

### Ctx 寄存器组（与技能共用）

特性与技能共用同一个 `Ctx` 寄存器组（99字段，64条目 ADDRESS_MAP）。查询表达式 `{"q": "energy", "of": "sprite_self"}` 通过 ADDRESS_MAP 映射到 `ctx.energy_self`。

完整寄存器定义见 `data/SKILL_IR_RISC.md` 第27-125行。

### trait_ctx 补充字段

部分特性需要预计算的队伍级聚合值，由引擎在触发前注入 `trait_ctx`：

| 字段 | 含义 | 示例 |
|------|------|------|
| `player_fainted_count` | 己方力竭数 | 悼亡 |
| `opponent_fainted_count` | 敌方力竭数 | 悼亡 |
| `player_bug_count` | 己方虫系精灵数 | 虫群鼓舞 |
| `team_elements` | 队伍系别集合 | 虫群鼓舞 |
| `team_counters[key]` | 队伍级计数器 | 慢热型 |
| `self_total_energy_cost` | 自己全技能能耗之和 | — |
| `enemy_total_energy_cost` | 敌方全技能能耗之和 | — |
| `enemy_unique_elements` | 敌方队伍系别种类数 | — |

### 触发点（13个）

```python
TRIGGER_POINTS = {
    "pre_calc",              # VM执行前（Ctx刚构建）
    "post_skill",            # 技能效果应用后
    "post_damage",           # 受到伤害后
    "post_switch",           # 精灵切换后
    "post_entry",            # 精灵入场后
    "post_abnormal_tick",    # 异常tick伤害后
    "post_abnormal_change",  # 异常层数变化后
    "post_abnormal_apply",   # 异常主动施加后
    "post_ko",               # 精灵力竭后
    "post_energy_change",    # 能量变化后
    "post_positive_change",  # 增益数量变化后
    "turn_end",              # 回合末结算
}
```

---

## 一、值表达式 — `value`

特性使用三种值表达式，与技能共用 `Literal` 和 `Query`，外加特性专用的 `RefExpr`。

### Literal — 字面量

```jsonc
3                    // 整数
0.5                  // 小数
"火"                 // 字符串
true                 // 布尔
```

### Query — 寄存器查询（与技能共用 ADDRESS_MAP）

```jsonc
// 己方能量
{"q": "energy", "of": "sprite_self"}

// 敌方当前HP比例
{"q": "hp_ratio", "of": "sprite_opp"}

// 己方队伍印记总层数
{"q": "mark_count", "of": "team_own"}

// 敌方力竭数
{"q": "fainted", "of": "team_opp"}

// 对方当前技能系别
{"q": "element", "of": "skill_opp_current"}

// 带 scale / offset / per
{"q": "hp_missing_ratio", "of": "sprite_self", "per": 0.1}   // 每损失10%HP算1
{"q": "energy", "of": "sprite_opp", "scale": 10}              // 敌方能量×10
{"q": "weather", "of": "battle", "default": "普通"}           // 天气系别，无天气回退
```

完整 Query 表达式参考 `data/SKILL_IR_RISC.md` 第233-278行。

### RefExpr — 路径表达式（特性专用）

特性中需要引用精灵/队伍的内部状态时使用 `=@` 前缀的路径表达式：

```jsonc
// 己方能量 × 2
"=@self.energy * 2"

// 己方灼烧层数
"=@self.effects[name=灼烧].stacks"

// 己方力竭数 × 3 + 敌方力竭数 × 3
"=@player_fainted_count * 3 + opponent_fainted_count * 3"

// 队伍计数器
"=@team_counters[counter_success] * 5"
```

**编译规则：**

- 单 `=@root.path * N + M` → 编译为 `RefExpr(root, path, multiplier, offset)`，O(1) 解析
- 多 `@` 引用或跨引用运算 → 保留为 Literal 字符串，运行时 eval
- 不含 `=@` 前缀 → 视为 Literal 字面量

**RefExpr root 合法值：**

| root | 含义 | 示例 |
|------|------|------|
| `self` | 当前己方精灵 | `self.energy`, `self.effects[name=灼烧].stacks` |
| `target` | 当前目标精灵 | `target.hp_ratio` |
| `attacker` | 攻击来源精灵 | `attacker.species.elements` |
| `player` | 己方队伍状态 | `player_fainted_count` |
| `opponent` | 敌方队伍状态 | `opponent_fainted_count` |
| `battle` | 全局战场 | `battle.turn` |
| `team_counters` | 队伍计数器（需 `[key]` 索引） | `team_counters[counter_success]` |

**路径语法：**

```
root.field1.field2[filter].field3
```
- `.` 分隔层级
- `[name=灼烧]` / `[element=火]` 过滤数组/字典元素
- `[counter_success]` 索引队伍计数器

---

## 二、Hook 点 — `on`

`TraitHandler` 基类的 19 个钩子方法映射到 IR 的 `on` 字段：

### 入场/离场

| Hook | `on` 值 | 说明 |
|------|---------|------|
| `on_entry` | `"entry"` | 精灵入场时触发 |
| `on_leave` | `"leave"` | 精灵离场时触发（含切换和力竭） |
| `on_enemy_leave` | `"enemy_leave"` | 敌方精灵离场时触发 |

### 回合边界

| Hook | `on` 值 | 说明 |
|------|---------|------|
| `on_turn_start` | `"turn_start"` | 回合开始时触发 |
| `on_turn_end` | `"turn_end"` | 回合结束时触发 |

### 技能管线

| Hook | `on` 值 | 说明 |
|------|---------|------|
| `on_modifier` | `"modifier"` | L0→L1：修改技能参数（power_mult/energy_cost等） |
| `on_damage` | `"damage"` | L1→L2：影响伤害计算 |
| `on_defend` | `"defend"` | L1→L2：防御方减伤（偏振/绝对秩序等） |
| `on_skill_use` | `"skill_use"` | 技能执行完毕后触发 |
| `on_counter_success` | `"counter_success"` | 应对/打断成功后触发 |

### 伤害/力竭

| Hook | `on` 值 | 说明 |
|------|---------|------|
| `on_take_damage` | `"take_damage"` | 受到攻击伤害后触发 |
| `on_fatal_damage` | `"fatal_damage"` | 受到致命伤害前触发（返回True=免疫） |
| `on_before_take_damage` | `"before_damage"` | 伤害拦截（返回0=免疫，<0=吸收，>0=修正） |
| `on_ko_enemy` | `"ko_enemy"` | 主动击败敌方后触发 |
| `on_faint` | `"faint"` | 自身力竭时触发 |

### 事件触发

| Hook | `on` 值 | 说明 |
|------|---------|------|
| `on_energy_change` | `"energy_change"` | 能量增减后触发 |
| `on_gain_effect` | `"gain_effect"` | 获得增益/减益/异常时触发 |
| `on_inflict` | `"inflict"` | 对敌方施加效果时触发 |
| `on_abnormal_tick` | `"abnormal_tick"` | 异常回合末扣血时触发 |
| `on_before_action` | `"before_action"` | 行动选择后修改/否决 |
| `on_energy_short` | (无IR对应) | 能量不足时的HP替代（引擎层处理） |

### Aura 展开

```
"aura": { "name": "...", "effects": [...], "target": "..." }
       ↓ 编译器展开
"on": "entry"  → 应用效果
"on": "leave"  → remove_effect 清除
```

---

## 三、指令集

### 3A. 共享操作码（与技能 VM 共用）

以下操作码与技能 IR 语义完全相同，参考 `data/SKILL_IR_RISC.md` 获取完整参数说明：

#### `mod` — 属性修正

```jsonc
// 入场双攻 +30%（永久）
{"kind": "stat", "stat": "atk", "steps": 3, "scope": "permanent", "source": "不移"}
{"kind": "stat", "stat": "sp_atk", "steps": 3, "scope": "permanent", "source": "不移"}

// 全技能能耗 -3（永久）
{"kind": "stat", "stat": "energy_cost", "steps": -3, "scope": "permanent", "source": "珊瑚骨"}

// 动态值：己方虫系精灵数 = 步数
{"kind": "stat", "stat": "atk", "steps": "=@player_bug_count", "scope": "battlefield", "source": "虫群鼓舞"}

// use_modifiers 方式：防御时减伤
{"use_modifiers": {"damage_mult": {"op": "mult", "value": 0.5}}}
```

特性中 `mod` 支持的 `stat` 值（与技能共用）：

`atk` `def` `sp_atk` `sp_def` `speed` `power` `priority` `energy_cost` `energy_cost_mult` `combo` `combo_mult` `hp` `energy` `damage_mult` `damage_reduction` `power_mult` `life_drain` `devotion` `pre_charged` `ignore_mods` `ignore_resistance` `cooldown` `life_as_energy` `survive` `extra_action` `extra_turn_end` `heal_reverse` `immune` `drive`

特性中 `mod` 的 `scope` 值：

| scope | 含义 |
|-------|------|
| `"battlefield"` | 在场有效，离场清除（默认） |
| `"persistent"` | 跨回合持久，受 ttl 控制 |
| `"permanent"` | 永久生效，不受离场影响 |

特性中 `mod` 的 `skill_filter` 值（与技能共用）：

`"attack"` `"defense"` `"status"` `"all"` `"others"` `"adjacent"` `"bare_attack"` `"bare_defense"` `"bare_status"`

#### 其他共享操作码

```jsonc
// mark — 印记
{"kind": "mark", "name": "灼烧印记", "stacks": 3, "mark_target": "opp_team"}

// abnormal — 异常
{"kind": "abnormal", "target": "opp", "name": "中毒", "stacks": 5, "scope": "battlefield"}

// weather — 天气
{"kind": "weather", "weather": "snow", "turns": 8}

// dispel — 驱散
{"op": "dispel", "target": "sprite_opp", "what": "positive"}
{"op": "dispel", "target": "team_both", "what": "mark", "name": "灼烧印记"}

// steal — 偷取
{"op": "steal", "target": "sprite_opp", "what": "positive"}

// tick — 异常结算
{"op": "tick", "target": "sprite_opp", "name": "灼烧"}

// double — 翻倍
{"op": "double", "target": "sprite_self", "what": "positive"}

// escape / return / lock / interrupt / hit / exchange / reset / redirect / replay / borrow
// 以上操作码在特性中较少使用，但语法与技能完全一致
// 详见 data/SKILL_IR_RISC.md
```

### 3B. 特性独有操作码

这些操作码只在特性系统中使用，技能 IR 中没有。

#### `mutate_effect` — 修改现有效果

修改目标精灵已有 effect 的层数或步数。

```jsonc
// 萌化层数 +1
{"kind": "mutate_effect", "target": "self", "filter": {"name": "萌化"}, "delta_stacks": 1}

// 灼烧层数翻倍（delta_stacks = 当前值）
{"kind": "mutate_effect", "target": "opp", "filter": {"name": "灼烧"}, "delta_stacks": "=@self.effects[name=灼烧].stacks"}
```

| 字段 | 含义 | 必填 |
|------|------|------|
| `target` | `self` / `opp` | 是 |
| `filter` | 筛选条件 `{"name": "萌化"}` / `{"kind": "abnormal"}` | 是 |
| `delta_steps` | 步数变化量（可为负，支持 RefExpr） | 否 |
| `delta_stacks` | 层数变化量（可为负，支持 RefExpr） | 否 |

#### `remove_effect` — 按来源清除效果

Aura 离场时自动生成，清除入场时施加的效果。

```jsonc
// aura 离场：清除自己施加的 stat 效果
{"kind": "remove_effect", "source": "凛冬之翼", "target": "opponent_active"}
```

| 字段 | 含义 |
|------|------|
| `source` | 效果来源名（匹配 effect.source） |
| `target` | 从哪个目标清除 |

#### `battleskill_mut` — 修改技能对象属性

直接修改 BattleSkill 对象的字段（不是通过 modifier 管线）。

```jsonc
// 下次攻击技能威力 × 1.5
{"kind": "battleskill_mut", "field": "next_attack_mult", "value": 1.5}

// 技能系别改为光
{"kind": "battleskill_mut", "field": "element", "value": "光", "op": "set"}

// 技能能耗 -1
{"kind": "battleskill_mut", "field": "energy_cost", "value": -1, "op": "add"}
```

| 字段 | 含义 |
|------|------|
| `filter` | 筛选哪些技能（`skill_filter` / `element` / `name`） |
| `field` | 要修改的字段名 |
| `value` | 新值 / 变化量 |
| `op` | `"set"`（默认）/ `"add"` |
| `target` | `"all"`（默认）= 所有匹配技能 / `"current"` = 仅当前技能 |

#### `use_modifier` — 修改 SkillUse.modifiers

在 modifier 管线的 L0 阶段注入修饰器。

```jsonc
// 防御时伤害倍率 × 0.5
"use_modifiers": {
  "damage_mult": {"op": "mult", "value": 0.5}
}

// 威力倍率 +30%
"use_modifiers": {
  "power_mult": {"op": "add", "value": 0.3}
}

// 连击数设为 3
"use_modifiers": {
  "multi_hit": {"op": "set", "value": 3}
}
```

| key | 含义 |
|-----|------|
| `power_mult` | 威力倍率（叠加） |
| `damage_mult` | 伤害倍率（叠加） |
| `damage_reduction` | 减伤比例 |
| `multi_hit` | 连击数 |
| `ignore_mods` | 忽略属性修正 |
| `priority_mod` | 优先级修正 |

每个 key 的 value 是一个 `{"op": "set"/"add"/"mult", "value": ...}` 对象。

#### `action_modifier` — 修改可用行动

```jsonc
// 禁止使用2号位技能
{"kind": "action_modifier", "action": "forbid_skill", "slot": 2}

// 限制只能使用1号和3号位技能
{"kind": "action_modifier", "action": "restrict_slots", "slots": [1, 3]}
```

| 字段 | 含义 |
|------|------|
| `action` | `"forbid_skill"` / `"restrict_slots"` / `"seal_all_but"` |
| `slot` | 单个槽位号（1-4） |
| `slots` | 多个槽位号 |
| `force` | 强制使用的技能名 |

#### `schedule` — 延迟效果

```jsonc
// 3回合后复活
{"kind": "schedule", "turns": 3, "phase": "start",
 "effects": [{"kind": "special", "name": "revive", "amount": 1}]}
```

| 字段 | 含义 |
|------|------|
| `turns` | 延迟回合数 |
| `phase` | `"start"`（回合开始结算）/ `"end"`（回合结束结算） |
| `effects` | 延迟后执行的效果列表 |

#### `inherit_effects` — 效果继承

离场时将自身效果传递给下一个入场精灵。

```jsonc
// 离场时将所有 battlefield 效果传递给下一个己方入场精灵
{"kind": "inherit_effects", "scope": "battlefield", "source_sprite": "self", "target": "ally_new"}

// 通过 pending_effects 机制传递
{"kind": "inherit_effects", "via_pending": true, "target": "enemy_new"}
```

| 字段 | 含义 |
|------|------|
| `scope` | 继承哪些效果：`"battlefield"` / `"persistent"` |
| `source_sprite` | 来源精灵 |
| `target` | 目标：`"ally_new"` / `"enemy_new"` |
| `via_pending` | 是否通过 pending_effects 传递 |

#### `team_counter` — 队伍级计数器

```jsonc
// 己方队伍 "counter_success" 计数 +1
{"kind": "team_counter", "key": "counter_success", "delta": 1, "target_team": "own"}
```

| 字段 | 含义 |
|------|------|
| `key` | 计数器名称 |
| `delta` | 变化量（默认 +1） |
| `target_team` | `"own"` / `"opp"` |

#### `transform` — 形态变换

```jsonc
// 变换为"岚鸟-暴风形态"
{"kind": "transform", "species": "岚鸟-暴风形态", "skills": ["暴风眼", "风之翼", "龙卷风", "气流斩"], "reset_hp": false, "reset_energy": false}
```

#### `trait_interaction` — 特性交互

```jsonc
// 压制目标特性
{"kind": "trait_interaction", "action": "suppress", "target": "opponent_active"}

// 移除目标特性
{"kind": "trait_interaction", "action": "remove", "target": "opponent_active"}

// 复制目标特性
{"kind": "trait_interaction", "action": "copy", "target": "opponent_active", "copy_from": "opponent_active"}
```

| 字段 | 含义 |
|------|------|
| `action` | `"suppress"` / `"remove"` / `"copy"` / `"replace"` |
| `target` | 操作目标 |
| `copy_from` | 复制来源（仅 `action: "copy"`） |
| `new_ability` | 新特性名（仅 `action: "replace"`） |

#### `lives` — 修改队伍魔力值

```jsonc
// 己方魔力值 -1
{"kind": "lives", "delta": -1, "target_team": "own"}
```

### 3C. `special` — 特殊效果

```jsonc
// 能量置零
{"kind": "special", "name": "energy_set", "amount": 0}

// 回复能量（动态值）
{"kind": "special", "name": "gain_energy", "amount": "=@team_counters[counter_success] * 5"}
```

| name | 说明 |
|------|------|
| `energy_set` | 能量设为指定值 |
| `gain_energy` | 能量 +amount |
| `revive` | 复活（amount=复活后HP比例） |
| `remove_all_effects` | 清除全部效果 |

---

## 四、条件系统

特性使用两套条件体系：**命名条件**（与技能共用）和**路径条件**（特性专用）。

### 4A. 命名条件（COND_EVAL，与技能共用）

直接在 `condition` 字段中使用，与技能 `when.cond` 共享相同的 COND_EVAL 调度表：

```jsonc
// 入场条件：队伍中有虫系精灵
{"condition": {"cond": "have_skill_of", "of": "sprite_self", "element": "虫", "exclude_self": true}}

// 仅在敌方换宠时触发
{"condition": {"cond": "opp_switched"}}

// HP低于50%时
{"condition": {"cond": "hp_below", "ratio": 0.5}}

// 组合条件
{"condition": {"cond": "and", "conditions": [
  {"cond": "counter_succeeded"},
  {"cond": "on_damage_taken"}
]}}
```

完整 cond 合法值见 `data/SKILL_IR_RISC.md` 第718行。

### 4B. 路径条件（特性专用）

通过对象图路径反射访问精灵/技能内部状态：

```jsonc
// 技能系别 == 草
{"condition": {"path": "skill.element", "op": "eq", "value": "草"}}

// 技能系别不在攻击者的系别列表中
{"condition": {"path": "skill.element", "op": "not_in", "value": "=@attacker.species.elements"}}

// 己方有灼烧效果
{"condition": {"path": "self.effects[name=灼烧].exists", "op": "eq", "value": true}}

// 队伍系别中包含 "虫"
{"condition": {"path": "team_elements", "op": "contains", "value": "虫"}}
```

**路径语法：** `对象.属性[过滤].属性[过滤]...`

| 路径头 | 指向 |
|--------|------|
| `self` | 特性持有者精灵 |
| `target` | 目标精灵（对手/受击者） |
| `attacker` | 攻击来源精灵 |
| `skill` | 当前技能 |
| `team_elements` | 队伍系别集合 |
| `effect_name` | 触发事件的效果名 |

**op 合法值：**

| op | 含义 |
|----|------|
| `eq` / `=` / `==` | 等于 |
| `neq` / `!=` | 不等于 |
| `gt` / `>` | 大于 |
| `gte` / `>=` | 大于等于 |
| `lt` / `<` | 小于 |
| `lte` / `<=` | 小于等于 |
| `in` | 在列表中 |
| `not_in` | 不在列表中 |
| `contains` | 列表包含 |

**value 支持 RefExpr：**

```jsonc
"value": "=@attacker.species.elements"   // 动态值
"value": "草"                              // 静态值
"value": true                              // 布尔
```

### 4C. 逻辑组合

```jsonc
// AND
{"condition": {"kind": "and", "conditions": [
  {"path": "skill.element", "op": "eq", "value": "火"},
  {"path": "self.energy", "op": "gte", "value": 3}
]}}

// OR
{"condition": {"kind": "or", "conditions": [...]}}

// NOT
{"condition": {"kind": "not", "condition": {"path": "...", "op": "eq", "value": "..."}}}
```

### 4D. 函数条件（FnCond）

特定场景的快捷条件函数：

```jsonc
{"condition": {"kind": "fn", "name": "is_charging"}}
{"condition": {"kind": "fn", "name": "first_action"}}
```

---

## 五、控制流 — `when`

特性 trigger 内嵌 when 分支，与技能 IR 语法一致：

```jsonc
// 基础 when-then
{
  "when": {"cond": "counter_succeeded"},
  "then": [
    {"kind": "stat", "stat": "atk", "steps": 2}
  ]
}

// when-then-else
{
  "when": {"cond": "hp_below", "ratio": 0.5},
  "then": [/* 低HP时 */],
  "else": [/* 正常时 */]
}

// 嵌套 when
{
  "when": {"cond": "on_damage_taken"},
  "then": [
    {
      "when": {"cond": "have_skill_of", "of": "sprite_self", "element": "火"},
      "then": [
        {"kind": "stat", "stat": "damage_reduction", "value": 0.4, "scope": "battlefield", "per_hit": true}
      ]
    }
  ]
}
```

### `effects_mode` — 效果替换模式

特性独有字段，控制同一 trigger 重复触发时的行为：

| 值 | 含义 |
|----|------|
| `"accumulate"`（默认） | 每次触发累加效果 |
| `"replace"` | 新效果替换旧效果（如入场时重新计算动态值） |

```jsonc
// 悼亡：入场时根据力竭数计算双攻，每次入场替换（不累加）
{"on": "entry", "effects_mode": "replace", "effects": [
  {"kind": "stat", "stat": "atk", "steps": "=@player_fainted_count * 3 + opponent_fainted_count * 3"}
]}
```

---

## 六、Scope 与生命周期

| scope | 含义 | 离场 | 力竭 | 永久 |
|--------|------|------|------|------|
| `battlefield` | 战场光环 | 清除 | 清除 | 否 |
| `persistent` | 持久效果 | 保留 | 清除 | 否（受 ttl 控制） |
| `permanent` | 永久效果 | 保留 | 保留 | 是 |

```jsonc
// battlefield：在场时双攻+30%，离场消失
{"kind": "stat", "stat": "atk", "steps": 3, "scope": "battlefield", "source": "虫群鼓舞"}

// permanent：全技能能耗-3，永久有效
{"kind": "stat", "stat": "energy_cost", "steps": -3, "scope": "permanent", "source": "珊瑚骨"}

// persistent：受 ttl 控制，N回合后自动消失
{"kind": "abnormal", "target": "opp", "name": "中毒", "stacks": 5, "scope": "persistent", "ttl": 3}
```

---

## 七、Count / Observer 模型（旧 passive 格式的核心机制）

旧 `passive` 格式的核心是 `count` opcode，它注册一个持久化条件监听器（Observer），在每次相关事件发生时 eval 条件并执行 then-block。

### 旧格式（passive）

```jsonc
// 偏振：每次受到伤害 → 检查是否携带对应系别技能 → 减伤40%
{"passive": [{
  "op": "count",
  "when": {"cond": "on_damage_taken"},
  "then": [{
    "when": {"cond": "have_skill_of", "of": "sprite_self", "element": {"q": "element", "of": "skill_opp_current"}},
    "then": [{"op": "mod", "target": "sprite_self", "stat": "damage_reduction", "value": 0.4, "scope": "battlefield", "per_hit": true}]
  }]
}]}

// 助燃：每次使用火系技能 → 双攻+20%
{"passive": [{
  "op": "count",
  "when": {"cond": "skill_use", "element": "火"},
  "then": [
    {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0.2, "mode": "add", "scope": "permanent"},
    {"op": "mod", "target": "sprite_self", "stat": "sp_atk", "value": 0.2, "mode": "add", "scope": "permanent"}
  ]
}]}

// 下黑手：敌方换宠 → 对入场精灵施加5层中毒
{"passive": [{
  "op": "count",
  "when": {"cond": "opp_switched"},
  "then": [{"op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 5}]
}]}
```

### 新格式（triggers）

旧 `passive` 中的 `count` 在编译时被标准化为 `triggers` 格式：

```jsonc
// 不移（无条件永久修饰器 → entry trigger，无 observer 开销）
{"triggers": [{
  "on": "entry",
  "effects": [
    {"kind": "stat", "stat": "power_mult", "steps": 3, "scope": "permanent", "source": "不移",
     "skill_filter": "bare_attack"}
  ]
}]}

// 爆燃（技能使用的能量消耗条件 → entry trigger + condition）
{"triggers": [{
  "on": "entry",
  "condition": {"path": "skill.energy_cost", "op": "eq", "value": 0},
  "effects": [
    {"kind": "stat", "stat": "power_mult", "steps": 3, "scope": "permanent"}
  ]
}]}

// 珊瑚骨（敌方离场 → enemy_leave trigger）
{"triggers": [{
  "on": "enemy_leave",
  "effects": [
    {"kind": "stat", "stat": "energy_cost", "steps": -3, "scope": "permanent", "source": "珊瑚骨"}
  ]
}]}

// 偏振（事件触发 + when 嵌套 → count observer 模式，但用 condition + when 表达）
{"triggers": [{
  "on": "take_damage",
  "effects": [{
    "when": {"cond": "have_skill_of", "of": "sprite_self", "element": {"q": "element", "of": "skill_opp_current"}},
    "then": [
      {"kind": "stat", "stat": "damage_reduction", "value": 0.4, "per_hit": true}
    ]
  }]
}]}
```

### 编译器标准化规则

| passive 模式 | 标准化为 |
|-------------|---------|
| `{"op": "mod", ...}`（无 when/then） | → `"on": "entry"` trigger |
| `{"op": "count", "when": {"cond": "skill_use", "element": "火"}}` | → `"on": "skill_use"` trigger + condition |
| `{"op": "count", "when": {"cond": "on_damage_taken"}}` | → `"on": "take_damage"` trigger |
| `{"op": "count", "when": {"cond": "opp_switched"}}` | → `"on": "enemy_leave"` trigger |
| `{"op": "count", "when": {"cond": "on_ko"}}` | → `"on": "ko_enemy"` trigger |
| `{"op": "count", "when": {"cond": "turn_end"}}` | → `"on": "turn_end"` trigger |
| `{"op": "count", "when": {"cond": "sprite_entered"}}` | → `"on": "entry"` trigger |

---

## 八、Trigger 结构完整参考

```jsonc
{
  "on": "entry",                    // 必填：hook 点
  "condition": {...},               // 可选：触发条件（PathCond / FnCond / AndCond / OrCond / NotCond）
  "effects": [...],                 // 可选：效果列表
  "effects_mode": "accumulate",     // 可选："accumulate"（默认）/ "replace"
  "clear_condition": {...},         // 可选：自动清除条件
  
  // 延迟
  "delay": 0,                       // 可选：延迟回合数
  "delay_phase": "start",           // 可选："start" / "end"
  
  // 计数器
  "counter": "my_counter",          // 可选：计数器名称
  "counter_op": "inc",              // 可选："inc" / "dec" / "set"
  "counter_value": 1,               // 可选：计数器变化量（支持 IRValue）
  "counter_trigger": {"op": "gte", "value": 3},  // 可选：阈值触发
  "counter_reset": false,           // 可选：触发后是否重置
  
  // 轨迹跟踪
  "track": {"on": "damage", "stat": "total_damage"},  // 可选
  
  // 修饰器注入
  "use_modifiers": {                // 可选：注入到 SkillUse.modifiers
    "power_mult": {"op": "add", "value": 0.3}
  },
  
  // 技能变异
  "battleskill_mut": [              // 可选：直接修改 BattleSkill 对象
    {"field": "energy_cost", "value": -1, "op": "add"}
  ],
  
  // 行动修改
  "action_modifier": {              // 可选：修改可用行动
    "action": "restrict_slots",
    "slots": [1, 3]
  },
  
  // 离场效果
  "pending_effects": [...],         // 可选：离场时传递给下一个入场精灵
  
  // 标志位
  "flags": {"suppress_counter": true},  // 可选
  
  // 队伍计数器
  "team_counters": {"my_counter": 1}    // 可选
}
```

---

## 九、编译示例

### 示例1：不移（无条件永久修饰器）

```
描述：携带的无额外效果攻击技能威力+30%。
```

```jsonc
// 旧 passive 格式
{"passive": [{
  "op": "mod", "target": "sprite_self", "stat": "power_mult",
  "value": 1.3, "scope": "permanent", "skill_filter": "bare_attack"
}]}

// ↓ 编译后 triggers 格式
{"triggers": [{
  "on": "entry",
  "effects": [
    {"kind": "stat", "stat": "power_mult", "steps": 3,
     "scope": "permanent", "source": "不移",
     "skill_filter": "bare_attack"}
  ]
}]}
```

### 示例2：偏振（受击触发 + when 嵌套）

```
描述：受到自己携带技能系别的攻击伤害-40%。
```

```jsonc
// 旧 passive 格式（count + 嵌套 when）
{"passive": [{
  "op": "count",
  "when": {"cond": "on_damage_taken"},
  "then": [{
    "when": {"cond": "have_skill_of", "of": "sprite_self",
             "element": {"q": "element", "of": "skill_opp_current"}},
    "then": [{"op": "mod", "target": "sprite_self", "stat": "damage_reduction",
              "value": 0.4, "scope": "battlefield", "per_hit": true}]
  }]
}]}

// ↓ 编译后 triggers 格式
{"triggers": [{
  "on": "take_damage",
  "effects": [{
    "when": {"cond": "have_skill_of", "of": "sprite_self",
             "element": {"q": "element", "of": "skill_opp_current"}},
    "then": [
      {"kind": "stat", "stat": "damage_reduction", "value": 0.4,
       "scope": "battlefield", "per_hit": true}
    ]
  }]
}]}
```

### 示例3：助燃（技能使用触发 + 元素过滤）

```
描述：使用火系技能后，获得双攻+20%。
```

```jsonc
// 旧 passive 格式
{"passive": [{
  "op": "count",
  "when": {"cond": "skill_use", "element": "火"},
  "then": [
    {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0.2, "mode": "add", "scope": "permanent"},
    {"op": "mod", "target": "sprite_self", "stat": "sp_atk", "value": 0.2, "mode": "add", "scope": "permanent"}
  ]
}]}

// ↓ 编译后 triggers 格式
{"triggers": [{
  "on": "skill_use",
  "condition": {"path": "skill.element", "op": "eq", "value": "火"},
  "effects": [
    {"kind": "stat", "stat": "atk", "value": 0.2, "mode": "add", "scope": "permanent", "source": "助燃"},
    {"kind": "stat", "stat": "sp_atk", "value": 0.2, "mode": "add", "scope": "permanent", "source": "助燃"}
  ]
}]}
```

### 示例4：悼亡（入场 + effects_mode: replace + 复合 RefExpr）

```
描述：双方队伍每有1只力竭精灵，双攻+30%。
```

```jsonc
{"triggers": [{
  "on": "entry",
  "effects_mode": "replace",
  "effects": [
    {"kind": "stat", "stat": "atk",
     "steps": "=@player_fainted_count * 3 + opponent_fainted_count * 3",
     "scope": "battlefield", "source": "悼亡"},
    {"kind": "stat", "stat": "sp_atk",
     "steps": "=@player_fainted_count * 3 + opponent_fainted_count * 3",
     "scope": "battlefield", "source": "悼亡"}
  ]
}]}
```

`"=@player_fainted_count * 3 + opponent_fainted_count * 3"` 含两个 `@` 引用 + 跨引用运算，
编译为 Literal 字符串，运行时 eval 求值。

### 示例5：下黑手（敌方离场触发）

```
描述：敌方精灵离场后，更换入场的精灵获得5层中毒。
```

```jsonc
// 旧 passive 格式
{"passive": [{
  "op": "count",
  "when": {"cond": "opp_switched"},
  "then": [{"op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 5}]
}]}

// ↓ 编译后 triggers 格式
{"triggers": [{
  "on": "enemy_leave",
  "effects": [
    {"kind": "abnormal", "target": "opp", "name": "中毒", "stacks": 5, "scope": "battlefield", "source": "下黑手"}
  ]
}]}
```

### 示例6：虫群鼓舞（入场 + 路径条件 + RefExpr）

```
描述：队伍中每有1只其他虫系精灵，入场时获得攻防速+10%。
```

```jsonc
{"triggers": [{
  "on": "entry",
  "condition": {"path": "team_elements", "op": "contains", "value": "虫"},
  "effects_mode": "replace",
  "effects": [
    {"kind": "stat", "stat": "atk",   "steps": "=@player_bug_count", "scope": "battlefield", "source": "虫群鼓舞"},
    {"kind": "stat", "stat": "def",   "steps": "=@player_bug_count", "scope": "battlefield", "source": "虫群鼓舞"},
    {"kind": "stat", "stat": "speed", "steps": "=@player_bug_count", "scope": "battlefield", "source": "虫群鼓舞"}
  ]
}]}
```

`"=@player_bug_count"` 编译为 `RefExpr(root="player", path=["bug_count"], multiplier=1.0)`。

### 示例7：绝对秩序（defend hook + use_modifiers + RefExpr）

```
描述：受到非敌方系别的技能攻击时伤害-50%。
```

```jsonc
{"triggers": [{
  "on": "defend",
  "condition": {
    "path": "skill.element",
    "op": "not_in",
    "value": "=@attacker.species.elements"
  },
  "use_modifiers": {
    "damage_mult": {"op": "mult", "value": 0.5}
  }
}]}
```

### 示例8：慢热型（入场 + special + 队伍计数器）

```
描述：入场时能量置零，根据己方队伍应对成功次数回复能量（每层5点）。
```

```jsonc
{"triggers": [{
  "on": "entry",
  "effects_mode": "replace",
  "effects": [
    {"kind": "special", "name": "energy_set", "amount": 0},
    {"kind": "special", "name": "gain_energy", "amount": "=@team_counters[counter_success] * 5"}
  ]
}]}
```

---

## 十、迁移速查表

### passive when.cond → triggers on

| passive `when.cond` | triggers `on` | 说明 |
|---------------------|---------------|------|
| `skill_use` | `"skill_use"` | 使用技能后 |
| `sprite_entered` | `"entry"` | 精灵入场 |
| `opp_switched` | `"enemy_leave"` | 敌方换宠 |
| `self_switched` | `"leave"` | 己方换宠 |
| `on_abnormal_tick` | `"abnormal_tick"` | 异常tick |
| `on_ko` | `"ko_enemy"` | 击倒敌方 |
| `on_self_ko` | `"faint"` | 自身力竭 |
| `on_abnormal_changed` | `"gain_effect"` | 获得效果 |
| `on_damage_taken` | `"take_damage"` | 受到伤害 |
| `on_energy_changed` | `"energy_change"` | 能量变化 |
| `turn_end` | `"turn_end"` | 回合结束 |
| `turn_start` | `"turn_start"` | 回合开始 |

### passive op → triggers effects

| passive `op` | triggers `kind` | 说明 |
|-------------|-----------------|------|
| `mod` | `"stat"` | 属性修正 |
| `abnormal` | `"abnormal"` | 异常 |
| `mark` | `"mark"` | 印记 |
| `weather` | `"weather"` | 天气 |
| `dispel` | `"dispel"`（通过 effects 内的 op） | 驱散 |
| `steal` | `"steal"`（通过 effects 内的 op） | 偷取 |

### passive value 查询 → IR

| passive | IR | 说明 |
|----------|-----|------|
| `{"q": "element", "of": "skill_opp_current"}` | 不变（Query 格式与技能共用） | 寄存器查询 |
| `{"q": "energy", "of": "sprite_self", "scale": 0.1}` | 不变 | 带 scale |
| `"=@self.energy * 2"` | `"=@self.energy * 2"` → 编译为 `RefExpr` | RefExpr 路径 |
| 字面量 `3`, `0.5`, `"火"` | 不变 | Literal |

### target 值映射

| passive `target` | triggers 等效 |
|------------------|---------------|
| `sprite_self` | `"self"` |
| `sprite_opp` | `"opp"` / `"target"` |
| `team_own` | `"own_team"` |
| `team_opp` | `"opp_team"` |
| `skill_off_0` | `"current_skill"` |
| `skill_opp_current` | `"opp_skill"` |

---

## 十一、与技能 IR 的关键差异

| 维度 | 技能 IR | 特性 IR |
|------|---------|---------|
| 入口 | `effects` 数组（技能体） | `triggers` 数组（特性体） |
| 触发 | 玩家选择 + priority 排序 | 引擎事件 → hook 点路由 |
| 条件 | `when.cond`（命名条件） | `condition`（命名条件 + 路径条件） |
| 生命周期 | 瞬时（单次技能执行） | 持久（跨回合 Observer） |
| 排序 | `feeds`/`needs` 拓扑排序 | 无排序（Observer 按注册顺序 eval） |
| 专有 opcode | `charge` `borrow` `replay` | `mutate_effect` `schedule` `transform` `trait_interaction` `inherity_effects` `team_counter` `lives` |
| 专有字段 | `feeds` `needs` `per_hit` `delay` `ttl` `cooldown` | `effects_mode` `counter` `use_modifiers` `battleskill_mut` `action_modifier` `pending_effects` `flags` `team_counters` `track` |
| 共用 | opcode 指令集、COND_EVAL 条件表、Query 值表达式、scope 体系、when 控制流 | 同左 |

---

## 十二、附录

### A. 完整 `on` 值合法表

| on 值 | 对应 Hook |
|--------|-----------|
| `entry` | `on_entry` |
| `leave` | `on_leave` |
| `turn_start` | `on_turn_start` |
| `turn_end` | `on_turn_end` |
| `modifier` | `on_modifier` |
| `damage` | `on_damage` |
| `defend` | `on_defend` |
| `skill_use` | `on_skill_use` |
| `take_damage` | `on_take_damage` |
| `before_damage` | `on_before_take_damage` |
| `fatal_damage` | `on_fatal_damage` |
| `ko_enemy` | `on_ko_enemy` |
| `counter_success` | `on_counter_success` |
| `faint` | `on_faint` |
| `energy_change` | `on_energy_change` |
| `gain_effect` | `on_gain_effect` |
| `inflict` | `on_inflict` |
| `enemy_leave` | `on_enemy_leave` |
| `abnormal_tick` | `on_abnormal_tick` |
| `before_action` | `on_before_action` |

### B. 完整 effects `kind` 合法表

| kind | IR 类型 |
|------|---------|
| `stat` | `TraitStatEffect` |
| `abnormal` | `TraitAbnormalEffect` |
| `mark` | `TraitMarkEffect` |
| `weather` | `TraitWeatherEffect` |
| `special` | `TraitSpecialEffect` |
| `mutate_effect` | `MutateEffectOp` |
| `remove_effect` | `RemoveEffectOp` |
| `battleskill_mut` | `BattleSkillMutOp` |
| `schedule` | `ScheduleOp` |
| `inherity_effects` | `InheritEffectsOp` |
| `team_counter` | `TeamCounterOp` |
| `transform` | `TransformOp` |
| `trait_interaction` | `TraitInteractionOp` |
| `lives` | `LivesOp` |

共享操作码（`dispel` / `steal` / `tick` / `double` / `escape` / `return` / `lock` / `interrupt` / `hit` / `exchange` / `reset` / `redirect` / `replay` / `borrow` / `count`）在特性 side 通常嵌套在 trigger 的 `effects` 数组内，使用与技能相同的 JSON 格式（`"op": "..."`）。

### C. 设计原则总结

1. **纯函数 VM**：`(Ctx, effects[]) -> Journal[Mutation]`。无副作用，确定性。
2. **Observer 模型**：特性 = `{cond, then, scope}` 三元组，事件驱动，按注册顺序 eval。
3. **共享底层**：特性与技能共用 opcode 指令集、COND_EVAL 条件表、Query 值表达式、scope 体系。
4. **双条件体系**：命名条件（COND_EVAL，与技能共用）+ 路径条件（object-graph-path，特性独有）。
5. **编译器标准化**：旧 passive 格式 → triggers 格式在编译期完成，运行时只处理统一格式。
6. **类型化 IR**：所有节点都是 frozen dataclass，match/case 类型分派替代字符串分派。
