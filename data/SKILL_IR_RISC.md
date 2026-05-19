# 技能 IR — RISC 基元设计（草案）

## 设计原则

每条effect就是一次函数调用，参数可以是**字面量**也可以是**查询表达式**。

```
技能（高级语言） → 基元组合（IR） → 引擎执行
```

## 三层模型

| 层 | 对应 | 职责 |
|----|------|------|
| 描述层 | 高级语言 | "敌方每有1层印记，本技能能耗-1" |
| **IR层** | **指令集** | **query + apply 基元组合** |
| 引擎层 | 虚拟机 | 解释执行基元 |

---

## 执行模型 — CPU + 寄存器

对战的本质：**每回合接收两边选手的技能选择，CPU 根据 priority 决定执行顺序，逐条执行 IR 指令。**

### CPU 外部：寄存器组

`ctx` 就是寄存器组 — 回合开始时对战场拍快照，**本回合内所有指令只读寄存器**，不直接读精灵对象：

```python
@dataclass
class Ctx:
    """回合快照 — 只读寄存器组。每条指令的 operand 通过寄存器寻址。"""

    # ── 己方精灵 ──
    hp_self: int
    hp_self_ratio: float
    hp_self_max: int
    hp_self_missing_ratio: float   # (max - current) / max
    energy_self: int
    atk_self: int
    def_self: int
    sp_atk_self: int
    sp_def_self: int
    speed_self: int
    abnormal_count_self: int
    abnormal_stacks_self: dict[str, int]  # {异常名: 层数}
    positive_count_self: int       # 增益数量
    first_action_self: bool
    charged_self: bool
    is_charging_self: bool          # 蓄力中（未完成），不同于 charged（已完成蓄力）
    self_koed: bool                # 本回合是否被击杀
    times_entered_self: int         # 累计入场次数
    times_left_self: int            # 累计离场次数（每次脱离/换下 +1）
    elements_used_count_self: int   # 使用过的不同系别技能数
    skills_energy_sum_self: int     # 全技能能耗之和

    # ── 敌方精灵 ──
    hp_opp: int
    hp_opp_ratio: float
    hp_opp_max: int
    hp_opp_missing_ratio: float
    energy_opp: int
    atk_opp: int
    def_opp: int
    sp_atk_opp: int
    sp_def_opp: int
    speed_opp: int
    abnormal_count_opp: int
    abnormal_stacks_opp: dict[str, int]  # {异常名: 层数}
    positive_count_opp: int        # 增益数量
    charged_opp: bool

    # ── 双方队伍 ──
    mark_count_own: int            # 己方队伍印记总层数
    mark_count_opp: int            # 敌方队伍印记总层数
    mark_count_both: int           # 双方队伍印记总层数
    skill_count_own: dict[str, int]  # {技能名: 队伍中携带该技能的精灵数}
    devotion_own: dict[str, int]   # 己方奉献池 {名称: 层数}
    devotion_opp: dict[str, int]   # 敌方奉献池 {名称: 层数}
    abnormal_stacks_battle: dict[str, int]  # 双方全场异常总层数 {名称: 总层数}
    fainted_own: int               # 己方力竭数
    fainted_opp: int               # 敌方力竭数
    burst_triggered_count_own: int # 己方队伍已触发的迸发种类数
    opp_switched: bool             # 敌方本回合是否换宠
    self_switched: bool            # 己方本回合是否被换下（非KO）

    # ── 技能 ──
    power_self: int                # 技能基础威力
    adjacent_power_sum: int        # 两侧相邻技能威力之和
    power_opp: int                 # 对方当前技能基础威力
    skill_type_opp: str            # 对方技能类型（物攻/魔攻/动态攻击/防御/状态）
    element_opp: str               # 对方当前技能系别
    combo_self: int                # 当前连击数
    energy_cost_self: int          # 当前能耗
    zero_cost_skill_count_self: int  # 携带的0能耗技能数量
    energy_cost_reduction_self: int # 累计能耗减少量（base - current，≥0）
    energy_cost_sum_self: dict[str, int]  # {技能类型/系别: 总能耗} 需配合 filter
    energy_cost_opp: int           # 对方技能总能耗
    skills_energy_sum_opp: int     # 敌方精灵全技能能耗之和
    counter_succeeded: bool        # 本技能应对是否成功
    prev_counter_succeeded: bool   # 上回合应对是否成功
    damage_taken_this_turn: int    # 本回合已受伤害次数
    damage_reduced_self: int       # 本回合被减免的伤害量
    last_tick_damage_self: int     # 最近一次 tick 受到的伤害量
    last_tick_damage_opp: int      # 敌方最近一次 tick 受到的伤害量

    # ── 战场 ──
    weather: str                   # 当前天气
    last_tick_abnormal: str        # 最近一次 tick 的异常名称
    last_tick_target: str          # 最近一次 tick 的目标 (sprite_self / sprite_opp)
    abnormal_changed_name: str     # 最近一次异常层数变化的名称
    abnormal_changed_target: str   # 最近一次异常层数变化的目标 (sprite_self / sprite_opp)
    abnormal_applied_name: str     # 最近一次主动施加异常的名称
    abnormal_applied_target: str   # 最近一次主动施加异常的目标 (sprite_self / sprite_opp)
    skills_energy_changed_of: str  # 最近一次全技能能耗变化所属 (sprite_self / sprite_opp)
    positive_changed_of: str       # 最近一次增益数量变化所属 (sprite_self / sprite_opp)
    energy_changed_of: str         # 最近一次能量值变化所属 (sprite_self / sprite_opp)
    turn_end: bool                 # 回合末结算信号（仅 count 用）
    turn: int
    is_first: bool                 # 本技能是否为本回合第一个行动
    skill_index: int               # 技能在精灵技能列表中的位置

    # ── 计次器快照 ──
    counter_values: dict[str, int] # 命名计次器当前值，{name: count}
```

### 寻址

查询表达式 `{ "q": "hp_missing_ratio", "of": "sprite_self" }` 本质是**寄存器寻址**：`of` + `q` → 映射到 `ctx.hp_self_missing_ratio`。

```python
# 寻址表： (of, q) → ctx 字段
ADDRESS_MAP = {
    ("sprite_self", "hp"):              "hp_self",
    ("sprite_self", "hp_ratio"):        "hp_self_ratio",
    ("sprite_self", "hp_missing_ratio"): "hp_self_missing_ratio",
    ("sprite_self", "energy"):          "energy_self",
    ("sprite_self", "skills_energy_sum"): "skills_energy_sum_self",
    ("sprite_self", "abnormal_count"):   "abnormal_count_self",
    ("sprite_self", "abnormal_stacks"):  "abnormal_stacks_self",   # 需配合 name 参数
    ("sprite_self", "times_entered"):    "times_entered_self",
    ("sprite_self", "times_left"):       "times_left_self",
    ("sprite_self", "elements_used_count"): "elements_used_count_self",
    ("sprite_self", "positive_count"):   "positive_count_self",
    ("sprite_self", "zero_cost_skill_count"): "zero_cost_skill_count_self",
    ("sprite_self", "damage_reduced"):   "damage_reduced_self",
    ("sprite_self", "last_tick_damage"): "last_tick_damage_self",
    ("sprite_opp",  "last_tick_damage"): "last_tick_damage_opp",
    ("sprite_opp",  "hp"):              "hp_opp",
    ("sprite_opp",  "hp_ratio"):        "hp_opp_ratio",
    ("sprite_opp",  "hp_missing_ratio"): "hp_opp_missing_ratio",
    ("sprite_opp",  "energy"):          "energy_opp",
    ("sprite_opp",  "abnormal_count"):   "abnormal_count_opp",
    ("sprite_opp",  "abnormal_stacks"):  "abnormal_stacks_opp",   # 需配合 name 参数
    ("sprite_opp",  "positive_count"):   "positive_count_opp",
    ("battle",      "abnormal_stacks"):  "abnormal_stacks_battle", # 需配合 name 参数，跨双方全场求和
    ("team_own",    "mark_count"):      "mark_count_own",
    ("team_own",    "skill_count"):     "skill_count_own",     # 需配合 name 参数
    ("team_own",    "devotion"):         "devotion_own",       # 需配合 name 参数
    ("team_own",    "fainted"):         "fainted_own",
    ("battle",      "weather"):           "weather",
    ("team_own",    "burst_triggered_count"): "burst_triggered_count_own",
    ("team_both",   "mark_count"):      "mark_count_both",    # mark_count_own + mark_count_opp
    ("team_opp",    "mark_count"):      "mark_count_opp",
    ("team_opp",    "devotion"):         "devotion_opp",       # 需配合 name 参数
    ("team_opp",    "fainted"):         "fainted_opp",
    ("skill_off_0", "power_base"):      "power_self",
    ("skill_off_0", "adjacent_power_sum"): "adjacent_power_sum",
    ("skill_off_0", "combo_current"):   "combo_self",
    ("skill_off_0", "energy_cost"):     "energy_cost_self",
    ("skill_off_0", "counter_value"):        "counter_values",  # 需配合 name 参数索引
    ("skill_off_0", "energy_cost_reduction"): "energy_cost_reduction_self",
    ("sprite_self", "energy_cost_sum"):   "energy_cost_sum_self",  # 需配合 skill_type / element / tag 参数
    ("skill_opp_current", "power_base"): "power_opp",
    ("skill_opp_current", "element"):     "element_opp",
    ("skill_opp_current", "energy_total"): "energy_cost_opp",
    ("sprite_opp", "skills_energy_sum"): "skills_energy_sum_opp",
}

def resolve(ctx: Ctx, value) -> int | float | str:
    """字面量直接返回，查询表达式查寄存器。"""
    if isinstance(value, (int, float, str)):
        return value
    q = value["q"]
    of = value.get("of", "sprite_self")
    key = ADDRESS_MAP[(of, q)]
    raw = getattr(ctx, key)

    # dict 类型寄存器需要 key 索引
    if q in ("counter_value", "abnormal_stacks", "devotion", "skill_count"):
        raw = raw.get(value["name"], 0)
    elif q == "energy_cost_sum":
        raw = raw.get(value.get("skill_type") or value.get("element") or value.get("tag"), 0)

    # default 回退值（raw 为 None/空字符串/0 时使用）
    if "default" in value and not raw:
        raw = value["default"]

    # weather 返回字符串，跳过数值运算
    if isinstance(raw, str):
        return raw

    # scale / per / offset
    if "per" in value:
        raw = int(raw / value["per"])
    if "scale" in value:
        raw *= value["scale"]
    if "offset" in value:
        raw += value["offset"]
    return raw
```

### CPU 主循环

```
每回合:
  1. 两边选手输入技能 → 中断信号
  2. 拍快照 → 寄存器组就绪 (Ctx 只读)
  3. priority 排序 → 决定执行顺序
  4. 逐技能:
     a. 取指: 读 effects 数组
     b. 译码: op → 选择执行函数
     c. 寻址: 查询表达式 → resolve(ctx, value)
     d. 执行: feeds 拓扑排序 → 各阶段写回
     e. count 注册: Counter 挂载到技能实例上
  5. 回合末: 写回精灵对象 (hp, energy, effects...)
```

寄存器是单次回合的快照，指令不关心"数据来源是哪个对象"，只关心寄存器名。加新查询 = 加一个寄存器字段 + 一行 `ADDRESS_MAP`。

---

## 一、值表达式 — `value`

所有需要数值的地方统一为值表达式，可以是字面量或查询：

```jsonc
// 字面量
5                              // 整数
0.3                            // 小数

// 查询表达式
{ "q": "mark_count", "of": "team_opp", "name": "灼烧印记" }   // 敌方灼烧印记层数
{ "q": "hp_missing_ratio", "of": "sprite_self" }               // 自己损失HP比例
{ "q": "energy", "of": "sprite_opp" }                          // 敌方当前能量
{ "q": "fainted", "of": "team_opp" }                           // 敌方力竭数
{ "q": "abnormal_count", "of": "sprite_self" }                 // 自己异常层数
{ "q": "abnormal_stacks", "of": "sprite_opp", "name": "中毒" }    // 敌方特定异常层数
{ "q": "times_left", "of": "sprite_self" }                     // 自己累计离场次数
{ "q": "damage_reduced", "of": "sprite_self" }                  // 本回合被减免的伤害量
{ "q": "positive_count", "of": "sprite_opp" }                   // 敌方增益数量
{ "q": "devotion", "of": "team_own", "name": "中毒两层" }         // 己方特定奉献层数
{ "q": "abnormal_stacks", "of": "battle", "name": "萌化" }       // 双方全场指定异常总层数
{ "q": "power_base", "of": "skill_opp_current" }              // 对方当前技能基础威力
{ "q": "element", "of": "skill_opp_current" }                // 对方当前技能系别
{ "q": "energy_total", "of": "sprite_opp" }                   // 对方技能总能耗
{ "q": "combo_current", "of": "skill_off_0" }                 // 本技能当前连击数
{ "q": "adjacent_power_sum" }                              // 两侧相邻技能威力之和
{ "q": "counter_value", "of": "skill_off_0", "name": "蓄力受击" }   // 命名计次器当前值
{ "q": "energy_cost_reduction", "of": "skill_off_0" }          // 能耗累计减少量
{ "q": "energy_cost_sum", "of": "sprite_self", "tag": "迅捷" }       // 自己释放过的迅捷标签技能总能耗
{ "q": "skill_count", "of": "team_own", "name": "虫鸣" }           // 己方队伍携带技能"虫鸣"的精灵数
{ "q": "zero_cost_skill_count", "of": "sprite_self" }          // 自己携带的0能耗技能数量
{ "q": "elements_used_count", "of": "sprite_self" }          // 自己使用过的不同系别技能数
{ "q": "burst_triggered_count", "of": "team_own" }          // 己方队伍已触发迸发种类数
{ "q": "skills_energy_sum", "of": "sprite_self" }              // 自己全技能能耗之和
{ "q": "skills_energy_sum", "of": "sprite_opp" }              // 敌方精灵全技能能耗之和
{ "q": "weather", "of": "battle", "default": "普通" }             // 当前天气系别，无天气时回退"普通"
{ "q": "last_tick_damage", "of": "sprite_opp" }                 // 敌方最近一次 tick 伤害量

查询支持 `scale`（倍率）、`offset`（偏移）、`default`（回退值，仅 raw 为 `None`/`0`/`""`时生效）：
```jsonc
// 敌方能量 × 10
{ "q": "energy", "of": "sprite_opp", "scale": 10 }

// 每损失10%HP算1步
{ "q": "hp_missing_ratio", "of": "sprite_self", "per": 0.1 }
```

---

## 二、指令集（10条基元）

### `target` 合法值

| 值 | 含义 |
|---|------|
| `sprite_self` | 当前在场己方精灵 |
| `sprite_opp` | 当前在场敌方精灵 |
| `team_own` | 己方队伍（印记/奉献等队伍级效果） |
| `team_opp` | 敌方队伍 |
| `team_both` | 双方队伍 |
| `team_own_benched` | 己方场下全体精灵 |
| `team_opp_benched` | 敌方场下全体精灵 |
| `skill_off_0` | 当前使用的技能 |
| `skill_at_1` | 1号位技能 |
| `skill_at_2` | 2号位技能 |
| `skill_opp_current` | 对方当前技能 |
| `battle` | 全局战场 |

### 写类（副作用）

#### `mod` — 属性修正（合并 stat / power_bonus / heal 等）

```jsonc
// 固定值
{ "op": "mod", "target": "sprite_self", "stat": "atk", "steps": 3 }
{ "op": "mod", "target": "skill_off_0", "stat": "power", "steps": 6 }
{ "op": "mod", "target": "skill_off_0", "stat": "energy_cost", "steps": -1 }
{ "op": "mod", "target": "skill_opp_current", "stat": "energy_cost", "value": 7, "mode": "add",
  "scope": "persistent", "ttl": 3 }

// 动态值
{ "op": "mod", "target": "skill_off_0", "stat": "energy_cost",
  "value": { "q": "mark_count", "of": "team_opp", "scale": -1, "name": "any" } }

// 伤害倍率
{ "op": "mod", "target": "skill_off_0", "stat": "damage_mult", "value": 2.0 }

// 治疗
{ "op": "mod", "target": "sprite_self", "stat": "hp", "value": 0.5 }     // 50%HP
{ "op": "mod", "target": "sprite_self", "stat": "hp", "value": 30 }      // 30点HP

// 连击
{ "op": "mod", "target": "skill_off_0", "stat": "combo", "value": 6 }    // 提高至6

// 奉献（命名 + 效果体）
{ "op": "mod", "target": "team_own", "stat": "devotion", "value": 1, "mode": "add",
  "name": "中毒两层",
  "then": [
    { "op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 2 }
  ]
}

// 随机奉献（引擎从5种中随机选一，自动绑定对应的 then）
{ "op": "mod", "target": "team_own", "stat": "devotion", "value": 1, "mode": "add",
  "name": "random" }
```

`stat` 合法值：`atk` `def` `sp_atk` `sp_def` `speed` `power` `priority` `energy_cost` `energy_cost_mult` `combo` `combo_mult` `hp` `energy` `damage_mult` `damage_reduction` `power_mult` `life_drain` `devotion` `pre_charged` `ignore_mods` `ignore_resistance` `cooldown` `life_as_energy` `survive` `extra_action` `extra_turn_end` `heal_reverse` `immune` `drive`

#### `mode` — 赋值模式（`mod` 可选字段）

| mode 值 | 含义 |
|---------|------|
| 不填（默认） | `set`：将值设为指定量 |
| `"add"` | 在现有基础上累加（可为负） |
| `"multiply"` | 当前值 × 指定值（累计翻倍） |

```jsonc
// combo -2（在现有连击数上减少）
{ "op": "mod", "target": "skill_off_0", "stat": "combo", "value": -2, "mode": "add" }

// 吸血比例 +30%（在现有基础上增加）
{ "op": "mod", "target": "skill_off_0", "stat": "life_drain", "value": 0.3, "mode": "add" }

// 下次蓄力技能无需蓄力
{ "op": "mod", "target": "sprite_self", "stat": "pre_charged", "value": 1,
  "on_next": true, "if_type": "charge" }
```

> **注意**：`steps` 天生是累加语义，不需要 `mode`。`value` 默认是设置语义，需要 `mode: "add"` 才是累加。

#### `skill_filter` — 批量目标（`mod` 可选字段）

当 `target` 是精灵且 `stat` 可作用于技能级属性时，`skill_filter` 指定精灵的哪些技能受影响：

```jsonc
// 敌方所有攻击技能能耗+4
{ "op": "mod", "target": "sprite_opp", "stat": "energy_cost",
  "value": 4, "scope": "persistent", "skill_filter": "attack" }
```

| skill_filter 值 | 筛选 |
|-----------------|------|
| 不填 | 仅当前技能 `skill_off_0` |
| `"attack"` | 物攻 + 魔攻 + 动态攻击 |
| `"defense"` | 防御技能 |
| `"status"` | 状态技能 |
| `"all"` | 精灵全部技能 |
| `"others"` | 排除当前正在使用的技能，其余全部 |
| `"adjacent"` | 当前技能两侧相邻的技能（1号和4号位只有一侧） |
| `"bare_attack"` | 无额外效果的攻击技能（effects 为空，仅隐式伤害） |
| `"bare_defense"` | 无额外效果的防御技能（effects 为空） |
| `"bare_status"` | 无额外效果的状态技能（effects 为空） |

#### `name` — 按技能名精确筛选（`mod` 可选字段）

`name` 与 `skill_filter` / `element` 正交，引擎先按 `skill_filter`/`element` 粗筛，再按 `name` 精筛。

```jsonc
// 仅"虫鸣"技能威力+20
{ "op": "mod", "target": "sprite_self", "stat": "power", "value": 20, "mode": "add",
  "scope": "permanent", "name": "虫鸣" }
```

| 字段 | 含义 |
|------|------|
| `name` | 技能名称精确匹配 |

#### `skill_where` — 技能属性条件筛选（`mod` 可选字段）

对 `skill_filter`/`element`/`name` 筛选后的技能逐个做数值条件过滤，`q` + `op` + `value` 语义同 `compare`。

```jsonc
// 仅能耗>3的技能威力+40%
{ "op": "mod", "target": "sprite_self", "stat": "power", "value": 40, "mode": "add",
  "scope": "permanent", "skill_filter": "all",
  "skill_where": { "q": "energy_cost", "op": "gt", "value": 3 } }
```

| 字段 | 含义 |
|------|------|
| `skill_where` | `{"q": "energy_cost", "op": "gt", "value": 3}` — 对每技能 eval |

#### `element` — 系别筛选（`mod` 可选字段）

限定只影响指定系别的技能。可与 `skill_filter` 组合：

```jsonc
// 光系技能威力永久+50%
{ "op": "mod", "target": "sprite_self", "stat": "power_mult",
  "value": 1.5, "scope": "permanent", "element": "光" }

// 火系攻击技能能耗-1（element + skill_filter 组合）
{ "op": "mod", "target": "sprite_self", "stat": "energy_cost",
  "value": -1, "mode": "add", "scope": "persistent",
  "element": "火", "skill_filter": "attack" }
```

| element 值 | 筛选 |
|-----------|------|
| 不填 | 所有系别 |
| `"火"` `"水"` `"草"` ... | 仅该系别技能 |
| `"each"` | 每种系别各取至多 `per_element` 个 |

#### `per_element` — 每种系别数量上限（`mod` 可选字段）

配合 `element: "each"` 使用，限定每种系别至多 N 个技能受效果影响。

```jsonc
// 每种系别至多1个技能，威力+35
{ "op": "mod", "target": "sprite_self", "stat": "power",
  "value": 35, "mode": "add", "scope": "permanent",
  "element": "each", "per_element": 1 }
```

引擎按系别分组后，每组取前 N 个技能（按槽位顺序）应用修正。

#### `on_next` + `if_type` — 延迟注入（`mod` 可选字段）

将效果挂到精灵身上，下次使用匹配类型的技能时自动注入 modifier，生效后自消。

```jsonc
// 下次攻击技能威力翻倍
{ "op": "mod", "target": "sprite_self", "stat": "power_mult", "value": 2.0,
  "on_next": true, "if_type": "attack" }
```

| 字段 | 含义 |
|------|------|
| `on_next: true` | 延迟到"下一次"生效，不立即执行 |
| `if_type` | `"attack"` / `"defense"` / `"status"`，不填 = 所有技能 |

#### `mark` — 印记

```jsonc
// 固定层数
{ "op": "mark", "target": "team_own", "name": "光合印记", "stacks": 1 }

// 动态层数
{ "op": "mark", "target": "team_opp", "name": "星陨印记",
  "value": { "q": "mark_count", "of": "team_opp", "name": "any" } }

// 施加成功时执行 then
{ "op": "mark", "target": "team_own", "name": "光合印记", "stacks": 1,
  "then": [
    { "op": "mod", "target": "sprite_self", "stat": "hp", "value": 0.2 }
  ]
}
```

#### `abnormal` — 异常

```jsonc
// 固定层数
{ "op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 1 }

// 动态层数（冻结翻倍 = 读当前层数 ×2 设入）
{ "op": "abnormal", "target": "sprite_opp", "name": "冻结",
  "value": { "q": "abnormal_stacks", "of": "sprite_opp", "name": "冻结", "scale": 2 } }

// 施加成功时执行 then
{ "op": "abnormal", "target": "sprite_self", "name": "萌化", "stacks": 1,
  "then": [
    { "op": "mod", "target": "skill_off_0", "stat": "power", "value": 20, "mode": "add", "scope": "permanent" }
  ]
}
```

`stacks` = 固定层数，`value` = 动态层数（查询表达式），`then` = 施加成功时执行。

#### `weather` — 天气

```jsonc
{ "op": "weather", "weather": "snow", "turns": 8 }
```

#### `dispel` — 驱散

```jsonc
{ "op": "dispel", "target": "sprite_opp", "what": "positive" }
{ "op": "dispel", "target": "sprite_opp", "what": "abnormal", "name": "中毒" }
{ "op": "dispel", "target": "team_both", "what": "mark", "name": "灼烧印记" }
// what: "positive" | "negative" | "mark" | "abnormal"
// name: 可选，指定具体 mark/abnormal 名称；不填=全部
// limit: 可选，驱散层数上限，随机分配
{ "op": "dispel", "target": "sprite_opp", "what": "positive", "limit": 5 }
// type_limit: 可选，驱散种类上限，每种全清
{ "op": "dispel", "target": "sprite_opp", "what": "positive", "type_limit": 1 }
```

#### `steal` — 偷取

将目标的效果层数转移到己方。`target` 指定偷取来源，`what` 指定偷取类型。

```jsonc
{ "op": "steal", "target": "team_opp", "what": "mark" }         // 偷取敌方所有印记
{ "op": "steal", "target": "sprite_opp", "what": "positive" }    // 偷取敌方增益
{ "op": "steal", "target": "team_opp", "what": "mark", "name": "灼烧印记" }  // 偷取指定印记
{ "op": "steal", "target": "sprite_opp", "what": "energy", "amount": 3 }      // 偷取能量（不足时偷取实际剩余量）
// what: "positive" | "mark" | "energy"
// name: 可选，指定名称；不填=全部
// amount: 偷取量（仅 what: "energy"）
```

#### `tick` — 异常结算

触发一次指定异常的伤害结算，是否消耗层数及消耗量由异常类型决定（灼烧=一半向下取整，中毒=不消耗）。

```jsonc
{ "op": "tick", "target": "sprite_opp", "name": "灼烧" }
{ "op": "tick", "target": "sprite_opp", "name": "中毒" }
```

#### `double` — 翻倍

将指定类型效果的层数/步数 ×2。

```jsonc
{ "op": "double", "target": "sprite_self", "what": "positive" }
{ "op": "double", "target": "sprite_opp", "what": "negative" }
{ "op": "double", "target": "sprite_opp", "what": "abnormal", "name": "中毒" }
// what: "positive" | "negative" | "abnormal" | "mark"
// name: 可选，指定具体 abnormal/mark 名称；不填=全部
```

### 控制类

#### `charge` — 蓄力

```jsonc
{ "op": "charge" }
```

#### `escape` — 换宠

```jsonc
{ "op": "escape", "target": "sprite_self" }
{ "op": "escape", "target": "sprite_opp" }        // 强制敌方脱离
{ "op": "escape", "target": "sprite_self", "inherit": true,
  "then": [ /* 换宠后执行 */ ] }
{ "op": "escape", "target": "sprite_self", "urgent": true }
```

| 字段 | 含义 |
|------|------|
| `target` | `sprite_self`=自己脱离，`sprite_opp`=强制敌方脱离 |
| `inherit: true` | 下个入场精灵继承增益（仅 `sprite_self`） |
| `urgent: true` | 紧急脱离：提到伤害之前执行，换上场的精灵承受本次伤害 |
| `then` | 换宠后执行的效果 |

#### `return` — 返场

回合结束时离开战场并重新入场。"返场"是脱出再上场，区别于"换宠"（换另一个人）。

```jsonc
{ "op": "return", "target": "sprite_self" }     // 自己脱出再上场
{ "op": "return", "target": "sprite_opp" }      // 强制敌方脱出再上场
```

#### `lock` — 锁定

```jsonc
{ "op": "lock", "target": "sprite_opp", "turns": 2 }
```

#### `interrupt` — 打断

```jsonc
{ "op": "interrupt", "target": "sprite_opp" }
```

立即终止敌方当前技能的剩余效果执行。仅在应对成功等条件触发时使用。

#### `hit` — 独立伤害

独立造成一次伤害，不依赖技能自身的 `power`/`skill_type`。element 默认继承技能的系别。

```jsonc
// 默认继承技能 element
{ "op": "hit", "power": 90, "type": "物攻" }

// 显式指定系别
{ "op": "hit", "power": 90, "type": "物攻", "element": "火" }
```

| 字段 | 说明 | 默认 |
|------|------|------|
| `power` | 基础威力 | 必填 |
| `type` | `物攻` / `魔攻` | 必填 |
| `element` | 系别 | 继承技能 element |

#### `exchange` — 交换

```jsonc
{ "op": "exchange", "what": "hp_ratio" }     // 交换生命比例
{ "op": "exchange", "what": "effects" }       // 交换增益减益
{ "op": "exchange", "what": "skills" }        // 交换技能
{ "op": "exchange", "what": "adjacent_skills" } // 交换当前技能两侧技能位置
```

#### `reset` — 重置

消除永久增量，将指定 stat 还原到基础值。

```jsonc
// 使用后技能能耗重置
{ "op": "reset", "target": "skill_off_0", "stat": "energy_cost" }
```

#### `redirect` — 重定向

将本技能伤害目标重定向到指定对象。

```jsonc
{ "op": "redirect", "target": "sprite_self" }
```

#### `replay` — 重放历史技能

从精灵技能使用历史中筛选并重放技能。`from` 指定来源（`sprite_self` = 自己用过的技能），`skill_filter` 按技能类型/系别筛选。

```jsonc
// 重放自己释放过的所有迅捷标签技能
{ "op": "replay", "from": "sprite_self", "skill_filter": { "tag": "迅捷" } }

// 重放自己释放过的火系技能
{ "op": "replay", "from": "sprite_self", "skill_filter": { "element": "火" } }

// 重放自己释放过的物攻技能
{ "op": "replay", "from": "sprite_self", "skill_filter": { "skill_type": "物攻" } }

// 重放队伍已触发的所有迸发效果
{ "op": "replay", "from": "team_burst", "what": "burst" }
```

| 字段 | 说明 | 必填 |
|------|------|------|
| `from` | 技能来源：`sprite_self`（自己历史）/ `team_burst`（队伍已触发的迸发效果） | 是 |
| `skill_filter` | 筛选条件，支持 `tag` / `skill_type` / `element` | 否（不填=全部） |

#### `borrow` — 借用技能

复制目标技能的全部属性（威力、技能类型、effects 等）来替代本技能。

```jsonc
// 借用被应对技能的属性
{ "op": "borrow", "from": "skill_opp_current" }
```

| 字段 | 说明 |
|------|------|
| `from` | 技能来源：`skill_opp_current`（对方当前技能） |

---

## 三、控制流 — `when`

```jsonc
// if-else
{
  "when": { "cond": "counter_succeeded" },
  "then": [ { "op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 2.0 } ],
  "else": [ { "op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 1.0 } ]
}

// if-elseif-else
{
  "when": { "cond": "have", "what": "abnormal", "of": "sprite_opp", "name": "中毒", "stacks_ge": 5 },
  "then": [ { "op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 2.0 } ],
  "else_if": [
    {
      "when": { "cond": "have", "what": "abnormal", "of": "sprite_opp", "name": "中毒", "stacks_ge": 3 },
      "then": [ { "op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 1.5 } ]
    }
  ],
  "else": [ { "op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 1.2 } ]
}

// 多系别并列（独立 when，携带多个系别时全部生效）
{ "when": { "cond": "have_skill_of", "of": "sprite_self", "element": "火", "exclude_self": true },
  "then": [ { "op": "abnormal", "target": "sprite_opp", "name": "灼烧", "stacks": 4 } ] },
{ "when": { "cond": "have_skill_of", "of": "sprite_self", "element": "冰", "exclude_self": true },
  "then": [ { "op": "abnormal", "target": "sprite_opp", "name": "冻结", "stacks": 2 } ] }
```

`cond` 合法值：`counter_succeeded` `prev_counter_succeeded` `charged` `is_charging` `burst` `first_action` `on_damage_taken` `on_ko` `on_self_ko` `opp_switched` `self_switched` `opp_is_attack` `is_first` `is_second` `skill_at` `skill_use` `skill_position_changed` `self_was_countered` `have` `have_skill_of` `devotion_triggered` `hp_below` `energy_le` `energy_eq` `energy_depleted` `weather_is` `prev_skill_is` `prev_damage_taken` `sprite_entered` `on_abnormal_tick` `on_abnormal_changed` `on_abnormal_applied` `on_skills_energy_changed` `on_positive_changed` `on_energy_changed` `turn_end` `compare` `and` `or` `not`

### 实现：条件寄存器 + dispatch 表

每个条件是注册到 dispatch 表的一个函数，签名统一 `(ctx, cond) → bool`。`and`/`or`/`not` 是组合子，递归调用 `eval_one`，不需要知道子条件是什么类型。

```python
# 原子条件 — 输入信号（寄存器读）
COND_EVAL = {
    "counter_succeeded":   lambda ctx, cond: ctx.skill_use.countered_skill is not None,
    "self_was_countered": lambda ctx, cond: ctx.skill_use.was_countered,
    "prev_counter_succeeded": lambda ctx, cond: ctx.user.prev_counter_succeeded,
    "charged":           lambda ctx, cond: ctx.skill_use.is_charged,
    "is_charging":       lambda ctx, cond: ctx.user.is_charging,
    "burst":             lambda ctx, cond: ctx.user.first_action,
    "first_action":      lambda ctx, cond: ctx.user.first_action,
    "on_ko":             lambda ctx, cond: ctx.skill_use.target_fainted,
    "on_self_ko":        lambda ctx, cond: ctx.user.self_koed,
    "on_damage_taken":   lambda ctx, cond: ctx.skill_use.damage_taken_this_turn > 0,
    "opp_switched":      lambda ctx, cond: ctx.battle.opp_switched_this_turn,
    "self_switched":     lambda ctx, cond: ctx.battle.self_switched,
    "opp_is_attack":     lambda ctx, cond: ctx.opp_skill_type in ("物攻", "魔攻", "动态攻击"),
    # prev_skill_is: what="attack" → 匹配任何攻击技；skill_type="X" → 精确匹配
    "prev_skill_is":     lambda ctx, cond: (
        ctx.prev_skill_type in ("物攻", "魔攻", "动态攻击") if cond.get("what") == "attack"
        else ctx.prev_skill_type == cond.get("skill_type")
    ),
    "prev_damage_taken": lambda ctx, cond: ctx.get_sprite(cond.get("of", "sprite_self")).prev_damage_taken > 0,
    "is_first":          lambda ctx, cond: ctx.turn.is_first,
    "is_second":         lambda ctx, cond: not ctx.turn.is_first,
    "hp_below":          lambda ctx, cond: ctx.user.hp_ratio < cond["ratio"],
    "energy_le":         lambda ctx, cond: ctx.get_sprite(cond.get("of", "sprite_self")).energy <= cond["value"],
    "energy_eq":         lambda ctx, cond: ctx.get_sprite(cond.get("of", "sprite_self")).energy == cond["value"],
    "energy_depleted":   lambda ctx, cond: ctx.get_sprite(cond.get("of", "sprite_self")).energy == ctx.skill_use.energy_cost,
    "weather_is":        lambda ctx, cond: ctx.battle.weather == cond["weather"],
    "skill_at":                 lambda ctx, cond: ctx.skill_index == cond["position"],
    "skill_position_changed":   lambda ctx, cond: ctx.skill_position_changed,  # 仅 count 用
    "skill_use":         lambda ctx, cond: ctx.skill_use.matches(cond),  # 仅 count 用，不填 filter=本技能自己
    "have_skill_of":     lambda ctx, cond: ctx.get_sprite(cond["of"]).has_skill_of_element(resolve(ctx, cond["element"]) if isinstance(cond["element"], dict) else cond["element"], exclude_self=cond.get("exclude_self", False)),
    "sprite_entered":     lambda ctx, cond: ctx.user.just_entered,  # 本回合入场（仅 count 用）
    "on_abnormal_tick":   lambda ctx, cond: ctx.battle.last_tick_abnormal == cond["name"] and ctx.battle.last_tick_target == cond.get("of", "sprite_opp"),
    "on_abnormal_changed": lambda ctx, cond: ctx.battle.abnormal_changed_name == cond["name"] and ctx.battle.abnormal_changed_target == cond.get("of", "sprite_opp"),
    "on_abnormal_applied": lambda ctx, cond: ctx.battle.abnormal_applied_name == cond["name"] and ctx.battle.abnormal_applied_target == cond.get("of", "sprite_opp"),
    "on_skills_energy_changed": lambda ctx, cond: ctx.battle.skills_energy_changed_of == cond.get("of", "sprite_self"),
    "on_positive_changed": lambda ctx, cond: ctx.battle.positive_changed_of == cond.get("of", "sprite_opp"),
    "on_energy_changed": lambda ctx, cond: ctx.battle.energy_changed_of == cond.get("of", "sprite_self"),
    "turn_end":          lambda ctx, cond: ctx.battle.turn_end,  # 仅 count 用
    "compare":           lambda ctx, cond: compare_op(resolve(ctx, cond), cond["op"], cond["value"]),
    "devotion_triggered": lambda ctx, cond: ctx.skill_use.devotion_triggered_this_action,
    # 逻辑门 — 组合子，递归 eval_one
    "and": lambda ctx, cond: all(eval_one(ctx, c) for c in cond["conditions"]),
    "or":  lambda ctx, cond: any(eval_one(ctx, c) for c in cond["conditions"]),
    "not": lambda ctx, cond: not eval_one(ctx, cond["condition"]),
    "have": lambda ctx, cond: HAVE_EVAL[cond["what"]](ctx, cond),
}

# have 的二级 dispatch
HAVE_EVAL = {
    "abnormal":      lambda ctx, cond: ctx.get_sprite(cond["of"]).has_abnormal(cond.get("name")),
    "mark":          lambda ctx, cond: ctx.get_team(cond["of"]).mark_count(cond.get("name")) > 0,
    "stat_positive":     lambda ctx, cond: ctx.get_sprite(cond["of"]).has_positive_stat(cond.get("stat")),
    "stat_negative":     lambda ctx, cond: ctx.get_sprite(cond["of"]).has_negative_stat(cond.get("stat")),
    "any_stat_positive": lambda ctx, cond: ctx.get_sprite(cond["of"]).has_any_positive_stat(),
    "any_stat_negative": lambda ctx, cond: ctx.get_sprite(cond["of"]).has_any_negative_stat(),
}

def compare_op(a, op, b):
    """泛用比较：lt / le / eq / ge / gt"""
    if op == "lt": return a < b
    if op == "le": return a <= b
    if op == "eq": return a == b
    if op == "ge": return a >= b
    if op == "gt": return a > b
    return False

def eval_one(ctx, cond):
    return COND_EVAL[cond["cond"]](ctx, cond)
```

关键设计：

1. 每个条件不关心自己在哪里被调用 — 在 `when` 里、`and` 里、`count` 里，签名相同
2. `and`/`or`/`not` 是逻辑门组合子，递归调用 `eval_one`，无需知道子条件类型
3. 加新条件 = 加一行注册，不改 `eval_one`，不改引擎主循环
4. `ctx` 是回合快照（不可变），条件函数无副作用 — 纯函数，可任意组合、可重排

---

## 四、计次 — `count`

```jsonc
{
  "op": "count",
  "when": { "cond": "skill_use", "element": "草" },    // 每次草系技能使用
  "then": [
    { "op": "mod", "target": "skill_off_0", "stat": "power", "value": 6, "scope": "permanent" }
  ]
}

// 受到伤害计次
{
  "op": "count",
  "when": { "cond": "on_damage_taken" },
  "then": [
    { "op": "mod", "target": "skill_off_0", "stat": "energy_cost", "value": 1, "scope": "permanent" }
  ]
}

// 命名计数（供 counter_value 查询，无 then = 仅追踪次数）
{
  "op": "count",
  "name": "星陨",
  "when": { "cond": "skill_use" }
}
```

| 字段 | 含义 | 必填 |
|------|------|------|
| `name` | 计数器名称，可通过 `counter_value` 查询 | 否（不填=匿名，仅触发 then） |

### 实现：持久化 `when` + 事件监听

`count` 本质是持久化的 `when`：技能使用时注册一个监听器到技能实例上，后续每次相关事件发生都 eval 条件，触发则执行 `then` 并递增计数器。

```python
@dataclass
class Counter:
    """持久化条件监听器。挂载在技能实例上，跨回合存活直到被 scope 清除。"""
    cond: dict          # 触发条件（与 when 共用 COND_EVAL）
    then: list[dict]    # 触发时执行的 effects
    count: int = 0      # 已触发次数
    scope: str = "persistent"  # persistent = 本场战斗, battlefield = 换宠清除

# ── 引擎 ──

def register_counters(skill_use, effects):
    """技能使用时：遍历 effects，遇到 count 就注册 Counter。"""
    for eff in effects:
        if eff.get("op") == "count":
            counter = Counter(
                cond=eff["when"],
                then=eff["then"],
                scope=eff.get("scope", "persistent"),
            )
            skill_use.counters.append(counter)

def fire_counters(skill_use, ctx):
    """事件发生时（每次技能使用 / 受到伤害 / KO 等）调用。
    遍历所有已注册 Counter，条件为真则执行 then。"""
    events = []
    for c in skill_use.counters:
        if eval_one(ctx, c.cond):
            for action in c.then:
                events += execute_op(action, ctx)
            c.count += 1
    return events
```

关键点：

- `count.when` 和 `when.cond` 共用同一个 `COND_EVAL` 表 — 条件函数签名统一，不加新代码
- `Counter` 挂在技能实例上，靠 `scope` 决定生命周期（`persistent` = 跨回合，`battlefield` = 换宠清除）
- `fire_counters` 在引擎主循环中每次相关事件后调用 — 技能使用后、受到伤害后、KO 后等
- 计数器本身不关心"是什么事件触发了它" — 事件发生时引擎遍历所有 Counter，各自 eval，命中则执行

---

## 五、执行时序 — `feeds` / `needs`

effect 不指定"我在第几层"，而是声明"我往哪个池子放东西 / 我消费哪个池子的结果"。引擎拓扑排序。

### 6 个 token

| Token | 用于 `feeds` | 用于 `needs` |
|-------|-------------|-------------|
| `"cost"` | 我修改能耗 → 排在 Gate 前 | — |
| `"power"` | 我修改威力 → 排在威力确定前 | — |
| `"mult"` | 我修改伤害倍率 → 排在伤害公式前 | — |
| `"result"` | — | 我需要伤害/KO 结果 → 排在伤害后 |
| `"counter"` | — | 我需要反击结束 → 排在最后 |
| `"turn_end"` | — | 我需要在回合末结算 |

### 引擎自动排序

```
feeds:cost → Gate(付能耗) → feeds:power → 威力确定 → feeds:mult
  → 伤害 → result 就绪
  → 默认(无声明) + needs:result(按数组顺序)
  → 反击 → counter 就绪 → needs:counter
  → 回合末 → needs:turn_end
```

无声明 effect 在 `result` 就绪之后、反击之前按数组顺序执行。

### 示例

```jsonc
// 绝境反击：动态威力(feeds:power) + 使用后自扣全部HP(默认)
{ "op": "mod", "target": "skill_off_0", "stat": "power",
  "value": { "q": "hp_missing_ratio", "of": "sprite_self", "per": 0.05, "scale": -10 },
  "feeds": "power" }
{ "op": "mod", "target": "sprite_self", "stat": "hp",
  "value": { "q": "hp_current", "of": "sprite_self", "scale": -1 } }

// 四维降解：动态能耗(feeds:cost)
{ "op": "mod", "target": "skill_off_0", "stat": "energy_cost",
  "value": { "q": "mark_count", "of": "team_opp", "scale": -1, "name": "any" },
  "feeds": "cost" }

// 回旋踢：威力翻倍(feeds:mult)
{ "op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 2.0,
  "feeds": "mult" }

// 嗜痛：减伤(feeds:mult)
{ "op": "mod", "target": "sprite_self", "stat": "damage_reduction", "value": 0.8,
  "feeds": "mult" }

// 击鼓传花：脱离 → 等反击结束(needs:counter)
{ "op": "escape", "target": "sprite_self", "inherit": true,
  "needs": "counter" }

// 回合末永久减能耗(needs:turn_end)
{ "op": "mod", "target": "skill_off_0", "stat": "energy_cost",
  "value": -5, "mode": "add", "scope": "permanent",
  "needs": "turn_end" }
```

---

## 六、通用字段

### 技能级字段（skill body）

| 字段 | 含义 | 默认值 |
|------|------|--------|
| `element` | 系别，支持静态字符串或 `{ "q": "weather", "of": "battle" }` 等查询表达式 | `"普通"` |
| `tag` | 机制标签：`"迅捷"` / `"传动"` 等 | 无 |
| `use_devotion` | `true` = 本技能触发队伍已积累的奉献效果 | `false` |
| `usable_while_charging` | `true` = 蓄力状态下可选用本技能 | `false` |
| `position_locked` | `true` = 本技能位置不被交换类效果移动 | `false` |
| `morph` | 变身：每回合变成指定来源的技能 `{ "from": "team_own"` or `"team_opp"`, `"mode": "random"`, `"exclude_self": true }` | 无 |
| `passive` | 被动效果数组，装备即生效，不依赖使用 | `[]` |

### Effect 级字段

所有 effect 共享：

| 字段 | 含义 | 默认值 |
|------|------|--------|
| `scope` | `"battlefield"` / `"persistent"` / `"permanent"` | `"battlefield"` |
| `feeds` | `"cost"` / `"power"` / `"mult"` | 不填=默认位置（伤害后） |
| `needs` | `"result"` / `"counter"` / `"turn_end"` | 不填=默认位置（伤害后） |
| `delay` | 延迟 N 回合后再生效，引擎回合开始时扣减并结算 | `0` |
| `ttl` | 效果存活回合数，每回合末扣减，归零自动消除 | 不填=永久 |
| `per_hit` | `true` → 每次连击都触发一次；不填 → 整次技能触发一次 | 不填 |
| `cooldown` | 效果冷却（次） | 0 |
| `priority` | 同池内优先级 | 0 |

---

## 六、编译示例

### 示例1：升龙咆哮

```
描述：蓄力，造成魔伤。
```

```jsonc
{
  "id": 10901,
  "name": "升龙咆哮",
  "element": "龙",
  "skill_type": "魔攻",
  "power": 200,
  "energy_cost": 3,
  "effects": [
    {
      "when": { "cond": "charged" },
      "then": [],
      "else": [ { "op": "charge" } ]
    }
  ],
  "description": "✦蓄力，对敌方造成魔法伤害。"
}
```

### 示例2：嗜痛

```
描述：减伤80%，期间每受到一次伤害，双攻+40%。
```

```jsonc
{
  "id": 10322,
  "name": "嗜痛",
  "element": "普通",
  "skill_type": "防御",
  "energy_cost": 2,
  "counter": "攻击",
  "effects": [
    { "op": "mod", "target": "sprite_self", "stat": "damage_reduction", "value": 0.8 },
    {
      "when": { "cond": "on_damage_taken" },
      "then": [
        { "op": "mod", "target": "sprite_self", "stat": "atk", "steps": 4 },
        { "op": "mod", "target": "sprite_self", "stat": "sp_atk", "steps": 4 }
      ]
    }
  ],
  "description": "✦减伤80%，应对攻击：期间自己每次受到伤害，获得双攻+40%。"
}
```

### 示例3：四维降解

```
描述：造成魔伤，敌方每有1层印记，本技能能耗-1。
```

```jsonc
{
  "id": 10155,
  "name": "四维降解",
  "element": "幻",
  "skill_type": "魔攻",
  "power": 100,
  "energy_cost": 7,
  "effects": [
    {
      "op": "mod",
      "target": "skill_off_0",
      "stat": "energy_cost",
      "value": { "q": "mark_count", "of": "team_opp", "scale": -1, "name": "any" },
      "scope": "persistent",
      "phase": "turn_start"
    }
  ],
  "description": "✦造成魔伤，敌方每有1层印记，本技能能耗-1。"
}
```

### 示例4：幼态延续

```
描述：造成魔伤，自身拥有萌化时威力+60。
```

```jsonc
{
  "id": 10820,
  "name": "幼态延续",
  "element": "萌",
  "skill_type": "魔攻",
  "power": 90,
  "energy_cost": 4,
  "effects": [
    {
      "when": { "cond": "have", "what": "abnormal", "of": "sprite_self", "name": "萌化" },
      "then": [
        { "op": "mod", "target": "skill_off_0", "stat": "power", "steps": 6 }
      ]
    }
  ],
  "description": "造成魔伤，自身拥有萌化时威力+60。"
}
```

### 示例5：回旋踢

```
描述：造成物伤，若敌方本回合更换精灵，本次技能威力翻倍。
```

```jsonc
{
  "id": 10456,
  "name": "回旋踢",
  "element": "武",
  "skill_type": "物攻",
  "power": 80,
  "energy_cost": 3,
  "effects": [
    {
      "when": { "cond": "opp_switched" },
      "then": [
        { "op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 2.0 }
      ]
    }
  ],
  "description": "✦造成物伤，若敌方本回合更换精灵，本次技能威力翻倍。"
}
```

### 示例6：应激反应（if-else）

```
描述：回复25%生命，应对防御：改为回复50%生命。
```

```jsonc
{
  "id": 10327,
  "name": "应激反应",
  "element": "普通",
  "skill_type": "状态",
  "energy_cost": 2,
  "counter": "防御",
  "effects": [
    {
      "when": { "cond": "counter_succeeded" },
      "then": [
        { "op": "mod", "target": "sprite_self", "stat": "hp", "value": 0.5 }
      ],
      "else": [
        { "op": "mod", "target": "sprite_self", "stat": "hp", "value": 0.25 }
      ]
    }
  ],
  "description": "✦自己回复25%生命，应对防御：改为回复50%生命。"
}
```

### 示例7：啃咬（计次）

```
描述：造成物伤，1连击，本技能会受奉献影响，每被影响1次，能耗永久+1。
```

```jsonc
{
  "id": 10852,
  "name": "啃咬",
  "element": "虫",
  "skill_type": "物攻",
  "power": 40,
  "energy_cost": 0,
  "combo": 1,
  "effects": [
    { "op": "mod", "target": "sprite_self", "stat": "devotion", "value": 1 },
    {
      "op": "count",
      "when": { "cond": "devotion" },
      "then": [
        { "op": "mod", "target": "skill_off_0", "stat": "energy_cost", "value": 1, "scope": "permanent" }
      ]
    }
  ],
  "description": "✦造成物伤，1连击，本技能会受奉献影响，每被影响1次，能耗永久+1。"
}
```

### 示例8：铁壁（and 组合条件）

```
描述：应对攻击：应对成功且受到伤害时，防御+10%。
```

```jsonc
{
  "id": 10399,
  "name": "铁壁",
  "element": "地",
  "skill_type": "防御",
  "energy_cost": 2,
  "counter": "攻击",
  "effects": [
    {
      "when": {
        "cond": "and",
        "conditions": [
          { "cond": "counter_succeeded" },
          { "cond": "on_damage_taken" }
        ]
      },
      "then": [
        { "op": "mod", "target": "sprite_self", "stat": "def", "steps": 1 }
      ]
    }
  ],
  "description": "✦应对攻击：应对成功且受到伤害时，防御+10%。"
}
```

`counter`（技能级声明） + `and`（逻辑组合） + `mod`（动作），三个已有基元，无需新字段。

### 示例9：迅捷回放

```
描述：释放自己释放过的迅捷技能，其能耗之和的二分之一加至本技能能耗，每次使用后能耗+1。
```

```jsonc
{
  "id": 10xxx,
  "name": "迅捷回放",
  "element": "普通",
  "skill_type": "状态",
  "tag": "迅捷",
  "energy_cost": 0,
  "effects": [
    {
      "op": "replay",
      "from": "sprite_self",
      "skill_filter": { "tag": "迅捷" }
    },
    {
      "op": "mod",
      "target": "skill_off_0",
      "stat": "energy_cost",
      "value": { "q": "energy_cost_sum", "of": "sprite_self", "tag": "迅捷", "scale": 0.5 },
      "feeds": "cost"
    },
    {
      "op": "count",
      "when": { "cond": "skill_use" },
      "then": [
        { "op": "mod", "target": "skill_off_0", "stat": "energy_cost", "value": 1, "scope": "permanent" }
      ]
    }
  ],
  "description": "✦释放自己释放过的迅捷技能，其能耗之和的二分之一加至本技能能耗，每次使用后能耗+1。"
}
```

新 op `replay` + `tag` 技能标签 + `energy_cost_sum`（支持 `tag` 索引）+ `count` 计次。

---

## 七、与旧 guide 的映射

| 旧 guide | RISC IR | 说明 |
|----------|---------|------|
| `stat` `stat: atk, steps: 3` | `mod` `stat: atk, steps: 3` | 合并 |
| `power_bonus` `amount: 20` | `mod` `stat: power, value: 20` | 合并 |
| `power_mult` `value: 2.0` | `mod` `stat: power_mult, value: 2.0` | 合并 |
| `heal` `value: 0.3` | `mod` `stat: hp, value: 0.3` | 合并 |
| `gain_energy` `amount: 2` | `mod` `stat: energy, value: 2` | 合并 |
| `damage_reduction` `value: 0.5` | `mod` `stat: damage_reduction, value: 0.5` | 合并 |
| `multi_hit` `amount: 6` | `mod` `stat: combo, value: 6` | 合并 |
| `combo_increment` | `count` + `mod` `stat: combo` | 计次替代 |
| `power_increment` | `count` + `mod` `stat: power` | 计次替代 |
| `energy_cost_increment` | `count` + `mod` `stat: energy_cost` | 计次替代 |
| `power_double` | `mod` `stat: power_mult, value: 2.0` | 删除语法糖 |
| `power_double_on_ko` | `when` `on_ko` + `mod` | 删除语法糖 |
| `counter_power_mult` | `when` `counter_succeeded` + `mod` | 删除语法糖 |
| `mark` | `mark` | 不变 |
| `abnormal` | `abnormal` | 不变 |
| `weather` | `weather` | 不变 |
| `charge` | `charge` | 不变 |
| `escape` / `escape_inherit` | `escape` `inherit: true` | 不变 |
| `dispel_positive` / `dispel_negative` | `dispel` `what: positive/negative` | 合并 |
| `double_positive` / `double_negative` / `double_abnormal` / `double_mark` | 暂保留在旧guide，可考虑纳入 `dispel` + `mod` 组合 | — |
| `exchange_*` | `exchange` | 合并 |
| `tally` | `count` | 重命名，语义更清晰 |
| `conditional` | `when` | 内联到每个effect |

**旧 guide ~60 个 kind → RISC IR 12 个基元 + 查询表达式**
