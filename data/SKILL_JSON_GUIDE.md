# 技能 JSON 编写指南

## 文件命名

`data/skills/<技能名称>.json`，文件名即技能名。

## 顶层字段一览

```jsonc
{
  "id": 0,                // [必填] 技能唯一ID
  "name": "",             // [必填] 技能名，与文件名一致
  "element": "",          // [必填] 属性（见下方合法值）
  "skill_type": "",       // [必填] 技能类型（见下方合法值）
  "power": 0,             // [可选] 基础威力，非攻击技为0
  "energy_cost": 0,       // [可选] 基础能耗
  "counter": "无",        // [可选] 应对类型，默认"无"
  "priority": 0,          // [可选] 优先级，默认0
  "combo": -1,            // [可选] 连击数，-1=不参与连击（默认），1=单次，2+=多次
  "transmission": -1,     // [可选] 传动行为：-1=主轴（不被传动移动），0=不传动，1+=传动等级
  "exclusive_to": "",     // [可选] 专属精灵名，萌化后精灵不匹配则封印
  "description": "",      // [可选] 人类可读描述（API/前端展示用）
  "effects": []           // [可选] 效果列表（见下方）
}
```

---

## 字段合法值

### `element` — 属性

| 值 |
|----|
| `"火"` `"水"` `"草"` `"冰"` `"电"` `"地"` `"武"` `"毒"` `"虫"` `"龙"` `"机械"` `"普通"` `"光"` `"幻"` `"恶"` `"幽灵"` |

### `skill_type` — 技能类型

| 值 | 含义 | 伤害计算 |
|----|------|----------|
| `"物攻"` | 物理攻击 | atk vs def |
| `"魔攻"` | 魔法攻击 | sp_atk vs sp_def |
| `"动态攻击"` | 自适应攻击 | 取精灵物攻/魔攻较高项 |
| `"防御"` | 防御技能 | 无伤害，可应对 |
| `"状态"` | 状态技能 | 无伤害 |

### `counter` — 应对类型

| 值 | 含义 |
|----|------|
| `"无"` | 不触发应对 |
| `"攻击"` | 应对物攻/魔攻/动态攻击 |
| `"防御"` | 应对防御技能 |
| `"状态"` | 应对状态技能 |

应对成功时 `counter_succeeded` 条件为真，被应对技能威力×0.5。

### `priority` — 优先级

整数，默认 0。同 priority 随机先后，不同 priority 高者先动。

### `combo` — 连击数

整数，默认 -1。

| 值 | 含义 |
|----|------|
| `-1` | 不参与连击机制（状态/防御技能默认），不受 combo 加成影响 |
| `0` | 保留（不使用） |
| `1` | 单次执行 |
| `2+` | 多次执行，每次独立计算伤害 |

### `transmission` — 传动行为

整数，默认 -1。用一个字段表达三种状态：

| 值 | 含义 |
|----|------|
| `-1` | 主轴：不参与传动，也不被其他技能传动推动（原 `main_axis: true`） |
| `0` | 普通技能：不主动传动，但可被其他传动技能推动 |
| `1+` | 传动等级：回合开始时向右移动 N 格 |

### `target` — 效果目标

所有 effect 的 `target` 字段指定效果作用域，按层级分为五类：

#### 1. 场级别

| 值 | 含义 | 典型效果 |
|----|------|----------|
| `"field"` | 全局场地（无归属双方） | 天气 |

#### 2. 队伍级别

| 值 | 含义 | 典型效果 |
|----|------|----------|
| `"team_own"` | 我方队伍 | 印记、奉献池 |
| `"team_opp"` | 对方队伍 | 印记、奉献池 |
| `"team_both"` | 双方队伍 | 驱散双方印记 |

#### 3. 精灵级别

| 值 | 含义 | 典型效果 |
|----|------|----------|
| `"sprite_self"` | 本精灵 | 属性变化、HP 回复、能量回复、异常 |
| `"sprite_opp"` | 对方场上精灵 | 属性变化、异常、伤害 |
| `"sprite_self_back"` | 我方场下精灵（替补） | 替补回复、入场触发 |
| `"sprite_opp_back"` | 对方场下精灵 | 替补伤害、标记 |
| `"sprite_both"` | 双方场上精灵 | 交换类效果 |

#### 4. 技能槽位级别（相对位移）

| 值 | 含义 | 典型效果 |
|----|------|----------|
| `"skill_off_0"` | 本技能自身 | 本技能威力永久增长、连击增长 |
| `"skill_off_-1"` | 前一位技能（左侧） | 相邻技能加成 |
| `"skill_off_1"` | 后一位技能（右侧） | 传动推动、相邻加成 |
| `"skill_off_-2"` | 前两位技能 | — |
| `"skill_off_2"` | 后两位技能 | — |

#### 5. 技能类型级别

按归属和类型筛选技能槽位：

| 值 | 含义 |
|----|------|
| `"skill_own_attack"` | 我方攻击技能（物攻/魔攻/动态攻击） |
| `"skill_own_defense"` | 我方防御技能 |
| `"skill_own_status"` | 我方状态技能 |
| `"skill_own_any"` | 我方所有技能 |
| `"skill_opp_current"` | 对方当前使用技能 |
| `"skill_opp_attack"` | 对方攻击技能 |
| `"skill_opp_defense"` | 对方防御技能 |
| `"skill_opp_status"` | 对方状态技能 |
| `"skill_opp_any"` | 对方所有技能 |

---

#### 旧值兼容

旧版简写仍可用，引擎自动映射：

| 旧值 | 映射到 |
|------|--------|
| `"self"` | `"sprite_self"` |
| `"opp"` | `"sprite_opp"` |
| `"both"` | `"sprite_both"` |
| `"own_team"` | `"team_own"` |
| `"opp_team"` | `"team_opp"` |

部分效果有默认 target（如 heal 默认 `sprite_self`，power_bonus 默认 `sprite_opp`），不填 `target` 时使用默认值。

### `scope` — 效果持续范围

| 值 | 含义 |
|----|------|
| `"battlefield"` | 换宠清除，可驱散（默认） |
| `"persistent"` | 换宠保留，可驱散 |
| `"permanent"` | 换宠保留，**不可驱散**（道具/特性/专属增益） |
| `"aura"` | 换宠清除，**不可驱散**（特性光环，持有者在场即自动维持） |

---

## Effect 类型

所有效果放在 `effects` 数组中，按顺序执行（L3 state 层）。

### 1. `stat` — 属性变化

```jsonc
{
  "kind": "stat",
  "target": "sprite_self",     // "sprite_self" | "sprite_opp"
  "stat": "atk",             // 见下表
  "steps": 3,                // 正=增益，负=减益
  "scope": "battlefield"     // "battlefield" | "persistent" | "permanent"
}
```

#### `stat` 合法值

| 值 | 每步单位 | 说明 |
|----|----------|------|
| `"atk"` | 10% | 物攻 |
| `"def"` | 10% | 物防 |
| `"sp_atk"` | 10% | 魔攻 |
| `"sp_def"` | 10% | 魔防 |
| `"speed"` | 10% | 速度 |
| `"power"` | 10 | 威力（每步±10） |
| `"priority"` | 1 | 优先级 |
| `"energy_cost"` | 1 | 能耗（每步±1） |

示例：
```jsonc
// 物攻+30%（3步×10%）
{ "kind": "stat", "target": "sprite_self", "stat": "atk", "steps": 3, "scope": "battlefield" }

// 敌方双防-70%（-7步×10%）
{ "kind": "stat", "target": "sprite_opp", "stat": "def", "steps": -7, "scope": "battlefield" }
{ "kind": "stat", "target": "sprite_opp", "stat": "sp_def", "steps": -7, "scope": "battlefield" }

// 所有技能威力+20（2步×10，挂载在精灵身上）
{ "kind": "stat", "target": "sprite_self", "stat": "power", "steps": 2, "scope": "battlefield" }
```

---

### 2. `abnormal` — 异常状态

```jsonc
{
  "kind": "abnormal",
  "target": "sprite_opp",      // "sprite_self" | "sprite_opp"
  "name": "中毒",            // 见下表
  "scope": "battlefield",    // "battlefield" | "persistent" | "permanent"
  "stacks": 1                // 层数
}
```

#### `name` 合法值

| 值 | 效果 |
|----|------|
| `"中毒"` | 回合末扣%HP |
| `"灼烧"` | 回合末扣%HP |
| `"冻结"` | 每层锁5%HP上限 |
| `"寄生"` | 回合末吸收对方HP |
| `"眩晕"` | 无法行动 |
| `"萌化"` | 标记萌化状态，影响专属技 |

---

### 3. `mark` — 印记

```jsonc
{
  "kind": "mark",
  "target": "team_own",        // "team_own" | "team_opp" | "team_both"
  "name": "光合印记",        // 任意名称，同名自动叠层
  "stacks": 1                // 赋予层数
}
```

印记本身无效果，由其他技能的条件效果读取。印记可被 `dispel_mark` / `steal_mark` 驱散/偷取。

---

### 4. `weather` — 天气

```jsonc
{
  "kind": "weather",
  "weather": "rain",         // "rain" | "sand" | "snow"
  "turns": 8                 // 持续回合数
}
```

---

### 5. 瞬时效果（原 special 扁平化）

所有瞬时效果现在都是顶级 `kind`，与 `stat`/`abnormal`/`mark`/`weather`/`conditional` 同级。

```jsonc
{
  "kind": "<效果类型>",        // 见下表，即原 special.name
  "value": 0.0,                // 倍率/百分比参数
  "amount": 0,                 // 整数参数（偷能量、驱散数量等）
  "target": "sprite_opp",             // 按具体效果不同，部分效果有默认值
  "abnormal_name": "",         // [仅特定效果] 目标异常名
  "per_stack_value": 0.0,      // [仅damage_reduction_by_abnormal] 每层追加值
  "max_value": 1.0             // [仅damage_reduction_by_abnormal] 上限
}
```

#### 效果类型全表

##### 伤害类（L0层注入。字段：`value`, `amount`）

| kind | 字段 | 效果 |
|------|------|------|
| `power_bonus` | `amount` (int) | 威力+N |
| `power_mult` | `value` (float) | 威力×N |
| `counter_power_mult` | `value` (float) | 应对威力倍率：应对成功时基础威力×N |
| `damage_mult` | `value` (float) | 伤害×N（独立乘区） |
| `damage_reduction` | `value` (float) | 减伤N%（0~1，取最大值） |
| `multi_hit` | `amount` (int) | 追加N次攻击 |

##### 治疗/能量类（`target` 默认 sprite_self，可选 sprite_opp）

| kind | 字段 | 效果 |
|------|------|------|
| `heal` | `value`, `target` | 回复最大HP的N%（value=0~1） |
| `direct_heal` | `amount`, `target` | 回复N点HP |
| `gain_energy` | `amount`, `target` | 回复N能量 |
| `steal_energy` | `amount`,`target` | 偷取target N能量,如果对象是对面，则自己偷取 |
| `life_drain` | `value` | 吸血：造成伤害的N%回复自身HP |
| `gain_energy_by_enemy` | `value` | 按敌方技能总能耗的N%回复能量 |
| `devotion_cost` | `value` (int) | 获得value层奉献，每层减少2点能耗 |

##### 控制类

| kind | 字段 | 效果 |
|------|------|------|
| `charge` | — | 蓄力：首次进入蓄力状态，下次同名技能自动释放 |
| `reflect_damage` | — | 反射：将本技能替换为被应对技能 |
| `counter_damage` | `value` | 反击：value=0用被应对技能威力，value>0固定威力 |
| `reverse_heal` | `target`, `value` | 反转治疗：指定方本回合治疗量×value转为丢失生命（sprite_self/sprite_opp） |
| `next_atk` | `target`, `stat`, `steps`, `scope` | 下一次攻击时：给指定方施加属性变化 |
| `lock_switch` | `target`, `turns` | 锁定换宠：指定方（sprite_self/sprite_opp）turns回合内无法换宠 |

##### 场地/返场类

| kind | 字段 | 效果 |
|------|------|------|
| `escape` | `target`, `inherit` (bool, 默认false), `then` (效果列表, 可选) | target脱离：本回合行动后换替补上场；inherit=true时替补继承离场精灵的正面增益；then中的效果在换宠完成后执行 |
| `force_return` | target | 强制target换宠 |
| `return_self` | — | 自己换宠 |

##### 驱散/加倍类

| kind | 字段 | 效果 |
|------|------|------|
| `dispel_positive` | `target` | 驱散正面效果（sprite_self/sprite_opp） |
| `dispel_negative` | `target` | 驱散负面效果 |
| `dispel_mark` | `target` | 驱散印记（team_own/team_opp/team_both） |
| `steal_mark` | `target` | 偷取印记 |
| `double_positive` | `target` | 正面效果翻倍 |
| `double_negative` | `target` | 负面效果翻倍 |
| `double_abnormal` | `target`, `abnormal_name` | 指定异常层数翻倍 |
| `double_mark` | `target`, `name` | 指定印记层数翻倍（team_own/team_opp） |
| `abnormal_tick` | `target`, `abnormal_name` | 触发一次指定异常的回合末效果 |

##### 异常相关

| kind | 字段 | 效果 |
|------|------|------|
| `damage_reduction_by_abnormal` | `value`, `per_stack_value`, `max_value`, `abnormal_name` | 基础减伤N% + 每层异常追加M%，上限X% |
| `power_by_abnormal` | `value` | 根据异常层数额外计算威力 |

##### 交换类

| kind | 字段 | 效果 |
|------|------|------|
| `exchange_hp_ratio` | — | 与敌方交换生命比例 |
| `exchange_effects` | — | 与敌方交换所有增益和减益 |
| `exchange_skills` | — | 与敌方交换技能 |

##### 萌化相关

| kind | 字段 | 效果 |
|------|------|------|
| `transfer_moe` | — | 将自己的萌化全部转移给敌方 |
| `combo_by_moe` | `amount` | 月光合奏：双方所有精灵每有1层萌化，连击+1 |

##### 动态缩放

| kind | 字段 | 效果 |
|------|------|------|
| `stat_by_abnormal` | `stat`, `steps` | 根据异常层数计算属性变化 |
| `power` | `by`, `target`, `value`, `amount` | 动态威力：按指定来源计算，覆盖基础威力 |

###### `power.by` 合法值

| by | value 含义 | amount | target | 效果 |
|----|-----------|--------|--------|------|
| `"energy"` | 倍率 | — | sprite_self/sprite_opp | 威力=指定方技能总能耗×value |
| `"adjacent"` | 倍率 | — | skill_off_-1/skill_off_1 | 威力=相邻技能威力之和×value |
| `"fainted"` | 加值 | — | — | 敌方每力竭1只，威力+value |
| `"missing_hp"` | 加值 | 阈值% | sprite_self/sprite_opp | 每损失amount%HP，威力+value |
| `"enemy_power"` | 倍率 | — | sprite_opp | 威力=对方技能基础威力×value |

示例：
```jsonc
// 威力=对方技能总能耗×2
{ "kind": "power", "by": "energy", "target": "sprite_opp", "value": 2.0 }

// 每力竭1只 +30
{ "kind": "power", "by": "fainted", "value": 30 }

// 每损失10%HP +20
{ "kind": "power", "by": "missing_hp", "target": "sprite_self", "value": 20, "amount": 10 }
```
| `power_penalty_by_energy` | `value` | 敌方每有1点能量，自身威力-value |
| `adjacent_power_bonus` | `amount` | 相邻技能威力+amount |
| `priority_bonus` | `amount` | 优先级+N |
| `ignore_mods` | — | 无视双方属性变化 |
| `random_devotion` | `amount` | 随机激活1~5种奉献类型 |
| `borrow_skill` | — | 从己方替补借用技能替换当前技能槽 |
| `defense_cooldown_reduce` | — | 降低防御技能冷却 |
| `power_gain_on_move` | `value` (int) | 传动位移后本技能威力永久+value |

##### 威力特殊计算

| kind | 字段 | 效果 |
|------|------|------|
| `skip_next_charge` | — | 跳过下次蓄力 |
| `power_double` | — | 本技能威力翻倍 |
| `power_double_on_ko` | — | 击杀后下次攻击威力翻倍 |
| `consume_energy_for_power` | `value` | 消耗能量换威力（每点+N） |
| `gain_energy_on_ko` | `amount` | 击杀后获得N能量 |

##### 技能永久增长（POST_USE层）

| kind | 字段 | 效果 |
|------|------|------|
| `combo_increment` | `amount` | 每次使用后连击+1 |
| `power_increment` | `amount` | 每次使用后威力+N |
| `energy_cost_increment` | `amount` | 每次使用后能耗+N |

---

### 6. `tally` — 计次成长

统计外部事件，每次满足条件时对本技能施加永久增长。

```jsonc
{
  "kind": "tally",
  "skip_self": true,              // [可选] 是否排除自身，默认false
  "when": {                       // 统计条件（条件 dict 格式）
    "target": "sprite_self",      //   条件作用域
    "kind": "skill_use",          //   条件类型
    "element": "草"               //   附加筛选（可选）
  },
  "then": [                       // 每次满足条件时执行的效果列表
    {
      "kind": "stat",
      "target": "skill_off_0",    // 本技能自身
      "stat": "power",
      "steps": 6,
      "scope": "permanent"
    }
  ]
}
```

#### `when.kind` 合法值

| kind | 参数 | 说明 |
|------|------|------|
| `"skill_use"` | `element` (str, 可选), `skill_type` (str, 可选) | 指定方使用技能时触发，可按属性/类型筛选 |

`then` 中 `target` 应指向 `"skill_off_0"`（本技能）以实现永久增长。

---

### 7. `conditional` — 条件触发

```jsonc
{
  "kind": "conditional",
  "when": {                  // 条件 dict（见下表）
    "kind": "hp_below",
    "ratio": 0.5
  },
  "then": [                  // 满足条件时执行的效果列表
    { /* 任意 effect */ }
  ]
}
```

#### 条件 `kind` 合法值

| kind | 参数 | 说明 |
|------|------|------|
| `"counter_succeeded"` | — | 本技能应对成功 |
| `"interrupt"` | — | 打断：应对成功时无效化被应对技能 |
| `"burst"` | — | 迸发：本精灵入场后首次使用技能 |
| `"charged"` | — | 蓄力：本技能本次释放为蓄力完成后的自动释放 |
| `"first_action"` | — | 先手：本精灵入场后首次使用技能 |
| `"is_first"` | — | 本技能本回合最先执行 |
| `"is_second"` | — | 本技能本回合后执行 |
| `"opp_switched"` | — | 对方本回合换宠了 |
| `"is_heal"` | `target` (str) | 指定方本回合使用治疗类技能（sprite_self/sprite_opp） |
| `"hp_below"` | `ratio` (float, 0~1) | 自己HP比例 < ratio |
| `"has_abnormal"` | `name` (str) | 自己有指定异常 |
| `"weather_is"` | `weather` (str) | 当前天气为指定值 |
| `"counter_ge"` | `key` (str), `value` (int) | 计数器 ≥ value |
| `"energy_le"` | `value` (int) | 目标能量 ≤ value |
| `"energy_eq"` | `value` (int) | 目标能量 = value |
| `"moe_succeeded_self"` | — | 最近一次对自己萌化成功（形态改变） |
| `"moe_succeeded_opp"` | — | 最近一次对敌方萌化成功（形态改变） |
| `"skill_at"` | `positions` (list[int]) | 本技能在指定位置（0=1号位） |
| `"and"` | `conditions` (list) | 所有子条件均满足 |
| `"or"` | `conditions` (list) | 任一子条件满足 |
| `"not"` | `condition` (dict) | 子条件取反 |

条件可嵌套：`"and"` / `"or"` 的 `conditions` 数组可包含任意条件 dict。

---

## 完整示例

### 简单攻击

```jsonc
{
  "id": 10001,
  "name": "冲撞",
  "element": "普通",
  "skill_type": "物攻",
  "power": 50,
  "energy_cost": 2,
  "effects": [],
  "description": "✦造成物伤。"
}
```

### 攻击+效果

```jsonc
{
  "id": 10152,
  "name": "偷师",
  "element": "幻",
  "skill_type": "物攻",
  "power": 30,
  "energy_cost": 0,
  "effects": [
    { "kind": "gain_energy", "amount": 1 }
  ],
  "description": "✦造成物伤，自己回复1能量。"
}
```

### 防御+应对效果

```jsonc
{
  "id": 10102,
  "name": "刺盾",
  "element": "地",
  "skill_type": "防御",
  "power": 0,
  "energy_cost": 2,
  "counter": "攻击",
  "effects": [
    { "kind": "damage_reduction", "value": 0.7 },
    {
      "kind": "conditional",
      "when": { "kind": "counter_succeeded" },
      "then": [
        { "kind": "stat", "target": "sprite_opp", "stat": "atk", "steps": -7, "scope": "battlefield" }
      ]
    }
  ],
  "description": "✦减伤70%，应对攻击：敌方获得物攻-70%。"
}
```

### 位置条件+传动

```jsonc
{
  "id": 10406,
  "name": "械斗",
  "element": "机械",
  "skill_type": "物攻",
  "power": 45,
  "energy_cost": 1,
  "transmission": 1,
  "effects": [
    {
      "kind": "conditional",
      "when": { "kind": "skill_at", "positions": [0] },
      "then": [
        { "kind": "stat", "target": "sprite_self", "stat": "power", "steps": 6, "scope": "battlefield" }
      ]
    }
  ],
  "description": "✦造成物伤，本技能位于1号位时威力+60，传动1。"
}
```

### 多层条件（and/or）

```jsonc
// 满足"应对成功 且 自身HP<50%"时触发
{
  "kind": "conditional",
  "when": {
    "kind": "and",
    "conditions": [
      { "kind": "counter_succeeded" },
      { "kind": "hp_below", "ratio": 0.5 }
    ]
  },
  "then": [
    { "kind": "power_mult", "value": 2.0 }
  ]
}
```

### 状态+天气+印记

```jsonc
{
  "id": 10055,
  "name": "冰天雪地",
  "element": "冰",
  "skill_type": "防御",
  "power": 0,
  "energy_cost": 2,
  "counter": "攻击",
  "effects": [
    { "kind": "weather", "weather": "snow", "turns": 8 },
    { "kind": "damage_reduction", "value": 0.5 },
    {
      "kind": "conditional",
      "when": { "kind": "weather_is", "weather": "snow" },
      "then": [
        { "kind": "mark", "target": "team_own", "name": "冰墙印记", "stacks": 1 }
      ]
    }
  ],
  "description": "✦设置8回合冰雪，减伤50%，冰雪天气下获得冰墙印记。"
}
```

---

## 效果执行层级

技能效果的管线分层，决定效果何时生效：

### 回合层级

| 阶段 | 名称 | 处理内容 |
|------|------|----------|
| TURN_START | 回合开始 | 延时效果(phase=start)、传动(transmission)、传动后hook、位置效果预扫描、trait turn_start、不朽复活 |
| TURN_END | 回合结束 | 延时效果(phase=end)、愿力还原、返场结算、异常dot结算、trait turn_end、力竭检查 |

### 技能执行层级（TURN_START 和 TURN_END 之间）

| 层 | 名称 | 处理内容 |
|----|------|----------|
| Gate | 前置检查 | 冷却 → 迸发能耗 → 能量支付 → 蓄力 |
| L0 | MODIFIER | 威力/伤害修正注入（power_bonus, power_mult, counter_power_mult, damage_mult, damage_reduction, multi_hit） |
| L1 | POWER | 动态威力解算（power）、连击数解算 |
| L2 | DAMAGE | per-hit 伤害计算 + 吸血(life_drain) |
| L3 | STATE | 属性变化(stat)、异常(abnormal)、印记(mark)、天气(weather)、条件触发(conditional)、计次注册(tally)、瞬时效果(charge/dispel/escape/borrow等) |
| L3.5 | POST_USE | 技能使用后永久增长（combo_increment, power_increment, energy_cost_increment） |
| L4 | COUNTER | 反击伤害（独立简化公式） |
| L5 | SWITCH | 换宠/返场/借用技能 + trait/计数器 |