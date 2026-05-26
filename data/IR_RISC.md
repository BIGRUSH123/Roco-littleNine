# IR — RISC 寄存器虚拟机设计

> **A Register-Based Domain VM for Turn-Based Battle Skills**
>
> 这是一个**基于寄存器的领域虚拟机**，为回合制对战游戏的技能/特性编译
> 到统一指令集而设计。"RISC" 代表设计目标而非严格定义——在回合制对战领域，
> 存在必要的务实偏离。详见 [附录 C — RISC 原则：遵循与偏离](#c-risc-原则遵循与偏离)。

## 设计原则

**只有一套 IR。** 技能和特性编译到同一套 RISC opcode 指令集，差异仅在于**触发方式**和**生命周期**：

```
技能 JSON               特性 JSON
  │                       │
  ▼                       ▼
effects[]              TraitToObserver 转换
  │                       │
  ▼                       ▼
Skill VM 即时执行      Observer { cond, then: [RiscIROp...], scope }
                        │
                        ▼
                      事件触发 → Skill VM 执行 then
```

**运行时只有一种执行路径：RISC VM。** 特性 = 持久化条件监听器（Observer），触发后执行的 `then` 块就是普通的 RISC IR opcode 数组。

| 维度 | 技能 | 特性 |
|------|------|------|
| 存储格式 | `effects[]` | `triggers[].{on, condition, effects[]}` |
| 编译目标 | 直接执行 | `Observer { cond, then, scope }` |
| then 内容 | — | **Skill IR opcode 数组** |
| 执行器 | RISC VM | RISC VM（事件触发后） |
| 生命周期 | 瞬时 | 持久（scope 控制） |

---

## 三层模型

| 层 | 对应 | 职责 |
|----|------|------|
| 描述层 | JSON 数据 | "敌方每有1层印记，本技能能耗-1" |
| **IR 层** | **opcode 指令集** | **统一的 op + target + value 组合** |
| 引擎层 | Skill VM + Observer 引擎 | 解释执行 / 事件触发 |

---

## 执行模型 — Ctx 寄存器组 + EventContext

`Ctx` 是回合快照寄存器组。回合开始时构建，**本回合内所有指令只读寄存器**。
事件瞬时标志（trigger 级别的 "刚刚发生了什么"）存放在独立的 `EventContext` 子对象中。

### EventContext — 事件瞬时上下文

```python
@dataclass
class EventContext:
    """Per-trigger event flags — 仅 Observer 触发时有效。"""
    counter_succeeded: bool = False       # 本次应对成功
    was_countered: bool = False           # 本次被应对
    prev_counter_succeeded: bool = False  # 上次行动应对成功
    target_fainted: bool = False          # 目标力竭
    self_koed: bool = False               # 己方力竭
    opp_switched: bool = False            # 敌方切换
    self_switched: bool = False           # 己方切换
    turn_end: bool = False                # 回合结束
    skill_position_changed: bool = False  # 技能被换位
    devotion_triggered: bool = False      # 奉献触发
    last_tick_abnormal: str = ""          # 最后 tick 的异常名
    last_tick_target: str = ""            # 最后 tick 的目标
    abnormal_changed_name: str = ""       # 刚变化的异常名
    abnormal_changed_target: str = ""     # 刚变化的目标
    abnormal_applied_name: str = ""       # 刚施加的异常名
    abnormal_applied_target: str = ""     # 刚施加的目标
    skills_energy_changed_of: str = ""    # 能耗变化方
    positive_changed_of: str = ""         # 增益变化方
    energy_changed_of: str = ""           # 能量变化方
```

### Ctx — 战斗状态快照

```python
@dataclass
class Ctx:
    # ── 事件上下文 ──
    event: EventContext                   # ctx.event.X 访问事件标志

    # ── 己方精灵 ──
    hp_self: int; hp_self_ratio: float; hp_self_max: int
    energy_self: int
    atk_self: int; def_self: int; sp_atk_self: int; sp_def_self: int
    speed_self: int
    priority_self: int                    # 行动优先级修饰
    damage_reduction_self: float          # 0.0=无减伤, 1.0=免疫
    abnormal_count_self: int
    abnormal_stacks_self: dict[str, int]
    positive_count_self: int
    first_action_self: bool
    charged_self: bool; is_charging_self: bool
    times_entered_self: int; times_left_self: int
    just_entered: bool                    # 本回合入场
    elements_used_count_self: int
    skills_energy_sum_self: int
    zero_cost_skill_count_self: int
    energy_cost_reduction_self: int
    energy_cost_sum_self: dict[str, int]
    damage_reduced_self: int
    damage_taken_this_turn: int
    last_tick_damage_self: int
    skill_elements_self: frozenset        # 携带技能的元素集合
    stat_stages_self: dict[str, int]      # {stat: stage}
    power_mult_self: float; damage_mult_self: float
    energy_cost_mult_self: float; combo_mult_self: float
    life_drain_self: float
    mark_bonus_own: float                 # 己方印记伤害加成

    # ── 敌方精灵 ──
    hp_opp: int; hp_opp_ratio: float; hp_opp_max: int
    energy_opp: int
    atk_opp: int; def_opp: int; sp_atk_opp: int; sp_def_opp: int
    speed_opp: int
    damage_reduction_opp: float
    abnormal_count_opp: int; abnormal_stacks_opp: dict[str, int]
    positive_count_opp: int
    charged_opp: bool
    last_tick_damage_opp: int
    skills_energy_sum_opp: int
    skill_elements_opp: frozenset
    stat_stages_opp: dict[str, int]
    power_mult_opp: float; damage_mult_opp: float

    # ── 双方队伍 ──
    mark_count_own: int; mark_stacks_own: dict[str, int]
    mark_count_opp: int; mark_stacks_opp: dict[str, int]
    skill_count_own: dict[str, int]
    team_counters_own: dict[str, int]; team_counters_opp: dict[str, int]
    devotion_own: dict[str, int]; devotion_opp: dict[str, int]
    abnormal_stacks_battle: dict[str, int]
    fainted_own: int; fainted_opp: int
    lives_own: int = 5; lives_opp: int = 5
    burst_triggered_count_own: int

    # ── 技能（当前发动的技能）──
    power_self: int; adjacent_power_sum: int
    power_opp: int
    skill_type_self: str; skill_type_opp: str
    element_self: str; element_opp: str
    skill_tag_self: str; skill_name_self: str
    combo_self: int
    energy_cost_self: int; energy_cost_opp: int
    energy_delta_self: int
    prev_skill_type: str
    prev_damage_taken_self: bool; prev_damage_taken_opp: bool

    # ── 战场 ──
    weather: str
    turn: int; is_first: bool
    skill_index: int

    # ── 计次器快照 ──
    counter_values: dict[str, int]
```

> **派生查询**: `hp_missing_ratio` 和 `mark_count_both` 不再存储为字段。
> 查询时由 `resolve.py` 动态计算：`hp_missing_ratio = 1.0 - hp_ratio`，`mark_count_both = mark_count_own + mark_count_opp`。

### 寻址：ADDRESS_MAP + 自动校验

```python
ADDRESS_MAP: dict[tuple[str, str], str] = {
    # sprite_self (32 条目)
    ("sprite_self", "hp"):                 "hp_self",
    ("sprite_self", "hp_ratio"):           "hp_self_ratio",
    ("sprite_self", "hp_max"):             "hp_self_max",
    ("sprite_self", "energy"):             "energy_self",
    ("sprite_self", "priority"):           "priority_self",
    ("sprite_self", "atk"):               "atk_self",
    ("sprite_self", "def"):               "def_self",
    ("sprite_self", "sp_atk"):            "sp_atk_self",
    ("sprite_self", "sp_def"):            "sp_def_self",
    ("sprite_self", "speed"):             "speed_self",
    ("sprite_self", "abnormal_count"):     "abnormal_count_self",
    ("sprite_self", "abnormal_stacks"):    "abnormal_stacks_self",
    ("sprite_self", "positive_count"):     "positive_count_self",
    ("sprite_self", "charged"):            "charged_self",
    ("sprite_self", "is_charging"):        "is_charging_self",
    ("sprite_self", "first_action"):       "first_action_self",
    ("sprite_self", "times_entered"):      "times_entered_self",
    ("sprite_self", "times_left"):         "times_left_self",
    ("sprite_self", "elements_used_count"):"elements_used_count_self",
    ("sprite_self", "zero_cost_skill_count"): "zero_cost_skill_count_self",
    ("sprite_self", "skills_energy_sum"):  "skills_energy_sum_self",
    ("sprite_self", "energy_cost_sum"):    "energy_cost_sum_self",
    ("sprite_self", "damage_reduction"):   "damage_reduction_self",
    ("sprite_self", "damage_reduced"):     "damage_reduced_self",
    ("sprite_self", "last_tick_damage"):   "last_tick_damage_self",
    ("sprite_self", "adjacent_power_sum"): "adjacent_power_sum",
    ("sprite_self", "power_mult"):         "power_mult_self",
    ("sprite_self", "damage_mult"):        "damage_mult_self",
    ("sprite_self", "energy_cost_mult"):   "energy_cost_mult_self",
    ("sprite_self", "combo_mult"):         "combo_mult_self",
    ("sprite_self", "life_drain"):         "life_drain_self",
    ("sprite_self", "mark_bonus"):         "mark_bonus_own",

    # sprite_opp (18 条目)
    ("sprite_opp", "hp"):                  "hp_opp",
    ("sprite_opp", "hp_ratio"):            "hp_opp_ratio",
    ("sprite_opp", "hp_max"):             "hp_opp_max",
    ("sprite_opp", "energy"):             "energy_opp",
    ("sprite_opp", "atk"):                 "atk_opp",
    ("sprite_opp", "def"):                 "def_opp",
    ("sprite_opp", "sp_atk"):              "sp_atk_opp",
    ("sprite_opp", "sp_def"):              "sp_def_opp",
    ("sprite_opp", "speed"):              "speed_opp",
    ("sprite_opp", "abnormal_count"):      "abnormal_count_opp",
    ("sprite_opp", "abnormal_stacks"):     "abnormal_stacks_opp",
    ("sprite_opp", "positive_count"):      "positive_count_opp",
    ("sprite_opp", "charged"):             "charged_opp",
    ("sprite_opp", "damage_reduction"):    "damage_reduction_opp",
    ("sprite_opp", "last_tick_damage"):    "last_tick_damage_opp",
    ("sprite_opp", "skills_energy_sum"):   "skills_energy_sum_opp",
    ("sprite_opp", "power_mult"):          "power_mult_opp",
    ("sprite_opp", "damage_mult"):         "damage_mult_opp",

    # battle
    ("battle", "abnormal_stacks"):         "abnormal_stacks_battle",
    ("battle", "weather"):                 "weather",

    # team_own / team_opp
    ("team_own", "mark_count"):            "mark_count_own",
    ("team_own", "mark_stacks"):           "mark_stacks_own",
    ("team_own", "skill_count"):           "skill_count_own",
    ("team_own", "team_counter"):          "team_counters_own",
    ("team_own", "devotion"):              "devotion_own",
    ("team_own", "fainted"):              "fainted_own",
    ("team_own", "burst_triggered_count"): "burst_triggered_count_own",
    ("team_opp", "mark_count"):            "mark_count_opp",
    ("team_opp", "mark_stacks"):           "mark_stacks_opp",
    ("team_opp", "team_counter"):          "team_counters_opp",
    ("team_opp", "devotion"):              "devotion_opp",
    ("team_opp", "fainted"):              "fainted_opp",

    # skill_off_0 / skill_opp_current
    ("skill_off_0", "power_base"):         "power_self",
    ("skill_off_0", "element"):            "element_self",
    ("skill_off_0", "adjacent_power_sum"): "adjacent_power_sum",
    ("skill_off_0", "combo_current"):      "combo_self",
    ("skill_off_0", "energy_cost"):        "energy_cost_self",
    ("skill_off_0", "counter_value"):      "counter_values",
    ("skill_off_0", "energy_cost_reduction"): "energy_cost_reduction_self",
    ("skill_opp_current", "power_base"):   "power_opp",
    ("skill_opp_current", "element"):       "element_opp",
    ("skill_opp_current", "energy_total"): "energy_cost_opp",
}
```

**自动校验** — 模块导入时运行，确保 ADDRESS_MAP 的每个条目指向实际 Ctx 字段：

```python
def _validate_address_map() -> None:
    """Verify every ADDRESS_MAP entry points to an actual Ctx field."""
    valid_fields = set(Ctx.__dataclass_fields__)
    for (of, q), field_name in ADDRESS_MAP.items():
        if field_name not in valid_fields:
            raise AttributeError(
                f"ADDRESS_MAP ({of}, {q}) -> '{field_name}' is not a Ctx field"
            )
_validate_address_map()
```

### 执行流程

```
每回合:
  1. 两边选手输入技能
  2. 拍快照 → Ctx + EventContext 就绪（只读）
  3. priority 排序 → 决定执行顺序
  4. 逐技能:
     a. 取指: 读 effects[]
     b. 译码: op → 选择 handler
     c. 寻址: Query → resolve(ctx, value)
     d. 执行: feeds 拓扑排序 → 写回
     e. observer 注册
  5. 回合末: 写回精灵对象 → fire Observer → ctx.event 填充事件标志
     → 触发特性的 then[]（同样走 Skill VM）
```

---

## 一、值表达式 — `value`

### Literal — 字面量

```jsonc
5                    // 整数
0.5                  // 小数
"火"                 // 字符串
true                 // 布尔
```

### Query — 寄存器查询

```jsonc
{ "q": "energy", "of": "sprite_self" }
{ "q": "hp_ratio", "of": "sprite_opp" }
{ "q": "mark_count", "of": "team_own" }
{ "q": "fainted", "of": "team_opp" }
{ "q": "element", "of": "skill_opp_current" }

// 带修饰
{ "q": "energy", "of": "sprite_opp", "scale": 10 }
{ "q": "hp_missing_ratio", "of": "sprite_self", "per": 0.1 }
{ "q": "weather", "of": "battle", "default": "普通" }
```

### RefExpr — 路径表达式（特性 JSON 中用 `=@` 前缀）

```jsonc
"=@self.energy * 2"
"=@self.effects[name=灼烧].stacks"
"=@player_fainted_count * 3 + opponent_fainted_count * 3"
"=@team_counters[counter_success] * 5"
```

`TraitToObserver` 编译器在加载时将 `=@` 表达式编译为 `RefExpr(root, path, multiplier, offset)`，运行时 O(1) 解析。多 `@` 引用或跨引用运算保留为 Literal 字符串，运行时 eval。

---

## 二、RISC 指令集

> **设计原则**：每条指令产生**一种** mutation 类型，不再通过 `stat` 参数隐式分发。
> 原 Authoring IR 的 `mod`/`count`/`schedule`/`when` 由编译器转换为以下 RISC opcode。
> 详见 [附录 D — 从旧 IR 迁移](#d-从旧-ir-迁移)。

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

### 2A. 寄存器修改类 — 替代 `mod`

原 `mod` 操作码按 mutation 类型拆分为 7 个独立 opcode，每条指令产生一种明确的 mutation：

#### `stat_stage` — 属性阶段修正

修改精灵的 atk/def/sp_atk/sp_def/speed 阶段值。产生 `StatChange` mutation。

```jsonc
// 基础用法 — steps 累加语义
{ "op": "stat_stage", "target": "sprite_self", "stat": "atk", "steps": 3 }
{ "op": "stat_stage", "target": "sprite_opp", "stat": "def", "steps": -2 }

// 动态值 — Query / RefExpr
{ "op": "stat_stage", "target": "sprite_self", "stat": "atk",
  "steps": "=@player_bug_count", "scope": "battlefield", "source": "虫群鼓舞" }
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `stat` | `"atk"/"def"/"sp_atk"/"sp_def"/"speed"` | 目标属性 |
| `steps` | `int` / Query / RefExpr | 阶段变化量（正=增益，负=减益） |
| `scope` | `str` | 生命周期 |
| `source` | `str` | 效果来源（追踪/驱散用） |

#### `power_mod` — 技能属性修正

修改技能的 power / energy_cost / combo / priority 等属性。产生 `SkillMod` mutation。

```jsonc
// 基础用法 — delta 累加语义
{ "op": "power_mod", "target": "skill_off_0", "attr": "power", "delta": 20 }
{ "op": "power_mod", "target": "skill_off_0", "attr": "energy_cost", "delta": -1,
  "scope": "persistent", "ttl": 3 }

// 批量筛选 — skill_where
{ "op": "power_mod", "target": "sprite_self", "attr": "power", "delta": 20,
  "skill_where": {"element": "火"} }

// 动态值 — Query
{ "op": "power_mod", "target": "skill_off_0", "attr": "energy_cost",
  "delta": {"q": "mark_count", "of": "team_opp", "scale": -1, "name": "any"} }
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `attr` | `"power"/"energy_cost"/"combo"/"priority"/"energy_cost_mult"/"combo_mult"/"energy_cost_delta_mult"` | 目标属性 |
| `delta` | `int` / Query / RefExpr | 变化量 |
| `skill_where` | `dict` | 技能筛选条件 |
| `scope` | `str` | 生命周期 |

#### `mult_mod` — 倍率修正

修改伤害/威力倍率。产生 `MultiplierMod` mutation。

```jsonc
{ "op": "mult_mod", "target": "sprite_self", "attr": "damage_reduction", "value": 0.4 }
{ "op": "mult_mod", "target": "skill_off_0", "attr": "power_mult", "value": 2.0 }
{ "op": "mult_mod", "target": "sprite_self", "attr": "life_drain", "value": 0.5 }
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `attr` | `"power_mult"/"damage_mult"/"damage_reduction"/"life_drain"` | 目标倍率 |
| `value` | `float` / Query | 倍率值（1.0=不变） |

#### `flag_set` — 布尔标记

设置/清除 boolean 标记。产生 `FlagSet` mutation。

```jsonc
{ "op": "flag_set", "target": "sprite_self", "flag": "immune", "name": "灼烧", "value": true }
{ "op": "flag_set", "target": "skill_off_0", "flag": "swift", "value": true }
{ "op": "flag_set", "target": "sprite_self", "flag": "freeze_immune", "value": true }
```

| flag 值 | 含义 |
|---------|------|
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

#### `heal` — HP 操作

回复或扣除 HP。产生 `Heal` mutation。

```jsonc
{ "op": "heal", "target": "sprite_self", "ratio": 0.5 }
{ "op": "heal", "target": "sprite_self", "value": 100 }
{ "op": "heal", "target": "sprite_opp", "value": -50 }
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `ratio` | `float` | HP 比例（0.5=半血） |
| `value` | `int` | 固定数值（负=伤害） |

#### `energize` — 能量操作

回复或扣除能量。产生 `EnergyChange` mutation。

```jsonc
{ "op": "energize", "target": "sprite_self", "delta": 2 }
{ "op": "energize", "target": "sprite_opp", "delta": -3 }
```

#### `revive` — 复活

复活力竭精灵。引擎处理：hp = max(1, max_hp × hp_ratio)，清除力竭标记，同队当前位力竭时自动上场。

```jsonc
{ "op": "revive", "target": "sprite_self", "hp_ratio": 1.0 }
```

### 2B. 状态效果类

这些 opcode 已经是单语义指令，符合 RISC 原则：

#### `mark` — 印记

```jsonc
{ "op": "mark", "target": "team_own", "name": "光合印记", "stacks": 1 }
{ "op": "mark", "target": "team_opp", "name": "星陨印记",
  "value": {"q": "mark_count", "of": "team_opp", "name": "any"} }
```

#### `abnormal` — 异常

```jsonc
{ "op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 1 }
{ "op": "abnormal", "target": "sprite_opp", "name": "冻结",
  "stacks": {"q": "abnormal_stacks", "of": "sprite_opp", "name": "冻结", "scale": 2} }
{ "op": "abnormal", "target": "sprite_opp", "name": "聒噪", "stacks": 1, "duration": 3 }
```

#### `weather` — 天气

```jsonc
{ "op": "weather", "weather": "snow", "turns": 8 }
```

#### `dispel` — 驱散

```jsonc
{ "op": "dispel", "target": "sprite_opp", "what": "positive" }
{ "op": "dispel", "target": "sprite_opp", "what": "abnormal", "name": "中毒" }
{ "op": "dispel", "target": "team_both", "what": "mark", "name": "灼烧印记" }
{ "op": "dispel", "target": "sprite_opp", "what": "positive", "limit": 5 }
// what: "positive" | "negative" | "mark" | "abnormal"
```

#### `steal` — 偷取

```jsonc
{ "op": "steal", "target": "team_opp", "what": "mark" }
{ "op": "steal", "target": "sprite_opp", "what": "positive" }
{ "op": "steal", "target": "sprite_opp", "what": "energy", "amount": 3 }
```

#### `tick` — 异常结算

```jsonc
{ "op": "tick", "target": "sprite_opp", "name": "灼烧" }
```

#### `double` — 翻倍

```jsonc
{ "op": "double", "target": "sprite_self", "what": "positive" }
{ "op": "double", "target": "sprite_opp", "what": "abnormal", "name": "中毒" }
```

### 2C. 战斗流控类

#### `hit` — 独立伤害

```jsonc
{ "op": "hit", "power": 90, "type": "物攻" }
{ "op": "hit", "power": 90, "type": "物攻", "element": "火" }
```

#### `escape` / `return` / `lock` / `interrupt`

```jsonc
{ "op": "escape", "target": "sprite_self" }
{ "op": "escape", "target": "sprite_self", "inherit": true }
{ "op": "escape", "target": "sprite_self", "urgent": true }
{ "op": "return", "target": "sprite_self" }
{ "op": "lock", "target": "sprite_opp", "turns": 2 }
{ "op": "interrupt", "target": "sprite_opp" }
```

#### `exchange` / `reset` / `redirect`

```jsonc
{ "op": "exchange", "what": "hp_ratio" }
{ "op": "exchange", "what": "effects" }
{ "op": "reset", "target": "skill_off_0", "stat": "energy_cost" }
{ "op": "redirect", "target": "sprite_self" }
```

#### 技能专用

```jsonc
{ "op": "charge" }
{ "op": "replay", "from": "sprite_self", "skill_filter": {"tag": "迅捷"} }
{ "op": "borrow", "from": "skill_opp_current" }
```

### 2D. 持久化/复合类

#### `observer` — 注册持久化条件→动作绑定

替代原 `count`。Observer 是 IR 第一公民，显式声明触发条件、可选计数器和生命周期。

```jsonc
// 无计数器 — 每次触发都执行
{ "op": "observer",
  "cond": {"cond": "sprite_entered", "of": "sprite_self"},
  "then": [{ "op": "stat_stage", "target": "sprite_self", "stat": "atk", "steps": 5 }],
  "scope": "persistent" }

// 带计数器 — 阈值触发
{ "op": "observer",
  "listen": "post_counter",
  "cond": {"cond": "counter_succeeded", "skill_type": "防御"},
  "counter": {"name": "defense_counter", "threshold": 2, "reset": true},
  "then": [
    { "op": "heal", "target": "sprite_self", "ratio": 1.0 },
    { "op": "transform", "species": "棋绮后" }
  ],
  "scope": "persistent" }
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `cond` | `Condition` | 触发条件 |
| `then` | `[RiscIROp]` | 命中时执行的 IR |
| `listen` | `str` | 触发点（编译器从 cond 推断） |
| `counter` | `{name, threshold, reset}` | 可选计数器 |
| `scope` | `str` | `"battlefield"/"persistent"/"permanent"` |

#### `defer` — 延迟执行

替代原 `schedule`。声明 "N 回合后执行"，phase 细节由引擎处理。

```jsonc
{ "op": "defer", "turns": 3, "at": "turn_start",
  "then": [{ "op": "revive", "target": "sprite_self", "hp_ratio": 1.0 }] }
```

#### `inherit` — 效果继承

替代原 `inherit_effects`。离场时将效果传递给入场精灵。

```jsonc
{ "op": "inherit", "target": "ally_new",
  "effects": [
    {"op": "stat_stage", "target": "sprite_self", "stat": "def", "steps": 1}
  ]}
```

#### 其他

```jsonc
{ "op": "team_counter", "key": "counter_success", "delta": 1, "target_team": "own" }
{ "op": "transform", "species": "岚鸟-暴风形态",
  "skills": ["暴风眼", "风之翼", "龙卷风", "气流斩"] }
{ "op": "trait_interaction", "action": "suppress", "target": "sprite_opp" }
{ "op": "lives", "delta": -1, "target_team": "own" }
{ "op": "mutate_effect", "target": "sprite_self",
  "filter": {"name": "萌化"}, "delta_stacks": 1 }
```

---

## 三、控制流 — `branch`

RISC 风格的条件分支：三操作数 `[cond, then, else]`。替代原 `when`。

```jsonc
// if-else
{ "op": "branch",
  "cond": {"cond": "counter_succeeded"},
  "then": [{ "op": "mult_mod", "target": "skill_off_0", "attr": "power_mult", "value": 2.0 }],
  "else": [{ "op": "mult_mod", "target": "skill_off_0", "attr": "power_mult", "value": 1.0 }]
}

// if-elseif-else (else_if = branch 链)
{ "op": "branch",
  "cond": {"cond": "have", "what": "abnormal", "of": "sprite_opp", "name": "中毒", "stacks_ge": 5},
  "then": [...],
  "else_if": [
    { "op": "branch",
      "cond": {"cond": "have", "what": "abnormal", "of": "sprite_opp", "name": "中毒", "stacks_ge": 3},
      "then": [...] }
  ],
  "else": [...]
}

// 嵌套 branch
{ "op": "branch",
  "cond": {"cond": "on_damage_taken"},
  "then": [
    { "op": "branch",
      "cond": {"cond": "have_skill_of", "of": "sprite_self", "element": "火"},
      "then": [{ "op": "mult_mod", "target": "sprite_self", "attr": "damage_reduction", "value": 0.4 }]
    }
  ]
}
```

> **编译映射**: `when` → `branch` 是纯重命名。`cond`/`then`/`else`/`else_if` 语义完全不变，
> 只是将自然语言隐喻（"当...时"）替换为控制流术语（"条件分支"），与 RISC 的分支指令对应。

### `scope` / `source` / `ttl` 通用字段

| 字段 | 含义 | 默认 | 适用 op |
|------|------|------|--------|
| `scope` | `"battlefield"/"persistent"/"permanent"` | `"battlefield"` | stat_stage/power_mod/flag_set/observer |
| `source` | 效果来源名（追踪/驱散用） | — | 全部 |
| `ttl` | 存活回合数 | 永久 | stat_stage/power_mod/flag_set |
| `per_hit` | 每次连击触发 | 否 | mult_mod/flag_set |

---

## 四、条件系统

### 命名条件 — COND_EVAL

```jsonc
// 基础
{ "cond": "counter_succeeded" }
{ "cond": "charged" }
{ "cond": "first_action" }
{ "cond": "opp_switched" }
{ "cond": "on_damage_taken" }
{ "cond": "on_ko" }

// 参数化
{ "cond": "hp_below", "ratio": 0.5 }
{ "cond": "energy_le", "value": 3 }
{ "cond": "weather_is", "weather": "snow" }
{ "cond": "have", "what": "abnormal", "of": "sprite_self", "name": "萌化" }
{ "cond": "have_skill_of", "of": "sprite_self", "element": "火", "exclude_self": true }
{ "cond": "counter_succeeded", "skill_type": "防御", "of": "sprite_self" }
{ "cond": "sprite_entered", "of": "sprite_opp" }
{ "cond": "sprite_left", "of": "sprite_self" }
{ "cond": "team_has_element", "element": "虫" }
{ "cond": "sprite_acted", "of": "sprite_self" }
{ "cond": "compare", "q": "energy", "of": "sprite_self", "op": "ge", "value": 3 }

// 逻辑组合
{ "cond": "and", "conditions": [
  { "cond": "counter_succeeded" },
  { "cond": "on_damage_taken" }
]}
{ "cond": "or", "conditions": [...] }
{ "cond": "not", "condition": {...} }
```

**完整 `cond` 合法值：**

`counter_succeeded` `prev_counter_succeeded` `charged` `is_charging` `burst` `first_action` `on_damage_taken` `on_ko` `on_self_ko` `opp_switched` `self_switched` `opp_is_attack` `is_first` `is_second` `skill_at` `skill_use` `skill_position_changed` `self_was_countered` `have` `have_skill_of` `devotion_triggered` `hp_below` `energy_le` `energy_eq` `energy_depleted` `weather_is` `prev_skill_is` `prev_damage_taken` `sprite_entered` `sprite_left` `sprite_acted` `team_has_element` `on_abnormal_tick` `on_abnormal_changed` `on_abnormal_applied` `on_skills_energy_changed` `on_positive_changed` `on_energy_changed` `turn_end` `compare` `and` `or` `not`

### 路径条件 — PathCond（特性 Observer.cond 用）

特性 JSON 中的 `condition` 字段编译为 Observer.cond，支持对象图路径：

```jsonc
// 技能系别 == 草
{ "path": "skill.element", "op": "eq", "value": "草" }

// 队伍系别中包含"虫"
{ "path": "team_elements", "op": "contains", "value": "虫" }

// 己方有灼烧效果
{ "path": "self.effects[name=灼烧].exists", "op": "eq", "value": true }
```

**路径头：** `self` `target` `attacker` `skill` `team_elements` `effect_name`

**op：** `eq` `neq` `gt` `gte` `lt` `lte` `in` `not_in` `contains`

### 函数条件 — FnCond

```jsonc
{ "kind": "fn", "name": "is_weekend" }
```

### 实现：统一 dispatch 表

```python
COND_EVAL = {
    "counter_succeeded": lambda ctx, cond: ctx.event.counter_succeeded,
    "hp_below":          lambda ctx, cond: ctx.hp_self_ratio < cond["ratio"],
    "have":              lambda ctx, cond: HAVE_EVAL[cond["what"]](ctx, cond),
    "and": lambda ctx, cond: all(eval_one(ctx, c) for c in cond["conditions"]),
    "or":  lambda ctx, cond: any(eval_one(ctx, c) for c in cond["conditions"]),
    "not": lambda ctx, cond: not eval_one(ctx, cond["condition"])),
    # ... 40+ 条件
}
```

> **事件条件访问模式**：16 个事件瞬时条件（`counter_succeeded`, `was_countered`, `prev_counter_succeeded`,
> `target_fainted`, `self_koed`, `opp_switched`, `self_switched`, `turn_end`, `skill_position_changed`,
> `devotion_triggered`, `last_tick_abnormal`, 等）通过 `ctx.event.X` 访问，而非 `ctx.X`。
> 其余条件（`hp_below`, `have`, `charged` 等）读取 Ctx 快照字段，不受 EventContext 影响。

---

## 五、Observer 模型 — 原 `count` 的 RISC 化

原 `count` 的隐式行为已由显式的 `observer` 操作码替代（见 [二、2D. 持久化/复合类](#2d-持久化复合类)）。

```jsonc
// 每次草系技能使用 → 威力+6（永久）
{ "op": "observer",
  "cond": {"cond": "skill_use", "element": "草"},
  "then": [{ "op": "power_mod", "target": "skill_off_0", "attr": "power", "delta": 6 }],
  "scope": "permanent" }

// 阈值计数：每 2 次防御应对 → 回满 HP + 变形
{ "op": "observer",
  "listen": "post_counter",
  "cond": {"cond": "counter_succeeded", "skill_type": "防御"},
  "counter": {"name": "defense_counter", "threshold": 2, "reset": true},
  "then": [
    { "op": "heal", "target": "sprite_self", "ratio": 1.0 },
    { "op": "transform", "species": "棋绮后" }
  ],
  "scope": "persistent" }
```

> **RISC 化要点**: `observer` 将隐式绑定的 Observer + counter 显式化。`count` 的 `when`/`then`/`threshold`/`reset_on_fire`/`scope`
> 全部映射为 `observer` 的对应字段，语义不变，但注册/计数/触发三个关注点可独立审视。

---

## 六、执行时序 — `feeds` / `needs` [技能]

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

## 七、通用字段

### effect 级

| 字段 | 含义 | 默认 | 技能 | 特性 |
|------|------|------|:----:|:----:|
| `scope` | `"battlefield"` / `"persistent"` / `"permanent"` | `"battlefield"` | ✓ | ✓ |
| `feeds` | 拓扑排序 token | — | ✓ | — |
| `needs` | 拓扑排序 token | — | ✓ | — |
| `delay` | 延迟 N 回合生效 | `0` | ✓ | — |
| `ttl` | 存活回合数 | 永久 | ✓ | ✓ |
| `per_hit` | 每次连击触发 | 否 | ✓ | ✓ |
| `cooldown` | 冷却（次） | `0` | ✓ | — |
| `source` | 效果来源名 | — | ✓ | ✓ |

### 技能 body 字段

| 字段 | 含义 | 默认 |
|------|------|------|
| `element` | 系别（支持 Query） | `"普通"` |
| `tag` | 机制标签 | 无 |
| `use_devotion` | 触发队伍奉献 | `false` |
| `usable_while_charging` | 蓄力中可用 | `false` |
| `position_locked` | 不被交换移动 | `false` |
| `morph` | 变身 `{"from": "team_own", "mode": "random"}` | 无 |
| `passive` | 被动效果数组 | `[]` |
| `counter` | 应对类型：`"攻击"` / `"防御"` / `"状态"` | 无 |

---

## 八、特性入口：Observer 模型

特性的 JSON 存储格式是 `triggers`，由 `TraitToObserver` 编译器在加载时转换为 Observer 对象。**运行时只有 Observer，没有 triggers。**

### 编译流程

```
特性 JSON (data/traits/不屈.json)
  {
    "id": 20001,
    "name": "不移",
    "triggers": [
      { "on": "entry", "effects": [
          {"kind": "stat", "stat": "power_mult", "steps": 3, "scope": "permanent",
           "skill_filter": "bare_attack"}
        ]
      }
    ]
  }
        │  TraitToObserver.convert()
        ▼
  Observer(
    cond={"cond": "trait_path", "path": "self.energy", "op": "ge", "value": 0},  // always-true
    then=[
      {"op": "mod", "target": "sprite_self", "stat": "power_mult", "steps": 3,
       "scope": "permanent", "skill_filter": "bare_attack"}
    ],
    scope="battlefield",
    source="不移"
  )
        │  registry.register(observer, hook="post_entry")
        ▼
  精灵入场 → fire("post_entry") → cond 命中 → Skill VM 执行 then[]
```

**关键点：Observer.then 就是 Skill IR opcode 数组。** 引擎执行时走的是同一条 `executor.py` 路径，不区分来源。

### Observer 结构

```
Observer = { cond, then, scope, source }
  cond:    条件表达式（COND_EVAL / PathCond / FnCond / 逻辑组合）
  then:    Skill IR opcode 数组 ← 与技能 effects[] 完全相同
  scope:   存活范围（battlefield / persistent / permanent）
  source:  特性名（用于注册/注销追踪）
```

### 触发点（引擎 fire 的 hook 点）

| hook | 说明 |
|------|------|
| `post_entry` | 精灵入场 |
| `post_leave` | 精灵离场（Observer 注销） |
| `post_enemy_leave` | 敌方离场+新精灵入场后（ctx 为我方视角，opp=新入场敌方） |
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
| `turn_end` | 回合末结算 |

### Scope 与生命周期

| scope | 含义 | 离场 | 力竭 | 回合末 |
|------|------|:----:|:----:|:----:|
| `turn` | 仅当前回合有效 | 清除 | 清除 | 清除 |
| `battlefield` | 在场有效 | 清除 | 清除 | 保留 |
| `persistent` | 跨回合持久（受 ttl 控制） | 保留 | 清除 | ttl-1，归零清除 |
| `permanent` | 永久 | 保留 | 保留 | 保留 |

### `ttl` — 回合存活数

`ttl` (time-to-live) 表示效果存活的回合数。仅在 `scope: "persistent"` 时生效。
回合结束时 ttl 减 1，归零后效果自动清除。`ttl: 0` 或未设置 = 无限期。

```jsonc
// 入场首回合物攻+100%（回合末自动消失）
{ "op": "mult_mod", "target": "sprite_self", "attr": "atk",
  "value": 1, "mode": "add", "scope": "turn" }

// 聒噪效果持续 3 回合
{ "op": "abnormal", "target": "sprite_opp", "name": "聒噪",
  "stacks": 1, "scope": "persistent", "ttl": 3 }
```

**`ttl` 递减规则**：回合末精灵仍在场上时 ttl 减 1。精灵被换下时 ttl 暂停（效果随精灵保留在 bench），重新上场后继续递减。

### 引擎钩子（非 IR，引擎层拦截）

部分特性需要修改引擎行为（如能量上限、印记共存模式），这些不走 Skill VM，而是通过 `register_hook()` 注册引擎层回调：

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

## 九、编译示例

### 技能

#### 升龙咆哮（蓄力）

```jsonc
{
  "name": "升龙咆哮", "element": "龙", "skill_type": "魔攻", "power": 200, "energy_cost": 3,
  "effects": [
    { "when": { "cond": "charged" }, "then": [],
      "else": [{ "op": "charge" }] }
  ]
}
```

#### 铁壁（and 组合条件）

```jsonc
{
  "name": "铁壁", "skill_type": "防御", "counter": "攻击",
  "effects": [
    { "when": { "cond": "and", "conditions": [
        { "cond": "counter_succeeded" }, { "cond": "on_damage_taken" }
      ]},
      "then": [{ "op": "stat_stage", "target": "sprite_self", "stat": "def", "steps": 1 }]
    }
  ]
}
```

### 特性

#### 不移（无条件永久修饰器）

```jsonc
// JSON 存储
{"triggers": [{"on": "entry", "effects": [
  {"kind": "stat", "stat": "power_mult", "steps": 3, "scope": "permanent", "skill_filter": "bare_attack"}
]}]}

// → Observer.then（RISC IR）
{ "op": "mult_mod", "target": "sprite_self", "attr": "power_mult",
  "value": 1.0 + 3 * 0.1, "scope": "permanent", "skill_filter": "bare_attack" }
```

#### 偏振（have_skill_of + element query + pre_calc Observer）

```jsonc
// 受到自己携带技能系别的攻击伤害-40%

// effects 格式（RISC IR）
{"effects": [
  {"op": "observer",
   "cond": {"cond": "have_skill_of", "of": "sprite_opp",
    "element": {"q": "element", "of": "skill_off_0"}},
   "then": [
     {"op": "mult_mod", "target": "sprite_opp", "attr": "damage_reduction",
      "value": 0.4, "per_hit": true, "scope": "battlefield", "source": "偏振"}
   ],
   "scope": "persistent"}
]}

// → Observer
//   cond:  {"cond": "have_skill_of", "of": "sprite_opp",
//           "element": {"q": "element", "of": "skill_off_0"}}
//   then:  [{"op": "mult_mod", "target": "sprite_opp", "attr": "damage_reduction",
//            "value": 0.4, "per_hit": true, "scope": "battlefield"}]
//   listen: {}  ← have_skill_of 无 CONDITION_TRIGGERS，靠 pre_calc 无条件遍历触发
//
// 运作流程：
//   pre_calc 触发 → 遍历所有 Observer → 条件求值：
//   ctx 为攻击方视角（self=攻击方, opp=防御方）
//   → cond 检查：防御方(ctx.sprite_opp)是否拥有攻击方(ctx.skill_off_0)的元素系别技能
//   → 命中则给防御方施加 damage_reduction = 0.4 (per_hit 衰减)
//
// 关键：("skill_off_0", "element") → Ctx.element_self（ADDRESS_MAP 新增条目）
// 注意：己方攻击时 Observer 也会触发，但 cond 检查敌方是否拥有己方技能系别，
// 此时敌方 ≠ sprite_opp(self视角)，条件不命中，安全。
```

#### 虫群鼓舞（RefExpr + effects_mode: replace）

```jsonc
// JSON 存储
{"triggers": [{"on": "entry", "effects_mode": "replace", "effects": [
  {"kind": "stat", "stat": "atk", "steps": "=@player_bug_count", "scope": "battlefield"}
]}]}

// → Observer.then（RISC IR）
{ "op": "stat_stage", "target": "sprite_self", "stat": "atk",
  "steps": RefExpr(root="player", path=["bug_count"], multiplier=1.0),
  "scope": "battlefield" }
```

#### 毒棘（敌方入场触发 + opp_entered）

```jsonc
// 敌方精灵离场后，更换入场的精灵获得5层中毒

// effects 格式（RISC IR）
{"effects": [
  {"op": "observer",
   "cond": {"cond": "sprite_entered", "of": "sprite_opp"},
   "then": [
     {"op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 5}
   ],
   "scope": "persistent"}
]}

// → Observer
//   cond:  {"cond": "sprite_entered", "of": "sprite_opp"}
//   then:  [{"op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 5}]
//   scope: "persistent"
//   listen: {"post_entry", "post_enemy_leave"}  ← CONDITION_TRIGGERS 推断
//
// 运作流程：
//   敌方离场+新精灵入场 → post_enemy_leave 触发（ctx 为我方视角，opp=新敌人）
//   → 条件 "sprite_entered, of: sprite_opp" 命中
//   → VM 执行 abnormal op → 新敌人获得5层中毒
```

#### 不朽（力竭后复活 + schedule + revive）

```jsonc
// 力竭3回合后复活

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "on_self_ko"},
   "then": [
     {"op": "schedule", "delay_turns": 3, "phase": "start",
      "effects": [
        {"op": "mod", "target": "sprite_self", "stat": "revive", "value": 1.0}
      ]}
   ],
   "scope": "permanent"}
]}

// → Observer
//   cond:  {"cond": "on_self_ko"}
//   then:  [{"op": "schedule", "delay_turns": 3, "phase": "start", "effects": [...]}]
//   scope: "permanent"  ← 力竭后 Observer 仍存活
//   listen: {"post_ko"}  ← CONDITION_TRIGGERS 推断
//
// 运作流程：
//   力竭 → post_ko 触发 → on_self_ko 命中 → schedule 写入延时队列
//   → 3 回合后 turn_start → 延时结算 → VM 执行 revoke → 复活上场
```

#### 防御反击（阈值计数 + counter_succeeded 过滤 + transform）

```jsonc
// 防御技能应对2次后，回满状态，变为棋绮后

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "counter_succeeded", "skill_type": "防御", "of": "sprite_self"},
   "threshold": 2, "reset_on_fire": true,
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "hp", "value": 1.0},
     {"op": "transform", "species": "棋绮后"}
   ],
   "scope": "persistent"}
]}

// → Observer
//   cond:  {"cond": "counter_succeeded", "skill_type": "防御", "of": "sprite_self"}
//   then:  [heal, transform]
//   listen: {"post_counter"}
//
// 运作流程：
//   每次防御技能应对成功 → post_counter 触发 → 计数器 +1
//   → 计数器 < 2: 不执行 then
//   → 计数器 >= 2: 执行 then (回满HP + 变身为棋绮后) → 计数器归零
```

> `threshold` + `reset_on_fire` 是 `count` opcode 新增字段。`counter_succeeded` 的 `skill_type`/`of` 是新增过滤参数。

#### 能耗波动（energy_cost_delta_mult）

```jsonc
// 技能受能耗变化效果影响翻倍

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "sprite_entered"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "energy_cost_delta_mult", "value": 2.0, "scope": "permanent"}
   ],
   "scope": "persistent"}
]}

// → Observer
//   cond:  {"cond": "sprite_entered"}
//   then:  [mod energy_cost_delta_mult 2.0]
//   listen: {"post_entry"}
//
// 运作流程：
//   入场 → 设置 energy_cost_delta_mult = 2.0
//   → 后续所有 energy_cost 变化量 × 2（-1→-2, +1→+2）
```

> `energy_cost_delta_mult` 是 `mod` opcode 新增 `stat`。

#### 衰减强化（入场 buff + 每次行动衰减）

```jsonc
// 入场时获得物攻+100%，每次行动后-20%

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "sprite_entered", "of": "sprite_self"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 5, "scope": "persistent"}
   ],
   "scope": "persistent"},
  {"op": "count",
   "when": {"cond": "sprite_acted", "of": "sprite_self"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "atk", "steps": -1}
   ],
   "scope": "persistent"}
]}

// → Observer #1
//   cond:  {"cond": "sprite_entered", "of": "sprite_self"}
//   then:  [mod atk steps: 5]
//   listen: {"post_entry"}

// → Observer #2
//   cond:  {"cond": "sprite_acted", "of": "sprite_self"}
//   then:  [mod atk steps: -1]
//   listen: {"post_skill"}

// 运作流程：
//   入场 → post_entry → Observer #1 命中 → atk +5 阶段 (+100%)
//   每次自己行动 → post_skill → Observer #2 命中 → atk -1 阶段 (-20%)
//   5 次行动后 → 5 + 5×(-1) = 0 阶段，加成归零
//
// StatChange 的 steps 在 sprite 上以独立 StatusEffect 累加，
// steps=5 和 steps=-1 各为独立效果，最终阶段数求和。
```

> `sprite_acted` 是新增条件，映射到 `post_skill` 触发点。

#### 虫鸣（按技能名过滤 + pre_calc 威力加成）

```jsonc
// 【虫鸣】技能威力+20

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "compare", "q": "name", "of": "skill_off_0", "op": "eq", "value": "虫鸣"},
   "then": [
     {"op": "mod", "target": "skill_off_0", "stat": "power", "value": 20}
   ],
   "scope": "permanent"}
]}

// → Observer
//   cond:  {"cond": "compare", "q": "name", "of": "skill_off_0", "op": "eq", "value": "虫鸣"}
//   then:  [mod power +20]
//   listen: {}  ← compare 无 CONDITION_TRIGGERS，靠 pre_calc 无条件遍历触发

// 运作流程：
//   pre_calc 触发 → 遍历所有 Observer → compare 求值：
//   ctx.skill_name_self == "虫鸣" ?
//   → 命中 → mod power +20 写入 _modifiers
//   → 当前技能伤害计算使用加成后威力
```

> `("skill_off_0", "name")` 是 ADDRESS_MAP 新增条目，`Ctx.skill_name_self` 已存在。

#### 能耗光环（入场施加 + 敌方换宠重施加 + 离场 dispel）

```jsonc
// 在场时，敌方全技能能耗+1。离场后敌方能耗恢复。

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "sprite_entered", "of": "sprite_self"},
   "then": [
     {"op": "mod", "target": "sprite_opp", "stat": "energy_cost", "value": 1,
      "scope": "battlefield", "source": "能耗光环"}
   ],
   "scope": "persistent"},
  {"op": "count",
   "when": {"cond": "sprite_entered", "of": "sprite_opp"},
   "then": [
     {"op": "mod", "target": "sprite_opp", "stat": "energy_cost", "value": 1,
      "scope": "battlefield", "source": "能耗光环"}
   ],
   "scope": "persistent"},
  {"op": "count",
   "when": {"cond": "sprite_left", "of": "sprite_self"},
   "then": [
     {"op": "dispel", "target": "sprite_opp", "source": "能耗光环"}
   ],
   "scope": "persistent"}
]}

// → Observer #1 (sprite_entered self)
//   cond: {"cond": "sprite_entered", "of": "sprite_self"}
//   then: [mod energy_cost +1]
//   listen: {"post_entry"}

// → Observer #2 (sprite_entered opp)
//   cond: {"cond": "sprite_entered", "of": "sprite_opp"}
//   then: [mod energy_cost +1]
//   listen: {"post_entry", "post_enemy_leave"}

// → Observer #3 (sprite_left self)
//   cond: {"cond": "sprite_left", "of": "sprite_self"}
//   then: [dispel source=能耗光环]
//   listen: {"post_leave"}

// 运作流程：
//   我方入场 → Observer #1 命中 → 敌方 energy_cost +1
//   敌方换宠 → 旧敌人离场（battlefield modifier 自动清）
//          → Observer #2 命中 → 新敌人 energy_cost +1
//   我方离场 → Observer #3 命中 → dispel 清除敌方"能耗光环" modifier
```

> `sprite_left` 是新增条件，映射到 `post_leave` 触发点。

#### 效果继承（离场 buff 传递给入场己方）

```jsonc
// 离场后，更换入场的精灵获得双防+20%且免疫冻结

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "sprite_left", "of": "sprite_self"},
   "then": [
     {"op": "inherit_effects", "target": "ally_new",
      "effects": [
        {"op": "mod", "target": "sprite_self", "stat": "def", "steps": 1, "scope": "battlefield"},
        {"op": "mod", "target": "sprite_self", "stat": "sp_def", "steps": 1, "scope": "battlefield"},
        {"op": "mod", "target": "sprite_self", "stat": "freeze_immune", "value": 1.0, "scope": "battlefield"}
      ]}
   ],
   "scope": "persistent"}
]}

// → Observer
//   cond:  {"cond": "sprite_left", "of": "sprite_self"}
//   then:  [inherit_effects → ally_new]
//   listen: {"post_leave"}

// 运作流程：
//   己方离场 → post_leave → sprite_left 命中
//   → inherit_effects 将 effects 包挂载到 battle.pending_effects
//   → 己方新精灵入场 → 消费 pending_effects → VM 执行：
//     def +1 阶段、sp_def +1 阶段、freeze_immune = 1.0
```

> `inherit_effects.effects` 是新增字段，`freeze_immune` 是 `mod` 新增 `stat`。

#### 绝境（入场检测己方魔力值）

```jsonc
// 入场时若己方魔力值为1，获得双攻+50%

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "and", "conditions": [
     {"cond": "sprite_entered", "of": "sprite_self"},
     {"cond": "compare", "q": "lives", "of": "team_own", "op": "eq", "value": 1}
   ]},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0.5, "scope": "persistent"},
     {"op": "mod", "target": "sprite_self", "stat": "sp_atk", "value": 0.5, "scope": "persistent"}
   ],
   "scope": "persistent"}
]}

// → Observer
//   cond: and(sprite_entered, compare(lives_own == 1))
//   then: [mod atk +0.5, mod sp_atk +0.5]
//   listen: {"post_entry"}  ← infer_triggers(and) = union = {"post_entry"}

// 运作流程：
//   入场 → post_entry → and 双条件求值：
//   sprite_entered(self) + compare(lives_own == 1)
//   → 命中 → atk * 1.5, sp_atk * 1.5（各 +50%）
```

> `("team_own", "lives")` 和 `("team_opp", "lives")` 是 ADDRESS_MAP 新增条目。

#### 星陨共鸣（敌方印记数 → 威力加成）

```jsonc
// 敌方每有1层星陨印记，技能威力+15%

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "compare", "q": "mark_stacks", "of": "team_opp", "name": "星陨", "op": "gt", "value": 0},
   "then": [
     {"op": "mod", "target": "skill_off_0", "stat": "power_mult",
      "value": {"q": "mark_stacks", "of": "team_opp", "name": "星陨", "scale": 0.15}}
   ],
   "scope": "persistent"}
]}

// → Observer
//   cond: compare(mark_stacks_opp["星陨"] > 0)
//   then: [mod power_mult = 星陨层数 × 0.15]
//   listen: {}  ← compare 无 CONDITION_TRIGGERS，靠 pre_calc 遍历

// 运作流程：
//   pre_calc → compare 查询 mark_stacks_opp["星陨"]
//   → 3 层 → value = 3 × 0.15 = 0.45 → power_mult = 0.45 (+45%)
```

> `("team_own", "mark_stacks")` / `("team_opp", "mark_stacks")` 是 ADDRESS_MAP 新增。`_NAMED_DICT_QUERIES` 需加入 `"mark_stacks"`。`scale` 截断问题见扩展 #12。

#### 虫群羁绊（队伍元素检测 + not 清除）

```jsonc
// 队伍存在虫系精灵 → 双攻+50%

// effects 格式（纯 Skill IR）
{"effects": [
  {"op": "count",
   "when": {"cond": "team_has_element", "element": "虫"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0.5, "mode": "set", "scope": "persistent"},
     {"op": "mod", "target": "sprite_self", "stat": "sp_atk", "value": 0.5, "mode": "set", "scope": "persistent"}
   ],
   "scope": "persistent"},
  {"op": "count",
   "when": {"cond": "not", "condition": {"cond": "team_has_element", "element": "虫"}},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0, "mode": "set", "scope": "persistent"},
     {"op": "mod", "target": "sprite_self", "stat": "sp_atk", "value": 0, "mode": "set", "scope": "persistent"}
   ],
   "scope": "persistent"}
]}

// → Observer #1 (有虫系)
//   cond: team_has_element(element=虫)
//   then: [mod atk=0.5, sp_atk=0.5 mode=set]
//   listen: {"post_entry", "post_leave", "post_ko"}

// → Observer #2 (无虫系)
//   cond: not(team_has_element(element=虫))
//   then: [mod atk=0, sp_atk=0 mode=set]
//   listen: {"post_entry", "post_leave", "post_ko"}

// 运作流程：
//   虫系入场 → post_entry → #1 命中 → 双攻 +50%
//   虫系离场 → post_leave → #2 命中 → 双攻清零
//   pre_calc 再验证一次（无条件遍历）
```

> `team_has_element` 是新增条件，`team_elements_own` 是 Ctx 新增字段。

---

## 十、附录

### A. 完整 opcode 表（RISC 分类）

#### 寄存器修改类 — 替代原 `mod` 操作码

| opcode | 技能 | 特性 | 说明 |
|--------|:----:|:----:|------|
| `stat_stage` | ✓ | ✓ | 属性阶段修正 (atk/def/sp_atk/sp_def/speed) |
| `power_mod` | ✓ | ✓ | 技能属性修正 (power/energy_cost/combo/priority) |
| `mult_mod` | ✓ | ✓ | 倍率修正 (power_mult/damage_mult/damage_reduction) |
| `flag_set` | ✓ | ✓ | 布尔标记 (immune/freeze_immune/survive/swift/...) |
| `heal` | ✓ | ✓ | HP 回复/扣除 |
| `energize` | ✓ | ✓ | 能量回复/扣除 |
| `revive` | ✓ | ✓ | 精灵复活 |

#### 状态效果类

| opcode | 技能 | 特性 | 说明 |
|--------|:----:|:----:|------|
| `mark` | ✓ | ✓ | 印记增/减/偷 |
| `abnormal` | ✓ | ✓ | 异常施加/tick |
| `weather` | ✓ | ✓ | 天气设置 |
| `dispel` | ✓ | ✓ | 驱散效果 |
| `steal` | ✓ | ✓ | 偷取效果/能量 |
| `double` | ✓ | ✓ | 翻倍效果层数 |
| `tick` | ✓ | ✓ | 异常结算 |

#### 战斗流控类

| opcode | 技能 | 特性 | 说明 |
|--------|:----:|:----:|------|
| `hit` | ✓ | ✓ | 独立伤害 |
| `escape` | ✓ | ✓ | 换宠下场 |
| `return` | ✓ | ✓ | 返场 |
| `lock` | ✓ | ✓ | 锁定（禁换宠） |
| `interrupt` | ✓ | ✓ | 打断 |
| `exchange` | ✓ | ✓ | 交换（hp_ratio/effects） |
| `reset` | ✓ | ✓ | 重置技能属性 |
| `redirect` | ✓ | ✓ | 重定向 |
| `charge` | ✓ | — | 蓄力 |
| `replay` | ✓ | — | 重放技能 |
| `borrow` | ✓ | — | 借用敌方技能 |

#### 持久化/复合类

| opcode | 技能 | 特性 | 说明 |
|--------|:----:|:----:|------|
| `observer` | ✓ | ✓ | 注册持久化条件→动作绑定 |
| `defer` | — | ✓ | 声明延迟执行 |
| `transform` | — | ✓ | 形态变换 |
| `inherit` | — | ✓ | 效果继承（离场→入场） |
| `team_counter` | — | ✓ | 队伍计数器 |
| `lives` | — | ✓ | 队伍魔力值 |

#### 控制流

| opcode | 技能 | 特性 | 说明 |
|--------|:----:|:----:|------|
| `branch` | ✓ | ✓ | 条件分支 (cond→then/else) |

> **编译映射**：原 `mod`/`count`/`schedule`/`when` 是 Authoring IR，编译器在加载时转换为上述 RISC IR。详见 [附录 D](#d-从旧-ir-迁移)。

### B. target 映射

| 旧格式 | 统一 |
|--------|------|
| `sprite_self` / `self` | `"sprite_self"` |
| `sprite_opp` / `opp` / `target` | `"sprite_opp"` |
| `team_own` / `own_team` | `"team_own"` |
| `team_opp` / `opp_team` | `"team_opp"` |
| `skill_off_0` / `current_skill` | `"skill_off_0"` |
| `skill_opp_current` | `"skill_opp_current"` |

### C. RISC 原则：遵循与偏离

#### 遵循 RISC 原则的设计

| 原则 | 本 VM 实现 |
|------|-----------|
| **统一寄存器组** | Ctx 是回合级只读寄存器快照，所有指令通过 ADDRESS_MAP 统一寻址 |
| **纯函数执行** | `(Ctx, opcodes[]) -> Journal[Mutation]`，无副作用，确定性 |
| **统一 IR** | 技能 effects[] 和特性 Observer.then[] 编译为同一套 RISC opcode 数组 |
| **单级间接寻址** | ADDRESS_MAP 提供 O(1) `(of, q) → field_name` 查找，自动校验 |
| **纯条件分发** | COND_EVAL 每个条件一个纯函数，通过 `ctx.event.X` 访问事件上下文 |
| **一条指令 = 一种 mutation** | `stat_stage`→StatChange, `heal`→Heal, `energize`→EnergyChange, `flag_set`→FlagSet |

#### 偏离 RISC 原则的设计（及领域理由）

| 偏离 | 描述 | 领域理由 |
|------|------|---------|
| **`skill_where` 保留** | 批量技能筛选仍在指令内 | 拆为循环会导致 IR 膨胀；回合制技能数 ≤4 |
| **`branch` 允许嵌套** | 控制流可嵌套 | 回合制对战的 if-else 天然嵌套，强制基本块过度复杂 |
| **`observer` 内嵌 counter** | Observer 可选配计数器 | 阈值计数是回合制"基本原子"，拆分无收益 |
| **保留 `defer`** | 延时执行仍为 IR 指令 | 延时是回合制核心机制，但简化为声明式 |
| **专用 opcode 存在** | `transform`/`lives`/`trait_interaction` 等 | 领域 VM 允许专用 opcode — 无法用通用 op 组合表达 |

#### 设计权衡总结

这不是通用 CPU，而是**回合制对战游戏的领域 VM**。目标不是最小化指令数，而是：
1. 游戏策划的 JSON 描述能 1:1 映射到 IR
2. 编译器和运行时足够简单，可审计正确性
3. 单条 IR 指令对应游戏中一个可理解的操作

### D. 从旧 IR 迁移

| 旧 IR (Authoring) | 新 IR (RISC) | 说明 |
|-------------------|-------------|------|
| `mod stat: atk/def/...` | `stat_stage` | 属性阶段 |
| `mod stat: power/energy_cost/combo/priority` | `power_mod` | 技能属性 |
| `mod stat: power_mult/damage_mult/damage_reduction/life_drain` | `mult_mod` | 倍率修正 |
| `mod stat: immune/freeze_immune/survive/...` | `flag_set` | 布尔标记 |
| `mod stat: hp` | `heal` | HP 操作 |
| `mod stat: energy` | `energize` | 能量操作 |
| `mod stat: revive` | `revive` | 复活 |
| `count` | `observer` | 条件→动作持久化绑定 |
| `schedule` | `defer` | 延迟执行 |
| `when` | `branch` | 条件分支 |
| `inherit_effects` | `inherit` | 效果继承 |

---

## 十一、待实现扩展 & 代码修改建议

> **注意**: 以下 54 个扩展是设计提案和演进路线图。部分已在代码中实现，
> 部分仍为前瞻性设计。代码示例中的 Ctx 字段名以 `backend/vm/ctx.py`
> 的实际定义为准。事件瞬时字段（counter_succeeded, turn_end 等）
> 已迁移至 `ctx.event.X` — 详见[执行模型](#执行模型--ctx-寄存器组)。

### 扩展 #1: `opp_entered` — 敌方入场检测

**需求**：表达"敌方精灵离场后，更换入场的精灵获得5层中毒"（毒棘）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "when": {"cond": "sprite_entered", "of": "sprite_opp"},
   "then": [
     {"op": "abnormal", "target": "sprite_opp", "name": "中毒", "stacks": 5}
   ],
   "scope": "persistent"}
]}
```

**代码修改清单**（5 个文件，~10 行）：

#### 1. `backend/vm/ctx.py` — 新增字段

```python
# Ctx dataclass，在 opp_switched / self_switched 附近新增：
opp_entered: bool = False   # 敌方本回合刚入场（post_enemy_leave 时为 True）
```

#### 2. `backend/vm/cond.py` — 两处修改

```python
# A. _sprite_of() — sprite_opp 的 just_entered 改为读 ctx.opp_entered
# 找到:
"sprite_opp": {
    ...
    "just_entered": False,
    ...
}
# 改为:
"sprite_opp": {
    ...
    "just_entered": ctx.opp_entered,
    ...
}

# B. CONDITION_TRIGGERS — sprite_entered 加入 post_enemy_leave
# 找到:
"sprite_entered":          frozenset({"post_entry"}),
# 改为:
"sprite_entered":          frozenset({"post_entry", "post_enemy_leave"}),
```

#### 3. `backend/engine/snapshot.py` — 接受参数

```python
# build_ctx() 函数签名，在 opp_switched 参数附近新增：
def build_ctx(
    ...
    opp_switched: bool = False,
    self_switched: bool = False,
    opp_entered: bool = False,     # 新增
    ...
):
    return Ctx(
        ...
        opp_switched=opp_switched,
        self_switched=self_switched,
        opp_entered=opp_entered,   # 新增
        ...
    )
```

#### 4. `backend/sim/battle.py` — `_make_ctx()` 透传

```python
# _make_ctx 已有 **kwargs 透传到 build_ctx，无需修改。
# 确认 **kwargs 正确传递即可。
```

#### 5. `backend/sim/battle_mechanics.py` — 两处传参

```python
# A. switch 路径（~line 78）
ctx_enemy_leave = self._make_ctx(opp_active, new, None, None, self.globals,
    team=opp_team, turn=self.turn, opp_entered=True)  # 新增参数

# B. faint-replace 路径（~line 156）
ctx_ko_enemy = self._make_ctx(opp_active, new, None, None, self.globals,
    team=opp_team, turn=self.turn, opp_entered=True)  # 新增参数
```

---

### 扩展 #2: `mod stat: "revive"` — 精灵复活

**需求**：表达"力竭3回合后复活"（不朽）。

**IR 表示**：
```json
{"op": "mod", "target": "sprite_self", "stat": "revive", "value": 1.0}
```

**`revive` 语义**：
- `value` = HP 回复比例（1.0 = 全满）
- `hp = max(1, int(max_hp * value))`
- 清除精灵力竭标记（`is_fainted = False`）
- 若同队当前位力竭 → 自动切上场（`player.active_index = revived_idx`，触发入场事件）

**代码修改清单**（2 个文件，~20 行）：

#### 1. `backend/engine/replayer.py` — `_apply_mod_sprite()` 新增分支

```python
# 在 _apply_mod_sprite() 的 stat 分发中新增：
if stat == "revive":
    ratio = max(0.01, float(value))
    sprite.current_hp = max(1, int(sprite.max_hp * ratio))
    # 清除力竭标记（Sprite 需有 is_fainted 可写属性）
    # 自动上场逻辑（检测同队 active 是否力竭）
    ...
```

#### 2. `backend/sim/sprite.py` — 确保 `current_hp` 可写、`is_fainted` 可清除

```python
# 确认 Sprite 支持：
#   sprite.current_hp = new_value  → 可写
#   sprite.is_fainted = False       → 力竭标记可清除（或通过方法）
```

---

### 扩展 #3: `count` 阈值 + `counter_succeeded` 过滤

**需求**：表达"防御技能应对2次后，回满状态，变为棋绮后"（防御反击）。

**IR 表示**：
```json
{"op": "count",
 "when": {"cond": "counter_succeeded", "skill_type": "防御", "of": "sprite_self"},
 "threshold": 2, "reset_on_fire": true,
 "then": [
   {"op": "mod", "target": "sprite_self", "stat": "hp", "value": 1.0},
   {"op": "transform", "species": "棋绮后"}
 ]}
```

**新增字段**：

| 字段 | 位置 | 类型 | 默认 | 说明 |
|------|------|------|------|------|
| `threshold` | `count` | `int` | `1` | 触发次数阈值，>=N 时执行 `then` |
| `reset_on_fire` | `count` | `bool` | `true` | 执行 `then` 后内部计数器是否归零 |
| `skill_type` | `counter_succeeded` | `str` | 不填 | 过滤应对类型（`"防御"`/`"攻击"`/`"状态"`） |
| `of` | `counter_succeeded` | `str` | `"sprite_self"` | 谁的技能（`"sprite_self"`/`"sprite_opp"`） |

**代码修改清单**（3 个文件，~25 行）：

#### 1. `backend/vm/ir_skill.py` — `CountOp` 新增字段

```python
@dataclass(frozen=True)
class CountOp:
    name: str = ""
    when: SkillCondition | None = None
    then: tuple[SkillIROp, ...] = ()
    scope: str = "persistent"
    threshold: int = 1           # 新增
    reset_on_fire: bool = True   # 新增
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

#### 2. `backend/vm/cond.py` — `counter_succeeded` 新增过滤参数

```python
# 找到：
"counter_succeeded": lambda ctx, cond: ctx.event.counter_succeeded,

# 改为：
"counter_succeeded": lambda ctx, cond: (
    ctx.event.counter_succeeded
    and (cond.get("skill_type") is None or cond.get("skill_type") == ctx.skill_type_self if cond.get("of", "sprite_self") == "sprite_self" else ctx.skill_type_opp)
),
```

#### 3. `backend/engine/battle.py` — `register_counter()` 支持阈值计数

```python
# CounterRegister 新增 threshold / reset_on_fire 字段
# Observer 触发时：内部计数器 +1 → counter >= threshold → 执行 then → reset 归零
# 内部计数器存储于 _counter_values[name]
```

---

### 扩展 #4: `mod stat: "energy_cost_delta_mult"` — 能耗变化量倍率

**需求**：表达"技能受能耗变化效果影响翻倍"（能耗波动）。

**IR 表示**：
```json
{"op": "mod", "target": "sprite_self", "stat": "energy_cost_delta_mult", "value": 2.0}
```

**`energy_cost_delta_mult` 语义**：
- 存储在 `sprite._modifiers["energy_cost_delta_mult"]`
- 所有 `energy_cost` modifier 的 delta 应用时 × multiplier
- 例如 `energy_cost -= 1` → 实际 `energy_cost -= 2`（multiplier = 2.0）
- 仅影响变化量，不影响最终值

**代码修改清单**（2 个文件，~10 行）：

#### 1. `backend/vm/ops/mod.py` — `_SKILL_MOD_STATS` 新增

```python
_SKILL_MOD_STATS = frozenset({
    "power", "energy_cost", "combo", "priority",
    "power_mult", "damage_mult", "damage_reduction", "life_drain",
    "energy_cost_mult", "combo_mult",
    "energy_cost_delta_mult",  # 新增
})
```

#### 2. `backend/engine/snapshot.py` — `build_ctx` 读取 multiplier

```python
# 在计算 energy_cost_reduction_self 时乘以 delta_mult：
delta_mult = ss._modifiers.get("energy_cost_delta_mult", 1.0)
energy_cost_reduction = int(energy_cost_reduction * delta_mult)
```

---

### 扩展 #5: `element_self` + ADDRESS_MAP — 己方技能系别查询

**需求**：表达"受到自己携带技能系别的攻击伤害-40%"（偏振）。

**IR 表示**：
```json
{"op": "count",
 "when": {"cond": "have_skill_of", "of": "sprite_opp",
  "element": {"q": "element", "of": "skill_off_0"}},
 "then": [
   {"op": "mod", "target": "sprite_opp", "stat": "damage_reduction",
    "value": 0.4, "per_hit": true, "scope": "battlefield"}
 ],
 "scope": "persistent"}
```

**涉及的代码改动**（3 个文件，全部已实现）：

#### 1. `backend/vm/ctx.py` — Ctx 新增 `element_self` 字段 + ADDRESS_MAP 新增条目

```python
# Ctx dataclass (~line 105):
element_self: str = ""               # current skill element

# ADDRESS_MAP (~line 233):
("skill_off_0", "element"):            "element_self",
```

`have_skill_of` 的 `element` 参数支持动态 Query（`{"q": "element", "of": "skill_off_0"}`），通过 ADDRESS_MAP 解析为 `ctx.element_self`。

#### 2. `backend/engine/snapshot.py` — `build_ctx()` 填充 `element_self`

```python
# ~line 266:
element_self=getattr(sk, 'element', ""),
```

#### 3. `backend/vm/resolve.py` — 字符串别名（已存在）

```python
# ~line 196:
"skill.element": "element_self",
```

**注意**：`have_skill_of` 条件没有 `CONDITION_TRIGGERS` 条目，因此 Observer 不能通过 `listen` 过滤触发。偏振 Observer 依赖 `pre_calc` 事件无条件遍历所有 Observer 并直接求值条件。`pre_calc` 的 ctx 为攻击方视角（self=攻击方），cond 检查防御方是否拥有攻击方的技能系别。

**设计要点 — Observer 方向性**：
- `pre_calc` 触发时，ctx 为攻击方视角
- 己方攻击：self=我们, opp=敌方。cond 检查敌方是否拥有我们的技能系别 → 正确
- 敌方攻击：self=敌方, opp=我们。cond 检查我们是否拥有敌方的技能系别 → 条件不命中（我们不一定有敌方系别技能），偏振不触发 → 正确

**状态**：此扩展的代码修改已在代码库中实现。文档更新（本次）补齐 Ctx 字段说明和 ADDRESS_MAP 条目。```

---

### 扩展 #6: `sprite_acted` — 精灵行动后触发

**需求**：表达"每次行动后-20%"（衰减强化）+ "使用防御技能后标记"（防御脱离）。

**IR 表示**：
```json
{"op": "count",
 "when": {"cond": "sprite_acted", "of": "sprite_self"},
 "then": [
   {"op": "mod", "target": "sprite_self", "stat": "atk", "steps": -1}
 ],
 "scope": "persistent"}
```

支持可选技能过滤参数：
```json
{"cond": "sprite_acted", "of": "sprite_self", "skill_type": "防御"}
```

| 过滤参数 | 类型 | 说明 |
|----------|------|------|
| `skill_type` | `str` | 匹配技能类型（`"物攻"`/`"魔攻"`/`"防御"`/`"状态"`） |
| `element` | `str`/`dict` | 匹配技能系别 |
| `tag` | `str` | 匹配技能标签 |
| `name` | `str` | 匹配技能名称（如 `"聚能"`） |
| `of` | `str` | 行动方：`"sprite_self"`（自己）/ `"sprite_opp"`（敌方） |

**新增条件**：

| 字段 | 说明 |
|------|------|
| 条件名 | `sprite_acted` |
| 触发点 | `post_skill` |
| 参数 | `of`: `"sprite_self"` (自己行动后) / `"sprite_opp"` (敌方行动后)；`skill_type`/`element`/`tag`/`name`（可选） |
| eval | `_skill_use_matches(ctx, cond) and _sprite_of_matches(ctx, cond.get("of", "sprite_self"))` |

**代码修改清单**（1 个文件，~12 行）：

#### `backend/vm/cond.py` — 三处修改

```python
# A. _skill_use_matches — 新增 name 过滤（~4 行）：
# 在现有 energy_cost 过滤后追加：
if "name" in cond:
    if ctx.skill_name_self != cond["name"]:
        return False

# B. COND_EVAL — 新增 eval 条目（~3 行）：
# of 过滤不需要专门的 ctx 字段——当 post_skill 触发时，ctx.self 就是行动方。
# Observer fire_trigger 从 battle 的特定队伍视角构建 ctx，因此：
#   - 我方行动 → ctx 视角是我方 → ctx.self 是我方精灵
#   - 敌方行动 → ctx 视角是敌方 → ctx.self 是敌方精灵
# 关键：Observer 注册在哪一方就只在哪一方触发。所以 sprite_acted + of:"sprite_opp"
# 的 Observer 应该注册在己方精灵上，当 fire_trigger("post_skill", team=ours) 时
# ctx 视角是我们自己，不匹配 of:"sprite_opp" → 跳过。
# 当 fire_trigger("post_skill", team=theirs) 时 ctx 视角是敌方，匹配 → 触发。
# 实现上需要 _sprite_of_matches 判断 ctx 视角：
def _sprite_of_matches(ctx: Ctx, of: str) -> bool:
    \"\"\"Check if ctx.self matches the requested of side.
    
    We infer side from team_self (ctx.team_self) if available, or use the
    trick: Observer registered on our sprite fires from our team's triggers.
    So ctx.self IS our sprite when of=="sprite_self", and IS enemy when
    of=="sprite_opp" requires a separate trigger cycle from the enemy team.
    
    In practice: Observer with scope:"persistent" registered on our sprite
    has its count stored in our sprite's observer list. fire_trigger iterates
    our sprites. So ctx is always from our perspective → ctx.self is always
    our sprite → of:"sprite_opp" would always be False.
    
    THEREFORE: to detect enemy actions, use the team routing trick
    (team_counter_write target:"opp") instead. of:"sprite_opp" is documented
    for future use when cross-team Observer firing is supported.
    \"\"\"
    if of == "sprite_self":
        return True  # ctx.self is always the sprite being processed
    # of == "sprite_opp": requires ctx.team_self to be populated
    return getattr(ctx, "team_self", None) is not None

"sprite_acted": lambda ctx, cond: (
    _skill_use_matches(ctx, cond)
    and _sprite_of_matches(ctx, cond.get("of", "sprite_self"))
),

# C. CONDITION_TRIGGERS — 新增触发点映射：
"sprite_acted": frozenset({"post_skill"}),
```

**代码修改清单（附带 ctx 字段）**（1 个文件，~2 行）：

#### `backend/vm/ctx.py` — 新增字段

```python
# Ctx dataclass 新增：
team_self: str = ""  # "A" or "B" — 当前 ctx 视角所属队伍
```

#### `backend/engine/snapshot.py` — 填充 team_self（~1 行）

```python
# build_ctx() 中：
team_self=battle_team,  # "A" or "B"
```

**设计要点**：
- `_skill_use_matches` 已支持 `element`、`skill_type`、`tag`、`energy_cost` 过滤（`cond.py:122-145`），新增 `name` 过滤
- `_skill_use_matches` 无过滤参数时返回 `True`，向后兼容"每次行动后-20%"的用法
- `sprite_acted` + `of` 的典型用例：敌方使用【聚能】技能后计数、自己使用防御技能后标记
- **`of: "sprite_opp"` 的实际限制**：当前 Observer 系统按队伍分别触发，Observer 注册在哪一方就只在那一方的 fire_trigger 中触发。因此 `of: "sprite_opp"` 在当前架构下不会匹配（ctx.self 始终是注册方）。**推荐使用团队路由技巧**：`team_counter_write target: "opp"` — 当敌方行动时，replayer.team 是敌方队，`target: "opp"` 写回我方计数器（方向正确）；当我方行动时写入敌方计数器（无害垃圾）。`team_self` 字段为未来的跨队伍 Observer 触发预留。

**注意**：`post_skill` 已存在于引擎触发点表，不需要新增触发点。只需在 `cond.py` 建立 `sprite_acted → post_skill` 的映射关系。```

---

### 扩展 #7: `("skill_off_0", "name")` — 按技能名过滤

**需求**：表达"【虫鸣】技能威力+20"（虫鸣）。

**IR 表示**：
```json
{"op": "count",
 "when": {"cond": "compare", "q": "name", "of": "skill_off_0", "op": "eq", "value": "虫鸣"},
 "then": [
   {"op": "mod", "target": "skill_off_0", "stat": "power", "value": 20}
 ],
 "scope": "permanent"}
```

**代码修改清单**（1 个文件，~1 行）：

#### `backend/vm/ctx.py` — ADDRESS_MAP 新增条目

```python
# ADDRESS_MAP 中 skill_off_0 区块新增：
("skill_off_0", "name"):             "skill_name_self",
```

`Ctx.skill_name_self` 字段已存在（`ctx.py:113`），由 `build_ctx` 填充，无需额外修改。

**设计要点**：
- `compare` 条件无 `CONDITION_TRIGGERS` → `listen: {}`，但 `pre_calc` 无条件遍历所有 Observer
- 在 `pre_calc` 阶段求值 `ctx.skill_name_self == "虫鸣"` → 命中则在伤害计算前注入 `power +20`
- 与偏振（扩展 #5）同模式：`compare` + `pre_calc` 无条件遍历```

---

### 扩展 #8: `sprite_left` — 精灵离场触发

**需求**：表达"离场后敌方能耗恢复"（能耗光环）。

**IR 表示**：
```json
{"op": "count",
 "when": {"cond": "sprite_left", "of": "sprite_self"},
 "then": [
   {"op": "dispel", "target": "sprite_opp", "source": "能耗光环"}
 ],
 "scope": "persistent"}
```

**新增条件**：

| 字段 | 说明 |
|------|------|
| 条件名 | `sprite_left` |
| 触发点 | `post_leave` |
| 参数 | `of`: `"sprite_self"` (自己离场) / `"sprite_opp"` (敌方离场) |
| eval | `lambda ctx, cond: True`（`post_leave` 时 ctx.self 即为离场精灵） |

**代码修改清单**（1 个文件，~4 行）：

#### `backend/vm/cond.py` — 两处修改

```python
# A. COND_EVAL — 新增 eval 条目：
"sprite_left": lambda ctx, cond: True,   # post_leave 时 self 即离场者

# B. CONDITION_TRIGGERS — 新增触发点映射：
"sprite_left": frozenset({"post_leave"}),
```

**注意**：`post_leave` 已存在于引擎触发点表。`sprite_entered` + `sprite_left` 配对覆盖了入/离场的完整生命周期。```

---

### 扩展 #9: `inherit_effects.effects` + `freeze_immune` — 效果继承 & 异常免疫

**需求**：表达"离场后，更换入场的精灵获得双防+20%且免疫冻结"（效果继承）。

**IR 表示**：
```json
{"op": "inherit_effects", "target": "ally_new",
 "effects": [
   {"op": "mod", "target": "sprite_self", "stat": "def", "steps": 1, "scope": "battlefield"},
   {"op": "mod", "target": "sprite_self", "stat": "sp_def", "steps": 1, "scope": "battlefield"},
   {"op": "mod", "target": "sprite_self", "stat": "freeze_immune", "value": 1.0, "scope": "battlefield"}
 ]}
```

**改动分两个子扩展**：

#### 9a. `inherit_effects` 新增 `effects` 字段

**代码修改清单**（2 个文件，~8 行）：

##### 1. `backend/vm/ir_skill.py` — `InheritEffects` 新增字段

```python
@dataclass(frozen=True)
class InheritEffects:
    source: str = "self"
    inherit_target: str = "enemy_new"
    scope: str = "battlefield"
    via_pending: bool = False
    effects: tuple[SkillIROp, ...] = ()   # 新增：要传递的 Skill IR 效果列表
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

##### 2. `backend/vm/ops/inherit_effects.py` — handler 实现（新建）

```python
def op_inherit_effects(ctx: Ctx, op) -> list[Mutation]:
    """Queue effects for the incoming sprite via battle.pending_effects."""
    return [PendingEffects(
        target=op.inherit_target,  # "ally_new" | "enemy_new"
        effects=list(op.effects),
    )]
```

**运作流程**：
1. `post_leave` → Observer 命中 → `inherit_effects` → 产生 `PendingEffects` mutation
2. `JournalReplayer` 将 `PendingEffects` 存入 `battle.pending_effects[team]`
3. 入场时 `_consume_pending_effects()` → 对入场精灵执行 `effects`

#### 9b. `freeze_immune` — mod 新 flag stat

**代码修改清单**（1 个文件，~1 行）：

##### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增

```python
_FLAG_STATS = frozenset({
    "life_as_energy", "survive", "extra_action", "extra_turn_end",
    "freeze_immune",  # 新增：免疫冻结
})
```

**语义**：`freeze_immune = 1.0` 时，引擎在 `_apply_abnormal` 前检查目标精灵的 `_modifiers.get("freeze_immune", 0)`，若 > 0 则跳过"冻结"类异常的施加。```

---

### 扩展 #10: `("team_own", "lives")` / `("team_opp", "lives")` — 队伍魔力值查询

**需求**：表达"入场时若己方魔力值为1，获得双攻+50%"（绝境）。

**IR 表示**：
```json
{"op": "count",
 "when": {"cond": "and", "conditions": [
   {"cond": "sprite_entered", "of": "sprite_self"},
   {"cond": "compare", "q": "lives", "of": "team_own", "op": "eq", "value": 1}
 ]},
 "then": [
   {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0.5, "scope": "persistent"},
   {"op": "mod", "target": "sprite_self", "stat": "sp_atk", "value": 0.5, "scope": "persistent"}
 ],
 "scope": "persistent"}
```

**代码修改清单**（1 个文件，~2 行）：

#### `backend/vm/ctx.py` — ADDRESS_MAP 新增两条

```python
# team_own 区新增：
("team_own", "lives"):           "lives_own",

# team_opp 区新增：
("team_opp", "lives"):           "lives_opp",
```

`Ctx.lives_own` 和 `Ctx.lives_opp` 字段已存在（`ctx.py:93-94`），由 `build_ctx` 填充，无需额外修改。```

---

### 扩展 #11: 连击 per-hit 触发 — `post_damage` 每段独立触发

**需求**：连击每段应独立触发 `post_damage` → `on_damage_taken`，而非合并为一次。

**现状**：
```
技能执行 → calc_damage(combo_count=3) → 1 个 Damage(amount=总量) → post_damage ×1
```

**目标**：
```
技能执行 → per-hit loop ×combo_count → N 个 Damage(per-hit amount) → post_damage ×N
```

**代码修改清单**（3 个文件，~15 行）：

#### 1. `backend/vm/damage.py` — `combo_count` 移出公式

```python
# calc_damage: 删除 combo_count 参数，公式中去掉 combo_count 乘法
# line 24: 删除 combo_count: int = 1 形参
# line 66: core *= damage_mult  （原 * combo_count * damage_mult）
```

连击次数不再影响单体伤害公式，由调用方循环 N 次自然实现总伤害 = N × per_hit。

#### 2. `backend/vm/journal.py` — `Damage` 新增字段

```python
@dataclass(frozen=True)
class Damage:
    target: str
    amount: int
    element: str
    type: str
    combo_count: int = 1   # 新增：此 Damage 代表的连击段数
```

#### 3. `backend/engine/battle.py` — 两处改动

**A. 创建 Damage 时 per-hit 循环：**
```python
# 旧：一个 Damage，combo_count 乘入公式
amount = calc_damage(..., combo_count=ctx.combo_self, ...)
journal.append(Damage(...))

# 新：combo_count 个 Damage，每个独立计算
for _ in range(ctx.combo_self):
    amount = calc_damage(...)  # 无 combo_count 参数
    journal.append(Damage(target="sprite_opp", amount=amount,
                          element=ctx.element_self, type=ctx.skill_type_self))
```

**B. `post_damage` 去重逻辑修改：**
```python
# 旧：按 trigger 名去重，多个 Damage 只触发一次 post_damage
if trigger and trigger not in fired:
    fired.add(trigger)
    ev = self._fire_post_event(trigger, ctx, replayer)

# 新：Damage 不参与去重，每个 Damage 独立触发
for m in journal:
    if isinstance(m, Damage):
        ev = self._fire_post_event("post_damage", ctx, replayer)
        events.extend(ev)
# 其余 trigger 保持去重
```

**影响分析**：

| 维度 | 变化 |
|------|------|
| 总伤害 | 不变（N × per_hit ≈ 原 total，圆整误差 ±combo_count 点） |
| `post_damage` | 每段连击独立触发（原 1 次 → N 次） |
| `on_damage_taken` | 每段连击独立触发 Observer |
| `per_hit` 修饰器 | 每段独立衰减（per_hit 衰减现在用于降低每段伤害，而非总伤） |

**设计意图**：连击的语义是"多次命中"，每段应独立触发命中效果（反伤、奉献、印记等），与 per_hit 修饰器语义一致。```

---

### 扩展 #12: `mark_stacks` 查询 + `float_scale` 精度

**需求**：表达"敌方每有1层星陨印记，技能威力+15%"（星陨共鸣）。

**IR 表示**：
```json
{"op": "mod", "target": "skill_off_0", "stat": "power_mult",
 "value": {"q": "mark_stacks", "of": "team_opp", "name": "星陨", "scale": 0.15}}
```

**改动分两个子扩展**：

#### 12a. `mark_stacks` 查询支持

**代码修改清单**（2 个文件，~4 行）：

##### 1. `backend/vm/ctx.py` — ADDRESS_MAP 新增两条

```python
("team_own", "mark_stacks"):     "mark_stacks_own",
("team_opp", "mark_stacks"):     "mark_stacks_opp",
```

##### 2. `backend/vm/resolve.py` — `_NAMED_DICT_QUERIES` 新增

```python
_NAMED_DICT_QUERIES = frozenset({
    "counter_value", "abnormal_stacks", "devotion", "skill_count", "team_counter",
    "mark_stacks",  # 新增
})
```

`Ctx.mark_stacks_own` / `mark_stacks_opp` 字段已存在（`ctx.py:81-83`）。

#### 12b. `float_scale` — 避免 int 截断

**问题**：`_resolve_dict_query` 中 `scale` 使用 `int()` 截断：
```python
# 3 层星陨 × 0.15 = 0.45 → int(0.45) = 0  ❌
raw = int(raw * value["scale"])
```

**方案**：新增 `float_scale` 字段，保留浮点精度：
```python
if "scale" in value:
    raw = int(raw * value["scale"])
if "float_scale" in value:
    raw = raw * value["float_scale"]   # 不截断
```

IR 中使用 `float_scale` 替代 `scale`：
```json
{"value": {"q": "mark_stacks", "of": "team_opp", "name": "星陨", "float_scale": 0.15}}
```

**代码修改**：`backend/vm/resolve.py` `_resolve_dict_query` 加 3 行。```

---

### 扩展 #13: `team_has_element` — 队伍元素检测

**需求**：表达"队伍存在虫系精灵 → 双攻+50%"（虫群羁绊）。

**IR 表示**：
```json
{"op": "count",
 "when": {"cond": "team_has_element", "element": "虫"},
 "then": [
   {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0.5, "mode": "set", "scope": "persistent"}
 ],
 "scope": "persistent"}
```

配合 `not` 清除：
```json
{"op": "count",
 "when": {"cond": "not", "condition": {"cond": "team_has_element", "element": "虫"}},
 "then": [
   {"op": "mod", "target": "sprite_self", "stat": "atk", "value": 0, "mode": "set", "scope": "persistent"}
 ],
 "scope": "persistent"}
```

**新增条件**：

| 字段 | 说明 |
|------|------|
| 条件名 | `team_has_element` |
| 触发点 | `post_entry`, `post_leave`, `post_ko` |
| 参数 | `element`: 系别名 |
| eval | `lambda ctx, cond: cond["element"] in ctx.team_elements_own` |

**代码修改清单**（3 个文件，~8 行）：

#### 1. `backend/vm/ctx.py` — Ctx 新增字段

```python
team_elements_own: frozenset[str] = frozenset()  # all elements across team
```

#### 2. `backend/vm/cond.py` — 两处修改

```python
# COND_EVAL:
"team_has_element": lambda ctx, cond: cond["element"] in ctx.team_elements_own,

# CONDITION_TRIGGERS:
"team_has_element": frozenset({"post_entry", "post_leave", "post_ko"}),
```

#### 3. `backend/engine/snapshot.py` — `build_ctx` 填充

```python
# 扫描己方队伍所有精灵的元素集合：
team_elements_own = frozenset(
    e for s in team.sprites for e in s.elements
)
```

`mode: "set"` 配合正/反两个 Observer 实现了条件的动态开关——队伍有虫系时设值，失去虫系时清零。```

---

### 扩展 #14: `bloodline` 查询 — 精灵血脉检测

**需求**：表达"攻击时若敌方血脉是污染血脉，威力+100%"（血脉克星）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "血脉克星",
   "scope": "battlefield",
   "when": {"cond": "compare",
    "q": {"target": "sprite_opp", "query": "bloodline"},
    "op": "eq",
    "value": "污染血脉"
   },
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "power_mult", "value": 1.0}
   ]}
]}
```

**代码修改清单**（3 个文件，~7 行）：

#### 1. `backend/vm/ctx.py` — Ctx 新增字段 + ADDRESS_MAP 新增条目

```python
# Ctx dataclass 新增：
bloodline_self: str = ""            # 己方精灵血脉
bloodline_opp: str = ""             # 敌方精灵血脉

# ADDRESS_MAP 新增：
("sprite_self", "bloodline"):       "bloodline_self",
("sprite_opp", "bloodline"):        "bloodline_opp",
```

#### 2. `backend/engine/snapshot.py` — `build_ctx` 填充

```python
# 从 sprite.bloodline 填充：
bloodline_self=getattr(self_sprite, 'bloodline', ''),
bloodline_opp=getattr(opp_sprite, 'bloodline', ''),
```

#### 3. `backend/vm/cond.py` — `compare_op` 已支持字符串比较（无需修改）

`compare_op` 使用 Python 原生 `==` 运算符，`eq` 操作天然支持字符串比较：
```python
if op == "eq":
    return a == b    # "污染血脉" == "污染血脉" → True
```

**设计要点**：
- `compare` 条件无 `CONDITION_TRIGGERS` → `listen: {}`，依赖 `pre_calc` 无条件遍历所有 Observer
- `pre_calc` 时 ctx 为攻击方视角（self=攻击方, opp=敌方），检查 `bloodline_opp == "污染血脉"` 正确命中
- `bloodline` 在 `Sprite` 上已存在（`sprite.py:87`），只需透传到 Ctx
- 不需要新 opcode、不需要新 condition type
- 如需表达"己方血脉为X时触发"，改为 `"q": {"target": "sprite_self", "query": "bloodline"}` 即可```

---

### 扩展 #15: `gain_skills` — 临时获得技能

**需求**：表达"额外获得三个未携带的随机技能，非光系技能威力+25%"（技能扩展）。

**IR 表示**：
```json
{"effects": [
  {"op": "gain_skills", "count": 3, "exclude_carried": true, "source": "learnset"},
  {"op": "count",
   "name": "非光增幅",
   "scope": "permanent",
   "when": {"cond": "not", "condition": {"cond": "compare", "q": "element", "of": "skill_off_0", "op": "eq", "value": "光"}},
   "then": [
     {"op": "mod", "target": "skill_off_0", "stat": "power_mult", "value": 0.25, "per_hit": true}
   ]}
]}
```

**新增 opcode**：`gain_skills`

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `op` | `"gain_skills"` | — | 操作码 |
| `count` | `int` | `1` | 获得技能数 |
| `exclude_carried` | `bool` | `true` | 排除已有技能 |
| `source` | `str` | `"learnset"` | `"learnset"`（精灵可学技能池）/ `"global"`（全体） |
| `target` | `str` | `"sprite_self"` | 获得技能的对象 |

**语义**：
- 执行时从指定池中随机选取 `count` 个精灵不携带的技能
- "随机"由引擎运行时处理，IR 只声明意图，不做随机采样
- 获得的技能为临时技能（战斗结束后失效），加入当前精灵技能栏末尾
- `scope` 默认为 `"battlefield"`（入场失效不保留）

**代码修改清单**（3 个文件，~20 行）：

#### 1. `backend/vm/ir_skill.py` — 新增 `GainSkills` dataclass + `SkillIROp` union

```python
@dataclass(frozen=True)
class GainSkills:
    count: int = 1
    exclude_carried: bool = True
    source: str = "learnset"      # "learnset" | "global"
    target: str = "sprite_self"
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

#### 2. `backend/vm/ops/gain_skills.py` — handler 新建

```python
def op_gain_skills(ctx: Ctx, op) -> list[Mutation]:
    return [GainSkillsMutation(
        count=op.count,
        exclude_carried=op.exclude_carried,
        source=op.source,
        target=op.target,
    )]
```

#### 3. `backend/vm/executor.py` — match/case + `_DICT_DISPATCH` 注册

```python
# _DICT_DISPATCH:
"gain_skills": op_gain_skills,
```

**引擎侧处理**（`backend/engine/replayer.py`）：
- `GainSkillsMutation` → sprite 的 `add_temporary_skills()` 方法
- 从 `sprite.learnset` 或全局技能池随机选取未携带的技能
- 添加到 `sprite.skills` 列表，标记为临时技能（`is_temporary=True`），战斗结束后清理```

---

### 扩展 #16: `transform` — 精灵形态变换

**需求**：表达"状态技能应对1次后，回满状态，变为棋绮后"（防御反击变种）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "when": {"cond": "counter_succeeded", "skill_type": "状态", "of": "sprite_self"},
   "threshold": 1, "reset_on_fire": true,
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "hp", "value": 1.0},
     {"op": "transform", "species": "棋绮后"}
   ]}
]}
```

**新增 opcode**：`transform`

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `op` | `"transform"` | — | 操作码 |
| `species` | `str` | — | 目标精灵名称 |
| `skills` | `tuple[str]` | `()` | 替换技能名列表（空则保留原技能） |
| `reset_hp` | `bool` | `false` | 回满 HP（当前值 = maxHP） |
| `reset_energy` | `bool` | `false` | 回满能量 |
| `target` | `str` | `"sprite_self"` | 变换对象 |

**语义**：
- 将当前精灵的物种/形态切换为 `species`，同时更新种族值、属性、系别等基础数据
- `skills` 为空时保留变形前技能；非空时替换为指定技能
- `reset_hp` / `reset_energy` 为 true 时在变形后立即回满对应资源
- 若需精细控制 HP 恢复量（如半血变形），使用独立的 `mod hp` 放在 `then` 中
- 形态变换为单向不可逆（要变回需另一个 `transform` op）

**代码修改清单**（3 个文件，~25 行）：

#### 1. `backend/vm/ir_skill.py` — 新增 `Transform` dataclass + `SkillIROp` union

```python
@dataclass(frozen=True)
class Transform:
    species: str = ""
    skills: tuple[str, ...] = ()
    reset_hp: bool = False
    reset_energy: bool = False
    target: str = "sprite_self"
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

#### 2. `backend/vm/ops/transform.py` — handler 新建

```python
def op_transform(ctx: Ctx, op) -> list[Mutation]:
    return [TransformMutation(
        species=op.species,
        skills=list(op.skills) if op.skills else [],
        reset_hp=op.reset_hp,
        reset_energy=op.reset_energy,
        target=op.target,
    )]
```

#### 3. `backend/vm/executor.py` — match/case + `_DICT_DISPATCH` 注册

```python
# _DICT_DISPATCH:
"transform": op_transform,
```

**引擎侧处理**（`backend/engine/replayer.py`）：
- `TransformMutation` → 调用 `sprite.transform(species)` 更新种族值/属性
- 根据 `skills` 是否为空决定保留或替换技能
- 根据 `reset_hp`/`reset_energy` 决定是否回满资源
- 变换后需更新 Ctx 快照（后续 Observer 求值使用新物种数据）```

---

### 扩展 #17: `skill_cd` + `skill_name_opp` — 技能冷却 & 敌方技能名查询

**需求**：表达"打断敌方时，被打断技能进入2回合冷却"（打断冷却）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "打断冷却",
   "scope": "persistent",
   "when": {"cond": "counter_succeeded", "of": "sprite_self"},
   "then": [
     {"op": "skill_cd", "target": "sprite_opp", "turns": 2, "skill": "current"}
   ]}
]}
```

**新增 opcode**：`skill_cd`

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `op` | `"skill_cd"` | — | 操作码 |
| `target` | `str` | `"sprite_opp"` | 目标精灵 |
| `turns` | `int` | `1` | 冷却回合数 |
| `skill` | `str`/`dict` | `"current"` | `"current"` = 目标当前技能 / 技能名 / Query |

**语义**：
- `skill: "current"` — 引擎在运行时解析为目标精灵当前使用的技能（`counter_succeeded` 上下文中即被打断的技能），无需显式传技能名
- 冷却期间该技能不可被选择/使用
- 每回合结束时冷却-1，归零后恢复可用
- 与 `lock`（禁换宠/禁脱离）语义分离，保持 opcode 职责单一

**Ctx 字段**（可选扩展，同时加入）：

```python
# Ctx dataclass:
skill_name_opp: str = ""             # 敌方当前技能名

# ADDRESS_MAP:
("sprite_opp", "current_skill"):     "skill_name_opp",
```

`skill_name_opp` 使得未来可按敌方技能名做动态查询（如 `compare` 判断敌方是否在使用特定技能），但不影响 `skill: "current"` 的简写用法。

**代码修改清单**（5 个文件，~25 行）：

#### 1. `backend/vm/ir_skill.py` — 新增 `SkillCD` dataclass

```python
@dataclass(frozen=True)
class SkillCD:
    target: str = "sprite_opp"
    turns: int = 1
    skill: str = "current"        # "current" | skill_name | dict query
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

#### 2. `backend/vm/ops/skill_cd.py` — handler 新建

```python
def op_skill_cd(ctx: Ctx, op) -> list[Mutation]:
    skill = op.skill
    if isinstance(skill, dict):
        skill = resolve(ctx, skill)  # resolve dynamic query
    return [SkillCooldownMutation(
        target=op.target,
        turns=op.turns,
        skill=skill,
    )]
```

#### 3. `backend/vm/executor.py` — 注册

```python
"skill_cd": op_skill_cd,
```

#### 4. `backend/vm/ctx.py` — Ctx + ADDRESS_MAP（可选）

```python
# Ctx 新增：
skill_name_opp: str = ""

# ADDRESS_MAP 新增：
("sprite_opp", "current_skill"):     "skill_name_opp",
```

#### 5. `backend/engine/snapshot.py` — 填充

```python
skill_name_opp=getattr(opp_skill, 'name', ''),
```

**引擎侧处理**（`backend/engine/replayer.py`）：
- `SkillCooldownMutation` → 若 `skill == "current"`，转为 `target_sprite.current_skill_name`
- 调用 `target_sprite.add_cooldown(skill_name, turns)` 设置冷却
- `target_sprite.current_skill_name` 在技能开始执行时设置，执行结束后清除```

---

### 扩展 #18: `charge_any_skill` — 蓄力期间自由选招

**需求**：表达"蓄力状态下可使用任一携带技能"（蓄力自由选招）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "蓄力自由选招",
   "scope": "permanent",
   "when": {"cond": "compare",
    "q": {"target": "sprite_self", "query": "is_charging"},
    "op": "eq", "value": true},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "charge_any_skill", "value": 1.0}
   ]},
  {"op": "count",
   "name": "蓄力自由选招_关",
   "scope": "permanent",
   "when": {"cond": "not", "condition": {"cond": "compare",
    "q": {"target": "sprite_self", "query": "is_charging"},
    "op": "eq", "value": true}},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "charge_any_skill", "value": 0.0, "mode": "set"}
   ]}
]}
```

**新增 flag stat**：`charge_any_skill`

代码修改清单（1 个文件，~1 行）：

#### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增

```python
_FLAG_STATS = frozenset({
    "life_as_energy", "survive", "extra_action", "extra_turn_end",
    "heal_reverse", "immune", "drive",
    "freeze_immune",          # 扩展 #9b
    "charge_any_skill",       # 新增：蓄力期间可选任意技能
})
```

**语义**：
- 正反两个 Observer 形成动态开关：蓄力中 flag=1，蓄力结束清零
- `is_charging` 已在 ADDRESS_MAP（`ctx.py:179`），`compare` + `eq` 直接可用
- `compare` 操作符 `eq` 支持 boolean 比较（Python `True == True`）
- 引擎侧（技能选择 UI）检查 `sprite._modifiers.get("charge_any_skill", 0) > 0` 时放行任意技能选择
- 纯 flag — 不产生伤害/能量/异常等 mutation，只影响 UI 层的技能可选范围```

---

### 扩展 #19: `team_bench_own` / `team_bench_opp` — 板凳精灵聚合查询

**需求**：表达"己方其他精灵每有1层萌化，自己入场时全技能能耗-1"（萌化共鸣）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "萌化共鸣",
   "scope": "persistent",
   "when": {"cond": "sprite_entered", "of": "sprite_self"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "energy_cost",
      "value": {"q": "abnormal_stacks", "of": "team_bench_own", "name": "萌化", "scale": -1}}
   ]}
]}
```

**新增查询目标**：`team_bench_own` / `team_bench_opp`

| 查询 | 含义 | 支持的 query 类型 |
|------|------|-----------------|
| `team_bench_own` | 己方板凳（非活跃）精灵聚合 | `abnormal_stacks`, `mark_stacks` |
| `team_bench_opp` | 敌方板凳精灵聚合 | `abnormal_stacks`, `mark_stacks` |

**代码修改清单**（2 个文件，~12 行）：

#### 1. `backend/vm/ctx.py` — Ctx 新增字段 + ADDRESS_MAP

```python
# Ctx dataclass 新增：
abnormal_stacks_bench_own: dict[str, int] = field(default_factory=dict)
abnormal_stacks_bench_opp: dict[str, int] = field(default_factory=dict)

# ADDRESS_MAP 新增：
("team_bench_own", "abnormal_stacks"):  "abnormal_stacks_bench_own",
("team_bench_opp", "abnormal_stacks"):  "abnormal_stacks_bench_opp",
```

#### 2. `backend/engine/snapshot.py` — `build_ctx` 聚合填充

```python
# 遍历己方板凳精灵，聚合异常层数：
abnormal_stacks_bench_own = {}
for s in team.bench_sprites:
    for ab_name, stacks in s.abnormal_stacks.items():
        abnormal_stacks_bench_own[ab_name] = abnormal_stacks_bench_own.get(ab_name, 0) + stacks

# 敌方同理：
abnormal_stacks_bench_opp = {}
for s in opp_team.bench_sprites:
    for ab_name, stacks in s.abnormal_stacks.items():
        abnormal_stacks_bench_opp[ab_name] = abnormal_stacks_bench_opp.get(ab_name, 0) + stacks
```

**设计要点**：
- `abnormal_stacks` 已在 `_NAMED_DICT_QUERIES`，`name: "萌化"` 子索引自动生效
- `scale: -1` 将正层数转为负值 → `energy_cost -= stacks`（每层-1能耗，非反比例缩放）
- `scope: "persistent"`：入场时一次性快照计算，之后 bench 萌化变化不影响已在场的精灵
- 板凳聚合不含当前活跃精灵自身（`sprite_self` 不在 bench 中），自动满足"己方**其他**精灵"的语义```

---

### 扩展 #20: `skill_slot_lock` — 按槽位禁用技能

**需求**：表达"仅可使用1号和3号位技能"（封印2号和4号位）。

**IR 表示**：
```json
{"effects": [
  {"op": "skill_slot_lock", "target": "sprite_self", "slots": [2, 4]}
]}
```

**新增 opcode**：`skill_slot_lock`

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `op` | `"skill_slot_lock"` | — | 操作码 |
| `target` | `str` | `"sprite_self"` | 目标精灵 |
| `slots` | `tuple[int]` | `()` | 禁用的槽位号（1-indexed），空 = 解除全部 |
| `scope` | `str` | `"persistent"` | 生命周期 |

**语义**：
- 引擎在技能选择阶段检查 `sprite.locked_slots`，被锁槽位不可选
- 与 `skill_cd`（冷却 N 回合）不同：槽位锁持续存在，直到被 `dispel` 或精灵离场
- `slots: []` 可解除所有槽位锁（由反向 Observer 触发）
- 配合 `scope` 控制生命周期

**代码修改清单**（4 个文件，~20 行）：

#### 1. `backend/vm/ir_skill.py` — 新增 `SkillSlotLock` dataclass

```python
@dataclass(frozen=True)
class SkillSlotLock:
    target: str = "sprite_self"
    slots: tuple[int, ...] = ()
    scope: str = "persistent"
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

#### 2. `backend/vm/ops/skill_slot_lock.py` — handler 新建

```python
def op_skill_slot_lock(ctx: Ctx, op) -> list[Mutation]:
    return [SlotLockMutation(
        target=op.target,
        slots=list(op.slots),
    )]
```

#### 3. `backend/vm/executor.py` — 注册

```python
"skill_slot_lock": op_skill_slot_lock,
```

#### 4. `backend/engine/replayer.py` — 引擎侧处理

```python
# _apply_slot_lock(): 
if not mutation.slots:  # 空列表 = 解除
    target_sprite.locked_slots.clear()
else:
    target_sprite.locked_slots.update(mutation.slots)
```

**索引约定**：IR 使用 1-indexed（游戏层：1 号位 = 第一个技能），`locked_slots` 内部存储为 0-indexed（Python list 索引）。replayer 处理时 `slots = [s - 1 for s in mutation.slots]`，对外保持游戏语义一致。```

---

### 扩展 #21: `skill_self_all` + `skill_where` + `tag` — 按条件批量改造技能

**需求**：表达"携带的能耗<3的技能获得迅捷"（迅捷低耗）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "迅捷低耗",
   "scope": "persistent",
   "when": {"cond": "sprite_entered", "of": "sprite_self"},
   "then": [
     {"op": "mod", "target": "skill_self_all", "stat": "tag", "value": "迅捷",
      "skill_where": {"energy_cost": {"op": "lt", "value": 3}}}
   ]}
]}
```

**新增概念**：批量技能修饰

| 组件 | 类型 | 说明 |
|------|------|------|
| `target: "skill_self_all"` | 新目标 | 遍历己方所有携带技能 |
| `skill_where` | 新筛选 | 过滤条件，不填 = 全部命中 |
| `stat: "tag"` | 新 stat | 为技能添加/移除标签 |

**`skill_where` 支持的过滤 operator**：

| 字段 | 类型 | 示例 |
|------|------|------|
| `energy_cost` | `{"op": "lt"\|"lte"\|"eq"\|"gte"\|"gt", "value": N}` | `{"energy_cost": {"op": "lt", "value": 3}}` |
| `skill_type` | `"物攻"` / `"魔攻"` / `"防御"` / `"状态"` | `{"skill_type": "防御"}` |
| `element` | 系别名 | `{"element": "火"}` |
| `tag` | 标签名 | `{"tag": "必中"}` |

多个条件为 AND 关系。

**`tag` stat 语义**：
- `value: "迅捷"` → 添加标签；`value: ""` → 清除指定标签
- 标签为引擎级概念，影响技能的行为（迅捷 = 先手 +1，必中 = 回避无效等）
- 必须在 `_METADATA_STATS` 或等效集合中注册

**复用场景**：

```json
// 火系技能威力+20
{"op": "mod", "target": "skill_self_all", "stat": "power",
 "skill_where": {"element": "火"}, "value": 20}

// 防御技能能耗-1
{"op": "mod", "target": "skill_self_all", "stat": "energy_cost",
 "skill_where": {"skill_type": "防御"}, "value": -1}

// 所有技能获得必中
{"op": "mod", "target": "skill_self_all", "stat": "tag", "value": "必中"}
```

**代码修改清单**（3 个文件，~30 行）：

#### 1. `backend/vm/ops/mod.py` — 新增 `skill_self_all` 目标分发

```python
# op_mod() 中新增分支：
if _get_field(effect, "target") == "skill_self_all":
    return [SkillApplyMutation(
        target="sprite_self",
        stat=stat,
        value=value,
        skill_where=_get_field(effect, "skill_where", {}),
        metadata=_metadata(effect),
    )]
```

#### 2. `backend/engine/replayer.py` — `_apply_skill_all()` 新增

```python
def _apply_skill_all(self, mutation):
    """Apply a modifier to all carried skills matching skill_where."""
    for skill in self.self_sprite.skills:
        if not self._matches_skill_where(skill, mutation.skill_where):
            continue
        if mutation.stat == "tag":
            skill.tags.add(mutation.value) if mutation.value else skill.tags.discard(mutation.value)
        elif mutation.stat in _SKILL_MOD_STATS:
            skill._modifiers[mutation.stat] = skill._modifiers.get(mutation.stat, 0) + mutation.value
```

#### 3. `backend/vm/journal.py` — 新增 `SkillApplyMutation`

```python
@dataclass(frozen=True)
class SkillApplyMutation:
    target: str           # "sprite_self" | "sprite_opp"
    stat: str             # "tag" | "power" | "energy_cost" | ...
    value: float | str    # numeric or tag name
    skill_where: dict     # filter conditions
    metadata: dict        # name, source, etc.
```

---

### 扩展 #22: `mark action: "dispel_any"` + `devotion name: "random"` — 任意印记驱散 & 随机奉献

**需求**：表达"回合结束时驱散敌方1层印记，且驱散后己方队伍获得1次随机奉献"（回合末驱散奉献）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "回合末驱散奉献",
   "scope": "persistent",
   "when": {"cond": "turn_end"},
   "then": [
     {"op": "mark", "target": "sprite_opp", "action": "dispel_any", "delta": 1, "target_team": "opp"},
     {"op": "mod", "target": "team_own", "stat": "devotion", "name": "random", "value": 1}
   ]}
]}
```

#### 22a. `mark action: "dispel_any"` — 驱散任意印记

`dispel` 要求精确 `name` 匹配；`dispel_any` 不要求 name，驱散目标队伍任意一个有层数的印记。

**代码修改**（1 个文件，~2 行）：

##### `backend/engine/replayer.py` — `_apply_mark_change()` dispel 分支

```python
# 找到：
for mark in all_marks:
    if mark.name == m.name and mark.stacks > 0:

# 改为：
for mark in all_marks:
    if (m.action == "dispel_any" or mark.name == m.name) and mark.stacks > 0:
```

`delta` 控制驱散层数（默认 1）。

#### 22b. `devotion name: "random"` — 随机奉献

`mod stat: "devotion"` 已产生 `ModifierInjection`；`name: "random"` 时引擎从精灵可获得的奉献池中随机选取。

**代码修改**（1 个文件，~3 行）：

##### `backend/engine/replayer.py` — `_apply_modifier()` devotion 分支

```python
# devotion 注册时，若 name == "random"：
if m.stat == "devotion" and m.name == "random":
    pool = self.self_sprite.available_devotions  # 精灵可获得的奉献类型列表
    if pool:
        devotion_name = random.choice(pool)
    else:
        return ""  # 无可获得奉献
    # 以选中的 devotion_name 注册奉献层数
```

**语义**：
- `dispel_any`：按遍历顺序驱散第一个匹配的印记，非匹配全部。若需驱散多个印记，用多个 `mark` op
- `devotion name: "random"`：精灵需有可用奉献池（`sprite.available_devotions`），否则静默跳过
- 两个操作按 then 数组顺序执行：先驱散再奉献```

---

### 扩展 #23: `unlimited_abnormal` — 异常层数免除上限

**需求**：表达"萌化层数不受限制"（萌化无限）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "萌化无限",
   "scope": "once",
   "when": {"cond": "sprite_entered", "of": "sprite_self"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "unlimited_abnormal", "name": "萌化",
      "scope": "battlefield"}
   ]}
]}
```

**新增 flag stat**：

| 字段 | 说明 |
|------|------|
| stat | `unlimited_abnormal` |
| 类型 | flag（`_FLAG_STATS`） |
| 参数 | `name`: 目标异常名 |
| 作用 | 持有该 modifier 的精灵，被施加 `name` 异常时跳过层数上限 |
| scope | `battlefield` — 离场自动清除，对应"在场时"语义 |

**代码修改清单**（2 个文件，~6 行）：

#### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增条目

```python
_FLAG_STATS = frozenset({
    ...
    "unlimited_abnormal",  # 新增：指定异常层数不设上限（name=异常名）
})
```

#### `backend/sim/sprite.py` — `add_effect()` 上限检查旁路（~5 行）

```python
# add_effect() 中，当异常层数合并时：
# 在 existing.stacks += effect.stacks 之前或之后：
# 1. 检查 sprite 是否有 unlimited_abnormal modifier 且 name 匹配
# 2. 若有 → 跳过 max_stacks 裁剪
# 3. 若无 → current = min(current, max_stacks)

# 伪代码：
max_stacks = _ABNORMAL_MAX.get(effect.name, 9)  # 将来引入的上限表
unlimited = any(
    m.stat == "unlimited_abnormal" and m.name == effect.name
    for m in sprite.get_modifiers()
)
if not unlimited:
    existing.stacks = min(existing.stacks, max_stacks)
```

**设计要点**：
- 当前代码（`sprite.py:212`）**没有 `max_stacks` 限制**，异常层数可无限叠加。此扩展为将来引入层数上限机制预留豁免能力
- `immune` 跳过施加，`unlimited_abnormal` 跳过上限 — 两个 flag 正交
- `unlimited_abnormal` 不限制层数上限值，仅标记为"豁免"。上限值由引擎配置决定
- `name` 匹配异常名，支持多个条目覆盖不同异常（如 `stat: "unlimited_abnormal", name: "萌化"`, `stat: "unlimited_abnormal", name: "中毒"`）```

---

### 扩展 #24: `force_switch` + `observer_owner` — 备战精灵强制替换上场

**需求**：表达"回合结束时若场上己方精灵能量=0，自己立即替换之"（零能替换）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "零能替换",
   "scope": "persistent",
   "when": {"cond": "turn_end"},
   "then": [
     {"op": "when",
      "cond": {"cond": "compare", "q": "energy", "of": "sprite_self", "op": "eq", "value": 0},
      "then": [
        {"op": "force_switch", "target": "sprite_self", "replacement": "@observer_owner"}
      ]}
   ]}
]}
```

#### 24a. `force_switch` opcode — 强制替换上场

| 字段 | 类型 | 说明 |
|------|------|------|
| `op` | `"force_switch"` | 操作码 |
| `target` | `str` | 被替换下场的精灵（通常 `"sprite_self"` = 场上队友） |
| `replacement` | `str` | 替换上场的精灵引用（`"@observer_owner"` = Observer 所属精灵） |

**语义**：强制将 `target` 从场上换下，`replacement` 上场。不同于 `escape`（光是撤退），`force_switch` 是完整换人流程（退场触发 + 入场触发 + 技能/能量重置等）。

**代码修改**（1 个文件，~3 行）：

##### `backend/vm/ir_skill.py` — 新增 dataclass

```python
@dataclass(frozen=True)
class ForceSwitchOp:
    target: str          # 被换下的精灵
    replacement: str     # 换上的精灵（支持 "@observer_owner"）
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

##### `backend/vm/journal.py` — 新增 Mutation

```python
@dataclass
class ForceSwitch:
    target: str
    replacement: str
```

##### `backend/engine/replayer.py` — 路由

```python
def _apply_force_switch(self, m: ForceSwitch) -> str:
    old = self._target_sprite(m.target)
    new = self._resolve_reference(m.replacement)  # "@observer_owner" → observer owner sprite
    return f"{old.name} 被换下 → {new.name} 上场"
```

#### 24b. `observer_owner` — Observer 所属精灵引用

**需求**：`CountOp` 产生的 Observer 注册在全局 registry，但 `then` 块 effect 需要知道自己属于哪个精灵（即 Trait 持有者）。`@observer_owner` 是一个**运行时引用**，在 observer 触发时由 replayer 解析为对应的 `Sprite` 对象。

**代码修改**（3 个文件，~8 行）：

##### `backend/engine/observer.py` — Observer 新增字段

```python
@dataclass
class Observer:
    cond: dict
    then: list[dict]
    scope: str = "persistent"
    name: str = ""
    source: str = ""
    listen: frozenset = field(default_factory=frozenset)
    owner_sprite_id: int | None = None  # 新增：所属精灵的 id()
```

##### `backend/engine/battle.py` — `register_counter()` 记录 owner

```python
def register_counter(self, mutation: CounterRegister, owner_sprite=None) -> None:
    ...
    self.registry.register(Observer(
        ...
        owner_sprite_id=id(owner_sprite) if owner_sprite else None,  # 新增
    ))
```

##### `backend/engine/replayer.py` — `_target_sprite()` 新增解析

```python
# _target_sprite() 中：
if target == "observer_owner":
    # 从当前正在执行的 observer 上下文获取 owner sprite
    return self._observer_owner_sprite
```

`_observer_owner_sprite` 在 `_fire_post_event` 中设置为当前正在执行的 observer 的 owner。

#### 24c. 流程总览

```
1. battle.py: fire_trigger("turn_end") → _fire_post_event
2. _fire_post_event 遍历 registry._observers
3. 找到 "零能替换" Observer（owner_sprite_id = 备战精灵的 id）
4. 设置 replayer._observer_owner_sprite = sprite_by_id(owner_sprite_id)
5. eval_one(ctx, observer.cond) → turn_end → True
6. process_effects(ctx, observer.then) → [WhenBlock(cond=energy==0, then=[ForceSwitch])]
7. WhenBlock 条件满足 → ForceSwitch(target="sprite_self", replacement="@observer_owner")
8. replayer.replay → _apply_force_switch:
   - target sprite_self = 场上精灵（回合末 ctx 的 self）
   - replacement = replayer._observer_owner_sprite = 备战席的自己
9. 场上精灵换下，备战席的自己上场
```

**设计要点**：
- `@observer_owner` 前缀 `@` 表示运行时引用（与 `=@path` 公式字符串的 `@` 统一），区别于固定 target 字符串
- `observer_owner` 是通用基础设施：后续所有"自己替换之"、"自己获得 XX"类备战席 trait 都依赖它
- Observer 存储 `owner_sprite_id` 而非 `Sprite` 对象，避免循环引用```

---

### 扩展 #25: `damage_restraint` — 造成克制伤害后触发

**需求**：表达"造成克制伤害后，获得攻防速+20%并回复2能量"（克制增幅）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "克制增幅",
   "scope": "persistent",
   "when": {"cond": "damage_restraint"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "atk", "mode": "add", "steps": 2},
     {"op": "mod", "target": "sprite_self", "stat": "def", "mode": "add", "steps": 2},
     {"op": "mod", "target": "sprite_self", "stat": "speed", "mode": "add", "steps": 2},
     {"op": "mod", "target": "sprite_self", "stat": "energy", "value": 2}
   ]}
]}
```

**新增条件**：

| 字段 | 说明 |
|------|------|
| 条件名 | `damage_restraint` |
| 触发点 | `post_damage` |
| 参数 | 无 |
| eval | `ctx.element_advantage >= 2.0` |

**新增 Ctx 字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `element_advantage` | `float` | 当前技能攻击元素 vs 防御方元素的克制系数（0.5=抵抗, 1.0=正常, 2.0=克制） |

在 `build_ctx()` 时根据攻击技能元素和防御方精灵元素查克制表预填。

**代码修改清单**（4 个文件，~15 行）：

#### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# Ctx dataclass 新增：
element_advantage: float = 1.0  # 元素克制系数

# ADDRESS_MAP 新增：
("sprite_self", "element_advantage"): "element_advantage",
```

#### `backend/vm/cond.py` — 新增条件

```python
# A. CONDITION_TRIGGERS — 新增：
"damage_restraint": frozenset({"post_damage"}),

# B. COND_EVAL — 新增：
"damage_restraint": lambda ctx, cond: ctx.element_advantage >= 2.0,
```

#### `backend/engine/snapshot.py` — 填充 element_advantage（~8 行）

```python
# build_ctx() 中，在已知 self_skill.element 和 opp_sprite.elements 后：
_TYPE_CHART = {
    '火': {'草': 2.0, '冰': 2.0, '机械': 2.0, '虫': 2.0, '水': 0.5, '地': 0.5, '龙': 0.5},
    # ... (复制自 backend/sim/resolver.py)
}

def _get_element_advantage(atk_element: str, def_elements: list[str]) -> float:
    mult = 1.0
    for de in def_elements:
        mult *= _TYPE_CHART.get(atk_element, {}).get(de, 1.0)
    return mult
```

**设计要点**：
- `element_advantage` 在 ctx 构建时预计算（攻击元素和防御元素在此时已知），不依赖伤害计算
- `post_damage` 已存在于引擎触发点表，只需在 `cond.py` 建立 `damage_restraint → post_damage` 映射
- 克制判定阈值 `2.0`（标准克制系数），双属性抵抗（0.5×0.5=0.25）不会误触发
- `steps: 2` = +20%（1 step = 10%），`mode: "add"` 确保多次克制叠加
- 能量用 `value: 2`（非 `steps`），走 `_SPECIAL_STATS` → `EnergyChange(delta=2)` → `gain_energy(2)`
- `force_switch` 首次引入 `replacement` 字段，后续可扩展为 `"sprite_N"`、`"bench[0]"` 等引用格式```

---

### 扩展 #26: `compare` 条件 Observer 支持 + `transient` 修饰符

**需求**：表达"攻击时若敌方血脉是首领血脉，威力+100%"（首领压制）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "首领压制",
   "scope": "permanent",
   "when": {"cond": "compare", "q": "bloodline", "of": "sprite_opp", "op": "eq", "value": "首领血脉"},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "power_mult", "value": 2.0, "mode": "set", "transient": true}
   ]}
]}
```

**依赖**：扩展 #14 `bloodline` 查询（`bloodline_opp` 已有 ADDRESS_MAP 和 Ctx 字段）。

#### 26a. `compare` 条件加入 `CONDITION_TRIGGERS`

**问题**：`compare` 在 `COND_EVAL` 中存在但不在 `CONDITION_TRIGGERS` 中。`infer_triggers()` 查找 `compare` 时返回空集，导致以 `compare` 为条件的 Observer 永不触发。

**代码修改**（1 个文件，+1 行）：

##### `backend/vm/cond.py` — CONDITION_TRIGGERS

```python
CONDITION_TRIGGERS: dict[str, frozenset[str]] = {
    ...
    "compare":          frozenset({"pre_calc"}),  # 新增
}
```

`pre_calc` 是最合适的触发点：此时 ctx 全部字段已填充（包括 `bloodline_opp`），注入的 modifier 进入 journal → `collect_modifiers` 扫描 → `adjust_damage` 应用到当前伤害。

#### 26b. `transient` — 瞬态修饰符（仅当次攻击生效）

**问题**：`ModifierInjection` 总是持久化到 sprite/modifiers。下次 `build_ctx` 读取已存储的值，与当前注入的 journal 值双重计数：

```
Turn 1: ctx.power_mult=1.0, journal → 2.0, adjust → 100×2.0 = 200 ✓
Turn 2: ctx.power_mult=2.0 (持久化), op_hit → 200, journal → 2.0, adjust → 200×2.0=400 ✗
```

**方案**：新增 `transient` 标志。当 `true` 时：
- `collect_modifiers` 仍从 journal 扫描（影响当前伤害 ✓）
- replayer `_apply_modifier` **跳过存储**（`sprite._modifiers` / `skill._modifiers` 均不写入）

**代码修改**（2 个文件，~5 行）：

##### `backend/engine/replayer.py` — `_apply_modifier()` 跳过存储

```python
# _apply_modifier() 中，在写入 target_mods 之前：
if getattr(m, 'transient', False):
    # 只影响 collect_modifiers / adjust_damage，不持久存储
    return ""  # 或生成简短的 verbose log
```

`collect_modifiers` 在 `_apply_modifier` 之前运行（`apply_modifiers_to_journal` 在 `replay` 之前），所以 `transient` 修饰符已生效于当前伤害，跳过存储不影响正确性。

##### `backend/vm/ir_skill.py` — ModOp 新增字段

```python
@dataclass(frozen=True)
class ModOp:
    ...
    transient: bool = False  # 新增：仅当前回合生效，不持久存储
```

##### `backend/vm/journal.py` — ModifierInjection 新增字段

```python
@dataclass
class ModifierInjection:
    ...
    transient: bool = False  # 新增
```

**设计要点**：
- `transient` 是通用机制：任何 `pre_calc` 条件触发的修饰符（血脉检测、能量阈值、天气检测等）都应使用 `transient: true`，避免双重计数
- 持久修饰符（如应对成功后永久+20%威力）不使用 `transient`，正常持久化
- `mode: "set"` + `transient: true`：每次攻击重新判定条件，重新注入 → 条件不满足时不注入，威力恢复正常
- `bloodline` 查询已由扩展 #14 覆盖，`compare` 支持字符串 `eq` 比较（`compare_op` 使用 Python `==`）```

---

### 扩展 #27: `mark_bonus` — 虚拟印记层数加成

**需求**：表达"使用攻击技能时，敌方每层冻结视为1层额外星陨印记"（冻结即星陨）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "冻结即星陨",
   "scope": "permanent",
   "when": {"cond": "compare", "q": "skill_type", "of": "sprite_self", "op": "in", "value": ["物攻", "魔攻"]},
   "then": [
     {"op": "mod", "target": "sprite_opp", "stat": "mark_bonus", "name": "星陨",
      "value": {"q": "abnormal_stacks", "of": "sprite_opp", "name": "冻结"},
      "mode": "add", "transient": true}
   ]}
]}
```

**新增 stat**：

| 字段 | 说明 |
|------|------|
| stat | `mark_bonus` |
| 类型 | 特殊（需独立处理，非 `_SKILL_MOD_STATS` 亦非 `_FLAG_STATS`） |
| name | 目标印记名（如 `"星陨"`） |
| 语义 | 计算印记效果时，此值加到实际印记层数上：`effective = stacks + sum(mark_bonus values)` |
| scope | 跟随 modifier scope（默认 battlefield） |
| transient | 建议 `true`，避免跨回合持久化 |

**引擎改动**（`snapshot.py`，~5 行）：

当前 `mark_mult` 计算仅读取实际印记层数：
```python
# snapshot.py 当前逻辑：
for mark_name, stacks in mark_stacks_own.items():
    base_per_stack = _MARK_BASE.get(mark_name, 0.1)
    mark_mult = 1.0 + base_per_stack * stacks
```

修改后聚合 `mark_bonus` modifiers：
```python
# 收集 mark_bonus（虚拟层数加成）
mark_bonus: dict[str, float] = {}
for m in ss._modifiers.get("mark_bonus", []):
    mark_bonus[m.name] = mark_bonus.get(m.name, 0) + m.value

for mark_name, stacks in mark_stacks_own.items():
    base_per_stack = _MARK_BASE.get(mark_name, 0.1)
    effective = stacks + mark_bonus.get(mark_name, 0)
    mark_mult = 1.0 + base_per_stack * effective
```

**代码修改清单**（2 个文件，~8 行）：

##### `backend/engine/replayer.py` — `_METADATA_SKILL_LEVEL` 或存储逻辑

`mark_bonus` 需要像其他 modifier stat 一样被识别和存储。在 modifier dict 处理中新增键：
```python
# replayer.py _apply_modifier — 确保 mark_bonus 能被存储到 sprite._modifiers
# mark_bonus 的 value 是动态解析后的数值（如 3），存储为 float
if m.stat == "mark_bonus":
    # 按 name 分组存储：sprite._modifiers["mark_bonus"] = [ModifierInjection, ...]
    pass  # 走现有 generic modifier 存储路径
```

##### `backend/engine/snapshot.py` — 聚合 mark_bonus（见上，~5 行）

**设计要点**：
- `mark_bonus` 不直接设置印记层数，而是在计算 `mark_mult` 时作为加数 — 不影响印记本身的操作（驱散、偷取、转换等仍只看实际层数）
- `name` 指定目标印记，支持多个条目覆盖不同印记（如 `mark_bonus name: "星陨"`, `mark_bonus name: "破甲"`）
- `transient: true` 确保每回合重新从冻结层数计算，冻结被驱散后加成自动消失
- `in` 操作符（`["物攻", "魔攻"]`）`compare_op` 已支持，无需修改
- `abnormal_stacks` 查询已存在（`_NAMED_DICT_QUERIES` + ADDRESS_MAP），无需修改```

---

### 扩展 #28: `effective_*` — `skill_where` 按有效值过滤

**需求**：表达"全技能能耗-cnt后，有效能耗为0的技能威力+30"（水系节能增幅）。`skill_where` 当前只检查技能基础属性，不包含 `skill._modifiers` 的累加值，导致"先降耗再筛选"的顺序依赖无法表达。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "水系计数",
   "scope": "persistent",
   "when": {"cond": "sprite_acted", "element": "水"},
   "then": [
     {"op": "team_counter_write", "target": "own", "key": "water_skill_count", "delta": 1}
   ]},

  {"op": "count",
   "name": "水系节能增幅",
   "scope": "once",
   "when": {"cond": "sprite_entered", "of": "sprite_self"},
   "then": [
     {"op": "mod", "target": "skill_self_all", "stat": "energy_cost",
      "value": {"q": "team_counter", "of": "team_own", "name": "water_skill_count", "scale": -1},
      "mode": "add"},
     {"op": "mod", "target": "skill_self_all", "stat": "power",
      "value": 30, "mode": "add",
      "skill_where": {"effective_energy_cost": {"op": "eq", "value": 0}}}
   ]}
]}
```

**新增过滤字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `effective_energy_cost` | `{"op": ..., "value": N}` | 有效能耗 = 基础值 + `skill._modifiers["energy_cost"]` |
| `effective_power` | `{"op": ..., "value": N}` | 有效威力 = 基础值 + `skill._modifiers["power"]` |

`effective_*` 在 `eval_skill_where()` 中计算 `base + modifiers_aggregate`，然后与条件比较。与 `energy_cost`（仅基础值）互为补充。

**代码修改**（1 个文件，~8 行）：

##### `backend/engine/modifiers.py` — `eval_skill_where()` 新增

```python
# eval_skill_where() 中，在现有分支后追加：
if "effective_energy_cost" in skill_where:
    base = skill.get("energy_cost", 0)
    mods = skill.get("_modifiers", {})
    effective = base + mods.get("energy_cost", 0)
    cond = skill_where["effective_energy_cost"]
    if not compare_op(effective, cond["op"], cond["value"]):
        return False

if "effective_power" in skill_where:
    base = skill.get("power", 0)
    mods = skill.get("_modifiers", {})
    effective = base + mods.get("power", 0)
    cond = skill_where["effective_power"]
    if not compare_op(effective, cond["op"], cond["value"]):
        return False
```

**设计要点**：
- `effective_*` 计算 `base + sum(modifiers)`，与 `_apply_skill_all` 的累加逻辑一致
- `then` 数组按顺序执行：第 1 个 mod 写入 `skill._modifiers["energy_cost"]` → 第 2 个 mod 的 `effective_energy_cost` 查询读取到更新后的值
- `compare_op` 复用作比较（`eq`/`lt`/`lte`/`gt`/`gte` 等均支持）
- 后续可扩展到 `effective_speed`、`effective_priority` 等任意 `_SKILL_MOD_STATS` ```

---

### 扩展 #29: `abnormal` op `duration` — 限时异常自动过期

**需求**：表达"使用状态技能后，敌方获得聒噪效果（攻击技能能耗+3）持续3回合"（聒噪）。

**IR 表示**：
```json
{"effects": [
  {"op": "count",
   "name": "聒噪施加",
   "scope": "persistent",
   "when": {"cond": "sprite_acted", "skill_type": "状态"},
   "then": [
     {"op": "abnormal", "target": "sprite_opp", "name": "聒噪", "stacks": 1, "duration": 3}
   ]},

  {"op": "count",
   "name": "聒噪效果",
   "scope": "permanent",
   "when": {"cond": "and", "conditions": [
     {"cond": "compare", "q": "abnormal_stacks", "of": "sprite_self", "name": "聒噪", "op": "gte", "value": 1},
     {"cond": "compare", "q": "skill_type", "of": "sprite_self", "op": "in", "value": ["物攻", "魔攻"]}
   ]},
   "then": [
     {"op": "mod", "target": "sprite_self", "stat": "energy_cost", "value": 3, "mode": "add", "transient": true}
   ]}
]}
```

**新增字段**：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `duration` | `int` | `0` | 持续回合数。`0` = 无限期（持久），`>0` = 回合末递减，归零自动驱散 |

**代码修改清单**（3 个文件，~8 行）：

##### `backend/vm/ir_skill.py` — AbnormalOp 新增字段

```python
@dataclass(frozen=True)
class AbnormalOp:
    ...
    duration: int = 0  # 新增：持续回合数，0=持久
```

##### `backend/vm/journal.py` — AbnormalChange 新增字段

```python
@dataclass
class AbnormalChange:
    ...
    duration: int = 0  # 新增：传递给 StatusEffect.remaining_turns
```

##### `backend/sim/sprite.py` — StatusEffect + tick 逻辑

```python
# StatusEffect 新增字段：
remaining_turns: int = 0  # 0 = 持久

# tick 时递减：
def tick_turn(self):
    if self.remaining_turns > 0:
        self.remaining_turns -= 1
        return self.remaining_turns == 0  # True = 该回合自动驱散
```

引擎在回合末 tick 所有异常，`remaining_turns` 减到 0 时自动 `remove_effect`。

**设计要点**：
- `duration` 与 `stacks` 独立：叠加层数不重置回合计数；重新施加（同名已有）时可选择刷新或保留剩余回合（引擎默认刷新）
- **聒噪效果** Observer 用 `and` 组合两个 `compare`：`abnormal_stacks["聒噪"] >= 1` AND `skill_type in ["物攻","魔攻"]` → 攻击技能能耗 +3
- `and` 条件 `eval_one` + `infer_triggers` 均已支持（递归 union 子条件触发点 → `pre_calc`）
- 敌方没有聒噪时条件不满足 → `transient` 修饰符不注入 → 能耗正常
- 两个 Observer 分离关注点：施加 vs 效果，聒噪的机械效果独立定义

---

### 扩展 #30: `inherit_effects` 新增 `inherit_stat_effects` — 动态继承增益/减益

**Trait**：离场后，自己的增益和减益会被更换入场的精灵继承

**IR**：
```json
{"op": "inherit_effects", "target": "ally_new", "inherit_stat_effects": true}
```

**新增字段**：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `inherit_stat_effects` | `bool` | `false` | 是否复制离场精灵的动态属性效果（增益/减益）到入场精灵 |

**代码修改清单**（2 个文件，~6 行）：

##### `backend/vm/ir_skill.py` — InheritEffects 新增字段

```python
@dataclass(frozen=True)
class InheritEffects:
    ...
    inherit_stat_effects: bool = False  # 新增：复制动态 stat 效果
```

##### `backend/engine/replayer.py` — `_consume_pending_effects()` 新增分支

```python
# _consume_pending_effects() 中：
if op.inherit_stat_effects:
    for eff in leaving_sprite.effects:
        if eff.is_stat and eff.steps != 0:
            incoming_sprite.add_effect(copy(eff))
```

**设计要点**：
- `inherit_stat_effects` 与原有 `effects` 字段互补：`effects` 是硬编码的静态效果列表，`inherit_stat_effects` 是动态复制离场精灵当前的全部属性变化
- 复制范围：`is_stat=True` 且 `steps != 0` 的效果（含增益和减益）
- 与 Extension #8（`sprite_left` → `post_leave` 触发点）配合使用：Observer 在精灵离场时触发，`inherit_effects` 将效果挂到 `pending_effects`，新精灵入场时消费
- `via_pending: true` 时通过 battle.pending_effects 路由（跨回合传递）；`via_pending: false` 时直接写入（入场瞬间完成）

---

### 扩展 #31: `post_charge` 触发点 — 进入蓄力时触发

**Trait**：每次进入蓄力状态，获得全技能能耗永久-1

**IR**：
```json
{"op": "count", "name": "蓄力节能", "scope": "persistent",
 "when": {"cond": "charged", "of": "sprite_self"},
 "listen": ["post_charge"],
 "then": [
   {"op": "mod", "stat": "energy_cost", "target": "skill_off_0", "mode": "add", "value": -1, "scope": "persistent"}
 ]}
```

**扩展内容**：新增 `post_charge` 触发点，在 sprite 进入蓄力状态时触发。与已有 `charged` 条件不同——`charged` 检查是否处于蓄力中（状态检查，触于 `post_skill`/`turn_end`），`post_charge` 是状态变迁事件（进入瞬间）。

**代码修改清单**（2 个文件，~4 行）：

##### `backend/vm/cond.py` — CONDITION_TRIGGERS 新增 `post_charge`

```python
# charged 的 CONDITION_TRIGGERS 增加 post_charge：
"charged": frozenset({"post_skill", "turn_end", "post_charge"}),
```

##### `backend/engine/replayer.py` — `_apply_charge()` 末尾 fire trigger

```python
def _apply_charge(self, m: Charge) -> str:
    sprite = self._target_sprite(m.target)
    from backend.sim.sprite import StatusEffect
    sprite.add_effect(StatusEffect(
        name="charging", category="state", scope="persistent", source="skill",
    ))
    # 新增：fire post_charge trigger
    self.registry and self.registry.fire("post_charge", ...)
    return f"{sprite.name} 开始蓄力"
```

**设计要点**：
- `post_charge` 是事件触发点（进入蓄力瞬间），不是状态条件（蓄力中）
- 已有 `charged` 条件在 ctx 中映射为 `ctx.charged_self`（检查 charging 状态效果是否存在），`post_charge` 触发时 sprite 已进入 charging 状态，`charged` 条件自然满足
- `infer_triggers(["post_charge"])` 适用：`charged` 条件在 COND_EVAL 中已映射到 `ctx.charged_self`，无需新增条件

---

### 扩展 #32: Ctx 新增 `species_elements` — 精灵自身系别

**Trait**：使用非本系技能时威力+50%

**IR**：
```json
{"op": "count", "name": "非本系强化", "scope": "persistent",
 "when": {"cond": "compare", "q": "element", "of": "skill_off_0", "op": "not_in",
          "value": {"q": "species_elements", "of": "sprite_self"}},
 "then": [
   {"op": "mod", "stat": "power_mult", "target": "skill_off_0", "value": 1.5, "transient": true}
 ]}
```

**新增内容**：Ctx 新增 `species_elements_self` / `species_elements_opp` 字段，记录精灵自身系别（区别于 `skill_elements` 是携带技能的元素集合）。

**代码修改清单**（2 个文件，~6 行）：

##### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# Ctx 新增字段：
species_elements_self: frozenset = frozenset()  # sprite.species.elements()
species_elements_opp: frozenset = frozenset()

# ADDRESS_MAP 新增：
("sprite_self", "species_elements"):  "species_elements_self",
("sprite_opp", "species_elements"):  "species_elements_opp",
```

##### `backend/engine/snapshot.py` — `build_ctx()` 读取精灵系别

```python
species_elements_self = frozenset(ss.species.elements())
species_elements_opp = frozenset(os.species.elements())
```

**设计要点**：
- `species_elements` vs `skill_elements`：前者是精灵种族系别（1-2个），后者是携带技能的元素集合（最多n个），两者含义不同不可混用
- `not_in` 操作符 `compare_op` 已支持（检查 `a not in b`）
- `transient: true`（#26b）：每次触发时注入 `power_mult=1.5`，仅当前技能生效，不持久存储避免堆叠
- 触发点为 `pre_calc`（#26a `compare` 条件映射），在伤害计算前注入修饰符

---

### 扩展 #33: `post_charge_release` 触发点 — 蓄力释放时触发

**Trait**：蓄力时可使用任一携带技能，且获得双防+100%

**IR**：
```json
{"op": "count", "name": "蓄力强化-进入", "scope": "persistent",
 "when": {"cond": "charged", "of": "sprite_self"},
 "listen": ["post_charge"],
 "then": [
   {"op": "mod", "stat": "def", "target": "sprite_self", "mode": "add", "steps": 10, "source": "蓄力强化"},
   {"op": "mod", "stat": "sp_def", "target": "sprite_self", "mode": "add", "steps": 10, "source": "蓄力强化"},
   {"op": "mod", "stat": "usable_while_charging", "target": "sprite_self", "value": true, "source": "蓄力强化"}
 ]}
```

退出时用另一个 Observer 清除（见 #34）。

**扩展内容**：新增 `post_charge_release` 触发点，在 sprite 蓄力结束（释放技能/被打断）时触发。与 `post_charge`（#31）配对 — 进入和退出蓄力各有一个触发点。

**代码修改清单**（2 个文件，~4 行）：

##### `backend/vm/cond.py` — 新增条件 + 触发点映射

```python
# CONDITION_TRIGGERS 新增：
"charge_released": frozenset({"post_charge_release"}),

# COND_EVAL 新增：
"charge_released": lambda ctx, cond: ctx.charge_released_self,
```

##### `backend/engine/replayer.py` — 蓄力结束时 fire trigger

```python
# _apply_charge_release() 或等价位置：
self.registry and self.registry.fire("post_charge_release", ...)
```

**设计要点**：
- `post_charge`（进入）+ `post_charge_release`（退出）构成完整蓄力生命周期
- 退出 Observer 用 `dispel source: "蓄力强化"` 一键清除所有由进入 Observer 施加的效果（#15 `source` 字段）

---

### 扩展 #34: `usable_while_charging` — 运行时蓄力中可用技能 flag

**Trait**：蓄力时可使用任一携带技能

**扩展内容**：`_FLAG_STATS` 新增 `"usable_while_charging"`，引擎在检查技能可用性时读取此 flag。与 CompiledSkill 编译期字段 `usable_while_charging` 不同 — flag 版是运行时效果，可由 Observer 动态施加/清除。

**代码修改清单**（2 个文件，~3 行）：

##### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增

```python
_FLAG_STATS = frozenset({
    ...
    "usable_while_charging",  # 新增
})
```

##### `backend/sim/battle.py` — 技能可用性检查读取 flag

```python
# 判断技能是否在蓄力中可用时：
if sprite.has_flag("usable_while_charging"):
    return True  # 蓄力中可用
```

**设计要点**：
- 与 #33 配合使用：进入蓄力时 Observer 施加此 flag，退出蓄力时 `dispel source: "蓄力强化"` 清除
- 区别于 CompiledSkill.usable_while_charging（编译期固化），flag 版支持动态效果
- `source: "蓄力强化"` 确保 Observer 清除时只移除此特性的效果，不影响其他来源

---

### 扩展 #35: `skill_count_by_element` — 按元素统计携带技能数量

**Trait**：每携带1个毒系技能进入战斗，水系技能使敌方获得1层中毒

**IR**：
```json
{"op": "count", "name": "水系中毒", "scope": "persistent",
 "when": {"cond": "skill_use", "element": "水"},
 "then": [
   {"op": "abnormal", "target": "sprite_opp", "name": "中毒",
    "stacks": {"q": "skill_count_by_element", "of": "sprite_self", "name": "毒"}}
 ]}
```

**扩展内容**：Ctx 新增 `skill_count_by_element_self` / `skill_count_by_element_opp`，按技能元素统计携带数量。区别于 `skill_elements`（只记有无）和 `zero_cost_skill_count_self`（全技能计数不分元素）。

**代码修改清单**（4 个文件，~10 行）：

##### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# Ctx 新增：
skill_count_by_element_self: dict[str, int] = field(default_factory=dict)
skill_count_by_element_opp: dict[str, int] = field(default_factory=dict)

# ADDRESS_MAP 新增：
("sprite_self", "skill_count_by_element"): "skill_count_by_element_self",
("sprite_opp", "skill_count_by_element"): "skill_count_by_element_opp",
```

##### `backend/vm/resolve.py` — `_NAMED_DICT_QUERIES` 新增

```python
_NAMED_DICT_QUERIES = frozenset({
    "counter_value", "abnormal_stacks", "devotion", "skill_count", "team_counter",
    "skill_count_by_element",  # 新增
})
```

##### `backend/engine/snapshot.py` — 遍历技能计数

```python
skill_count_by_element_self = {}
for sk in (ss.skills or []):
    el = getattr(sk, 'element', '')
    if el:
        skill_count_by_element_self[el] = skill_count_by_element_self.get(el, 0) + 1
```

##### `backend/vm/ops/abnormal.py` — dict 路径 `stacks` 支持 query 解析

```python
# 修改前：delta = effect["stacks"]  # 裸值，query dict 报错
# 修改后：
if "stacks" in effect:
    stacks = effect["stacks"]
    delta = resolve(ctx, stacks) if isinstance(stacks, dict) else stacks
```

**设计要点**：
- `skill_count_by_element` vs `skill_elements`：前者是 `{"毒": 2, "水": 1}` 计数，后者是 `{"毒", "水"}` 集合；计数需求无法用集合满足
- 单个 Observer 即可表达：水系技能触发时，实时读取毒系技能数量作为中毒层数
- `abnormal` dict 路径的 `stacks` 字段原本不支持 query dict，修复后与 typed 路径的 `value` 行为一致

---

### 扩展 #36: `abnormal_tick_invert` — 异常 tick 方向逆转

**Trait**：在场时，所有灼烧的衰减变为增长

**IR**：
```json
{"op": "count", "name": "灼烧逆转-进入", "scope": "persistent",
 "when": {"cond": "sprite_entered", "of": "sprite_self"},
 "then": [
   {"op": "mod", "stat": "abnormal_tick_invert", "name": "灼烧",
    "target": "battlefield", "value": true, "source": "灼烧逆转"}
 ]}
```

退出时用 `sprite_left` + `dispel source: "灼烧逆转"` 清除（略）。

**扩展内容**：`_FLAG_STATS` 新增 `"abnormal_tick_invert"`，运行时标记指定异常的 tick 方向反转。引擎在 tick 周期递减/递增 stack 时检查此 flag。

**代码修改清单**（2 个文件，~6 行）：

##### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增

```python
_FLAG_STATS = frozenset({
    ...
    "abnormal_tick_invert",  # 新增
})
```

##### `backend/engine/replayer.py` — tick 逻辑读取 flag

```python
# tick 递减逻辑中：
invert = sprite.has_flag("abnormal_tick_invert", name=abnormal_name)
if invert:
    stacks_delta = +1   # 增长
else:
    stacks_delta = -1   # 衰减（默认）
```

**设计要点**：
- `name: "灼烧"` 限定只逆转灼烧；若省略 name 则对全场所有异常生效
- 进入/退出 Observer 配对：入场设 flag（`source: "灼烧逆转"`），离场 `dispel` 清除，依赖 #8（`sprite_left`）+ #15（`source` 过滤）
- flag 值存储在引擎侧（非 sprite._modifiers），通过 `has_flag()` 在 tick 时查询
- 不影响 tick 伤害等行为，只反转 stack 增减方向

---

### 扩展 #37: `mod stat: "devotion"` 无 `name` → 随机奉献

**Trait**：回合结束时偷取敌方1层印记，己方队伍获得1次随机奉献

**IR**：
```json
{"op": "count", "name": "回合末偷取", "scope": "persistent",
 "when": {"cond": "turn_end"},
 "then": [
   {"op": "mark", "target": "sprite_opp", "action": "steal", "target_team": "opp", "stacks": 1},
   {"op": "mod", "stat": "devotion", "target": "team_own", "value": 1}
]}
```

**扩展内容**：`mod stat: "devotion"` 当 `name` 字段为空时，引擎从可用奉献池中随机选取一个施加 +1。有 `name` 时行为不变（指定奉献名）。

**代码修改清单**（1 个文件，~4 行）：

##### `backend/engine/replayer.py` — `_apply_modifier` devotion 分支

```python
# devotion 分支中：
if name is None:
    # 无 name → 随机选取一个可用奉献
    name = random.choice(list(available_devotions))
```

**设计要点**：
- `mark action: "steal"` + 无 `name` → 偷敌方场上任意印记1层（#9 MarkOp action 字段扩展）
- `mark target_team: "opp"` 指定从敌方偷取
- `turn_end` 条件 + 触发点均已存在
- `mod stat: "devotion"` 的 `target: "team_own"` 写入己方队伍奉献计数器

---

### 扩展 #38: sprite 级 `mod` 的 `element` 过滤 — 按技能元素匹配 modifier

**Trait**：携带的电系技能获得迸发：能耗-2

**IR**：
```json
{"op": "count", "name": "电系迸发", "scope": "persistent",
 "when": {"cond": "sprite_entered", "of": "sprite_self"},
 "then": [
   {"op": "mod", "stat": "energy_cost", "target": "sprite_self", "mode": "add", "value": -2, "element": "电"}
]}
```

**扩展内容**：`mod` 的 `element` 字段在 sprite 级 modifier 上的消费逻辑。当前 `_apply_modifier` 已能将 `element` 存入，但 `build_ctx` 计算每个技能的 `energy_cost` 时不读取此过滤条件。

**代码修改清单**（2 个文件，~8 行）：

##### `backend/engine/snapshot.py` — 按 element 过滤 modifiers

```python
# 计算 skill energy_cost 时：
base_cost = sk.energy_cost
for mod in sprite._modifiers_for_stat("energy_cost"):
    if mod.element is None or mod.element == sk.element:
        base_cost += mod.value  # mode="add"
```

##### `backend/engine/replayer.py` — 保留 element 元数据存储

```python
# _apply_modifier: 确保 element 随 modifier 一起存储
# (当前 _metadata 已提取 element，补齐存储路径即可)
```

**设计要点**：
- `element: "电"` + `target: "sprite_self"` = 精灵上所有电系技能能耗-2
- 与 #28 `effective_energy_cost` 协作：此 mod 先减能耗，`effective_energy_cost` 后过滤
- `element` 过滤适用于所有技能级 stat（`energy_cost`、`power`、`priority` 等），不限于能耗
- `if_type: "电"` 同义，二者选一即可

---

### 扩展 #39: `counter_write` op — 公式/query 值写入引擎计数器

**Trait**：若使用技能能耗高于敌方，回合末敌方失去能耗之差的能量

**IR**（两个 Observer 协作）：
```json
{"op": "count", "name": "能耗差记录", "scope": "persistent",
 "when": {"cond": "compare", "q": "energy_cost", "of": "skill_off_0", "op": "gt",
          "value": {"q": "energy_total", "of": "skill_opp_current"}},
 "then": [
   {"op": "counter_write", "key": "能耗差",
    "value": "=@skill.energy_cost - @opponent_skill.energy_cost"}
 ]}
```
```json
{"op": "count", "name": "能耗差惩罚", "scope": "persistent",
 "when": {"cond": "turn_end"},
 "then": [
   {"op": "when",
    "cond": {"cond": "compare", "q": "counter_value", "of": "skill_off_0", "name": "能耗差", "op": "gt", "value": 0},
    "then": [
      {"op": "mod", "stat": "energy", "target": "sprite_opp", "mode": "add",
       "value": {"q": "counter_value", "of": "skill_off_0", "name": "能耗差"}, "negative": true},
      {"op": "counter_write", "key": "能耗差", "value": 0}
    ]}
 ]}
```

**扩展内容**：新增 `counter_write` op，将公式值或 query 值写入引擎计数器。区别于 `team_counter_write`（队伍级、只写 ±delta），`counter_write` 支持任意值赋值和公式计算。

**新 op 字段**：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `key` | `str` | `""` | 计数器名 |
| `value` | IRValue | — | 写入值，支持公式 `=@...` 或 query dict |
| `mode` | `str` | `"set"` | `"set"` 覆盖 / `"add"` 累加 |

**代码修改清单**（4 个文件，~14 行）：

##### `backend/vm/ir_skill.py` — 新增 dataclass + union

```python
@dataclass(frozen=True)
class CounterWrite:
    key: str = ""
    value: IRValue | None = None
    mode: str = "set"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

# SkillIROp union 新增 CounterWrite
```

##### `backend/vm/journal.py` — 新增 Mutation

```python
@dataclass
class CounterWriteMutation:
    key: str
    value: int | float
    mode: str = "set"
```

##### `backend/vm/ops/counter_write.py` — 新建 handler

```python
def op_counter_write(ctx: Ctx, effect) -> list[Mutation]:
    key = effect["key"] if isinstance(effect, dict) else effect.key
    raw = resolve(ctx, effect["value"]) if isinstance(effect, dict) else resolve(ctx, effect.value)
    mode = ...
    return [CounterWriteMutation(key=key, value=int(raw), mode=mode)]
```

##### `backend/engine/replayer.py` — `_apply_counter_write`

```python
def _apply_counter_write(self, m: CounterWriteMutation) -> str:
    if m.mode == "add":
        self._engine._counter_values[m.key] = self._engine._counter_values.get(m.key, 0) + int(m.value)
    else:
        self._engine._counter_values[m.key] = int(m.value)
    return ""
```

##### `backend/vm/resolve.py` — `_FORMULA_PATH_MAP` 补字段

```python
"opponent_skill.energy_cost": "energy_cost_opp",
```

**设计要点**：
- 两个 Observer 协作：第一个在技能使用时写入能耗差到计数器，第二个在回合末读取并扣除敌方能量，然后清零
- `negative: true` 将正值转为负值，实现扣除能量（`op_mod` 已支持）
- `counter_value` query 通过 `_NAMED_DICT_QUERIES` + ADDRESS_MAP 读取 `ctx.counter_values["能耗差"]`
- 区别于 `team_counter_write`：后者只支持 ±delta 增量且写队伍级；`counter_write` 支持公式赋值和任意值写入

---

### 扩展 #40: Ctx 新增 `bloodline` — 精灵血脉字段

**Trait**：根据自己的血脉，入场时获得不同效果（17 种血脉各有不同增益/减益）

**IR**（摘录恶系+武系两分支，其余 15 个分支结构相同）：
```json
{"op": "count", "name": "血脉入场", "scope": "persistent",
 "when": {"cond": "sprite_entered", "of": "sprite_self"},
 "then": [
   {"op": "when", "cond": {"cond": "compare", "q": "bloodline", "of": "sprite_self", "op": "eq", "value": "恶"},
    "then": [{"op": "mod", "stat": "life_drain", "target": "sprite_self", "value": 0.5, "scope": "persistent"}]},
   {"op": "when", "cond": {"cond": "compare", "q": "bloodline", "of": "sprite_self", "op": "eq", "value": "武"},
    "then": [{"op": "mod", "stat": "atk", "target": "sprite_self", "mode": "add", "steps": 8}]}
   // ... 15 more branches (光/普/翼/电/草/水/萌/地/虫/龙/幽/火/冰/毒/幻)
 ]}
```

**扩展内容**：Ctx + ADDRESS_MAP 新增 `bloodline_self` / `bloodline_opp`。#26 IR 示例中已使用 `bloodline` query，补齐 Ctx 实现即可。

**代码修改清单**（2 个文件，~4 行）：

##### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# Ctx 新增：
bloodline_self: str = ""
bloodline_opp: str = ""

# ADDRESS_MAP 新增：
("sprite_self", "bloodline"): "bloodline_self",
("sprite_opp", "bloodline"): "bloodline_opp",
```

##### `backend/engine/snapshot.py` — 从 species 读取血脉

```python
bloodline_self = ss.species.bloodline
bloodline_opp = os.species.bloodline
```

**设计要点**：
- 17 分支用 `when` 块表达，每个分支 `cond` 为 `compare bloodline eq "X"`
- `sprite_entered` 触发时只匹配一条分支，其余 `when` 跳过
- `bloodline` 是精灵物种固有属性（`species.bloodline`），不会在对局中变化
- #26 扩展中 IR 已前置使用 `bloodline` 作为 query，此扩展补齐其 Ctx 实现

---

### 扩展 #41: `post_heal` 触发点 — 治疗时触发 + 治疗增量追踪

**Trait**：获得能量或生命时，会将等量的能量或生命随机分配给场下的精灵（与 #42 配合）

**IR**：
```json
{"op": "count", "name": "能量分享", "scope": "persistent",
 "when": {"cond": "on_energy_changed", "of": "sprite_self"},
 "then": [
   {"op": "when",
    "cond": {"cond": "compare", "q": "energy_delta", "of": "sprite_self", "op": "gt", "value": 0},
    "then": [
      {"op": "mod", "stat": "energy", "target": "sprite_bench", "mode": "add",
       "value": {"q": "energy_delta", "of": "sprite_self"}}
    ]}
 ]}
```
治疗同理（`on_heal` → `heal_delta` → `mod hp`），略。

**扩展内容**：新增 `post_heal` 触发点 + `on_heal` 条件 + `heal_delta_self`/`heal_delta_opp` Ctx 字段。同时补齐 `energy_delta_self` 的 ADDRESS_MAP。

**代码修改清单**（3 个文件，~8 行）：

##### `backend/vm/cond.py` — 新增条件 + 触发

```python
# CONDITION_TRIGGERS:
"on_heal": frozenset({"post_heal"}),
"on_energy_changed": frozenset({"post_energy_change"}),  # 补：已有 COND_EVAL 但缺此映射

# COND_EVAL 新增：
"on_heal": lambda ctx, cond: ctx.heal_delta_self > 0,
```

##### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# 新增字段：
heal_delta_self: int = 0
heal_delta_opp: int = 0

# ADDRESS_MAP 补：
("sprite_self", "energy_delta"): "energy_delta_self",
("sprite_self", "heal_delta"): "heal_delta_self",
("sprite_opp", "heal_delta"): "heal_delta_opp",
```

##### `backend/engine/battle.py` — `_fire_mutation_events` 新增 Heal

```python
# 在 mutation 扫描中新增：
elif isinstance(m, Heal):
    trigger = "post_heal"
```

**设计要点**：
- `energy_delta_self` 已有字段但缺 ADDRESS_MAP 映射 — 一并补齐
- `heal_delta` 由 replayer 在 `_apply_heal` 后设置到 ctx（类似 `energy_delta` 在 `_apply_energy_change` 后）
- 两个 Observer 协作：能量分享 + 生命分享，条件检查 delta > 0 避免负值传递

---

### 扩展 #42: `sprite_bench` 目标 — 随机场下精灵

**扩展内容**：新增目标字符串 `"sprite_bench"`，引擎解析为从己方板凳精灵中随机选取一只。

**代码修改清单**（1 个文件，~6 行）：

##### `backend/engine/replayer.py` — `_target_sprite` 新增

```python
def _target_sprite(self, target: str) -> Sprite:
    if target == "sprite_bench":
        bench = self._battle.get_bench(self.team)
        if bench:
            import random
            return random.choice(bench)
        return self.self  # fallback: no bench → target self
    if target in ("sprite_self", "self", "team_own", "skill_off_0"):
        return self.self
    return self.opp
```

**设计要点**：
- 随机选取：每次 `_target_sprite("sprite_bench")` 调用时随机，同一 Observer 的多个 `then` 效果可能分配到不同板凳精灵
- 无板凳时 fallback 到 self（无害空操作）
- 与 #41 配合：能量/治疗 Observer 通过此目标将增量分配给板凳

---

### 扩展 #43: `InheritEffects.effects` — 硬编码效果传递给入场精灵

**Trait**：离场后，更换入场的精灵获得双攻+20%且免疫灼烧

**IR**：
```json
{"op": "count", "name": "离场传承", "scope": "persistent",
 "when": {"cond": "sprite_left", "of": "sprite_self"},
 "then": [
   {"op": "inherit_effects", "target": "ally_new", "via_pending": true,
    "effects": [
      {"op": "mod", "stat": "atk", "target": "sprite_self", "mode": "add", "steps": 2},
      {"op": "mod", "stat": "sp_atk", "target": "sprite_self", "mode": "add", "steps": 2},
      {"op": "mod", "stat": "immune", "name": "灼烧", "target": "sprite_self", "value": true}
    ]}
 ]}
```

**扩展内容**：`InheritEffects` 新增 `effects` 字段，离场时将硬编码效果施加到入场精灵。与 #30 `inherit_stat_effects`（动态拷贝已有效果）互补。

**新字段**：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `effects` | `tuple[SkillIROp, ...]` | `()` | 入场时施加到目标精灵的固定效果列表 |

**代码修改清单**（3 个文件，~10 行）：

##### `backend/vm/ir_skill.py` — InheritEffects 新增字段

```python
@dataclass(frozen=True)
class InheritEffects:
    ...
    effects: tuple[SkillIROp, ...] = ()  # 新增
```

##### `backend/vm/journal.py` — InheritEffectsMutation 新增字段

```python
class InheritEffectsMutation:
    ...
    effects: tuple = ()  # 新增
```

##### `backend/engine/replayer.py` — `_apply_inherit_effects_mutation` 新增分支

```python
# 在现有逻辑后追加：
if m.effects:
    from backend.vm.executor import process_effects
    # 构建简化的 Ctx 以 process_effects 处理 effects
    extra = process_effects(ctx, list(m.effects))
    if m.via_pending:
        self._battle.pending_effects.setdefault(self.team, []).extend(extra)
    else:
        for mut in extra:
            self._replay_one(mut)  # 或直接 add_effect
```

**设计要点**：
- `target: "ally_new"` replayer 已支持（`m.target_key != "enemy_new"` → self = 己方入场精灵）
- `via_pending: true` 挂到 `battle.pending_effects`，新精灵入场时消费
- `effects` 中的 `target` 解析相对于入场精灵（`sprite_self` = 入场精灵自己）
- 与 #30 互补：#30 `inherit_stat_effects` 复制离场精灵动态效果，#43 `effects` 施加固定效果
- `immune name: "灼烧"` 需引擎在灼烧 apply 时检查 named immunity flag，属引擎补漏非 IR 扩展

---

### 扩展 #44: `_FLAG_STATS` 新增 `"swift"` — 迅捷标记

**Trait**：1号位技能获得迅捷和传动1

**IR**：
```json
{"op": "count", "name": "1号位迅捷传动", "scope": "permanent",
 "when": {"cond": "skill_at", "position": 0},
 "then": [
   {"op": "mod", "target": "skill_off_0", "stat": "drive", "value": 1},
   {"op": "mod", "target": "skill_off_0", "stat": "swift", "value": 1}
 ]}
```

**扩展内容**：`_FLAG_STATS` 新增 `"swift"`，表示技能获得迅捷属性。`drive`（传动）已在 `_FLAG_STATS`，此扩展补齐 `swift`。

**代码修改清单**（1 个文件，~1 行）：

##### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增

```python
_FLAG_STATS = frozenset({
    ...
    "swift",  # 新增：迅捷标记
})
```

**设计要点**：
- `skill_at position: 0`（0-indexed = 1号位）→ `post_skill` 触发
- `drive` 已在 `_FLAG_STATS`，`向心力.json` 已有同模式 IR（传动1+威力+30）
- `mode: "set"`（默认）+ `scope: "permanent"` → 每次覆盖不堆叠
- 引擎在"释放所有迅捷技能"（疾风连袭）等机制中检查 `skill.has_flag("swift")`

---

### 扩展 #45: `post_energy_change` 触发时 ctx 含 `energy_delta` 和 `energy_changed_of`

**Trait**：每回复1能量，同时回复5%生命

**IR**：
```json
{"op": "count", "name": "能量回血", "scope": "persistent",
 "when": {"cond": "on_energy_changed", "of": "sprite_self"},
 "then": [
   {"op": "when",
    "cond": {"cond": "compare", "q": "energy_delta", "of": "sprite_self", "op": "gt", "value": 0},
    "then": [
      {"op": "mod", "stat": "hp", "target": "sprite_self",
       "value": "=@delta * @self.max_hp * 0.05"}
    ]}
 ]}
```

**扩展内容**：`_fire_mutation_events` 在触发 `post_energy_change` 前更新 `ctx.energy_delta_self` 和 `ctx.event.energy_changed_of`。#41 已添加条件定义和 ADDRESS_MAP，此扩展补齐 ctx 运行时填充——否则两字段始终为默认值 0/""，条件永不满足。

**代码修改清单**（1 个文件，~6 行）：

##### `backend/engine/battle.py` — `_fire_mutation_events` EnergyChange 分支

```python
# post_energy_change 触发前：
elif isinstance(m, EnergyChange):
    ctx.energy_delta_self = m.delta
    ctx.event.energy_changed_of = "sprite_self" if m.target in ("sprite_self",) else "sprite_opp"
    trigger = "post_energy_change"
```

**设计要点**：
- `on_energy_changed` + `energy_delta` query 定义已有（#41），此扩展补齐运行时 ctx 填充
- 内层 `when delta > 0` 仅能量增加时回血，避免能量减少时意外扣血
- 公式 `=@delta * @self.max_hp * 0.05`：`@delta` 映射 `energy_delta_self`（`_FORMULA_PATH_MAP` 已有 `"delta": "energy_delta_self"`）
- `ctx` 为可变 dataclass，直接设值即可；多 Observer 间相互独立（同一 trigger 内共享 ctx）

---

### 扩展 #46: `team_element_count` — 队伍精灵按系别计数

**Trait**：队伍中每有1只其他虫系精灵，入场时获得攻防速+15%

**IR**：
```json
{"op": "count", "name": "虫群之力", "scope": "persistent",
 "when": {"cond": "sprite_entered", "of": "sprite_self"},
 "then": [
   {"op": "mod", "target": "sprite_self", "stat": "atk",
    "steps": {"q": "element_count", "of": "team_own", "name": "虫", "scale": 1.5}},
   {"op": "mod", "target": "sprite_self", "stat": "def",
    "steps": {"q": "element_count", "of": "team_own", "name": "虫", "scale": 1.5}},
   {"op": "mod", "target": "sprite_self", "stat": "speed",
    "steps": {"q": "element_count", "of": "team_own", "name": "虫", "scale": 1.5}}
 ]}
```

**扩展内容**：新增 Ctx 字段 `team_element_count_own` / `team_element_count_opp`，统计己方/敌方队伍中**其他精灵**（不含当前 active）按系别的数量。配合 ADDRESS_MAP + `_NAMED_DICT_QUERIES`，通过 `{"q": "element_count", "of": "team_own", "name": "虫"}` 查询。

**代码修改清单**（3 个文件，~12 行）：

##### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# Ctx 新增：
team_element_count_own: dict[str, int] = field(default_factory=dict)  # {element: count}
team_element_count_opp: dict[str, int] = field(default_factory=dict)

# ADDRESS_MAP 新增：
("team_own", "element_count"): "team_element_count_own",
("team_opp", "element_count"): "team_element_count_opp",
```

##### `backend/vm/resolve.py` — `_NAMED_DICT_QUERIES` 新增

```python
_NAMED_DICT_QUERIES = frozenset({
    "counter_value", "abnormal_stacks", "devotion", "skill_count", "team_counter",
    "skill_count_by_element", "element_count",  # 新增
})
```

##### `backend/engine/snapshot.py` — `build_ctx` 遍历 bench 计数

```python
team_element_count_own = {}
for sprite in bench_own:  # bench_own = 己方板凳精灵列表（不含 active）
    for el in (sprite.species.elements() or []):
        team_element_count_own[el] = team_element_count_own.get(el, 0) + 1

team_element_count_opp = {}
for sprite in bench_opp:
    for el in (sprite.species.elements() or []):
        team_element_count_opp[el] = team_element_count_opp.get(el, 0) + 1
```

**设计要点**：
- 统计 bench（不含当前 active self/opp），天然满足"其他"语义
- `species.elements()` 返回 `list[str]`（来自 `SpeciesStats.attributes` 逗号分割），多系别精灵每个系别各计数一次
- `scale: 1.5` 使每只虫系 = 1.5 级 stat stage，int 截断后的行为：1只=1级(10%)，2只=3级(30%)，3只=4级(40%)
- `team_own` / `team_opp` 与 ADDRESS_MAP 中已有 `"team_own"` / `"team_opp"` of-type 一致
- 多 Observer 共享 ctx，同 trigger 内各 Observer 读取同一 `team_element_count_own` 快照

---

### 扩展 #47: `skill_element_count` + 公式路径 — 技能不重复系别计数

**Trait**：敌方每携带1种系别的技能，攻击威力+10%

**IR**：
```json
{"op": "count", "name": "多系压制", "scope": "persistent",
 "when": {"cond": "skill_use"},
 "then": [
   {"op": "mod", "target": "skill_off_0", "stat": "power_mult",
    "value": "=@enemy_skill_elements * 0.1 + 1.0"}
 ]}
```

**扩展内容**：新增 Ctx 字段 `skill_element_count_self` / `skill_element_count_opp`（int），存储精灵技能的不重复系别数。配合 `_FORMULA_PATH_MAP` 公式路径 `"enemy_skill_elements"` → 浮点算术求值。

**代码修改清单**（3 个文件，~5 行）：

##### `backend/vm/ctx.py` — 新增字段

```python
# Ctx 新增：
skill_element_count_self: int = 0  # distinct skill element types
skill_element_count_opp: int = 0
```

##### `backend/vm/resolve.py` — `_FORMULA_PATH_MAP` 新增

```python
"enemy_skill_elements": "skill_element_count_opp",
```

##### `backend/engine/snapshot.py` — 从已有 frozenset 取 len

```python
skill_element_count_self = len(skill_elements_self) if skill_elements_self else 0
skill_element_count_opp = len(skill_elements_opp) if skill_elements_opp else 0
# skill_elements_self/opp 已在 build_ctx 中计算（frozenset），直接 len()
```

**设计要点**：
- 走公式而非 query：`power_mult` 需要 `1.0 + count * 0.1`，query 的 `scale`/`offset` 是 int 截断管道（3×0.1=int(0.3)=0），公式 `=@e * 0.1 + 1.0` 保留浮点 → 1.3
- `skill_elements_opp` 已在 snapshot 计算（从对方技能列表提取所有 element 去重），此处只取 `len()`，零开销
- `target: "skill_off_0"` + `stat: "power_mult"` → 对进攻技能做乘法修正，默认 1.0 不变，敌方系别越多倍率越高
- `power_mult` 在 `collect_modifiers` 中始终走 `*= value`（`modifiers.py:51`），多个 power_mult 来源正确叠乘

---

### 扩展 #48: `stat_stage` query + `positive_changed_stat` — 单项 stat stage 查询与增益追踪

**Trait**：入场时复制敌方增益；在场时若敌方获得增益自己也会获得

**IR**（两个 Observer，第一个入场复制，第二个同步增益——5 项 stat 枚举仅展开 atk+def，其余同理）：
```json
{"op": "count", "name": "入场复制增益", "scope": "persistent",
 "when": {"cond": "sprite_entered", "of": "sprite_self"},
 "then": [
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "atk", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "atk", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "atk"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "def", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "def", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "def"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "sp_atk", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "sp_atk", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "sp_atk"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "sp_def", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "sp_def", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "sp_def"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "speed", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "speed", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "speed"}}]}
 ]}
```
```json
{"op": "count", "name": "同步增益", "scope": "persistent",
 "when": {"cond": "on_positive_changed", "of": "sprite_opp"},
 "then": [
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "atk", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "atk", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "atk"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "def", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "def", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "def"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "sp_atk", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "sp_atk", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "sp_atk"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "sp_def", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "sp_def", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "sp_def"}}]},
   {"op": "when", "cond": {"cond": "compare", "q": "stat_stage", "of": "sprite_opp", "name": "speed", "op": "gt", "value": 0},
    "then": [{"op": "mod", "stat": "speed", "target": "sprite_self", "steps": {"q": "stat_stage", "of": "sprite_opp", "name": "speed"}}]}
 ]}
```

**扩展内容**：两个子扩展合并。

**A. `stat_stage` query** — 单项 stat stage 查询

`stat_stages_self` / `stat_stages_opp` 是 `dict[str, int]`（如 `{"atk": 3, "speed": -1}`），但无 ADDRESS_MAP 和 `_NAMED_DICT_QUERIES` 支持按名查询单项值。

##### `backend/vm/ctx.py` — ADDRESS_MAP 新增

```python
("sprite_self", "stat_stage"): "stat_stages_self",
("sprite_opp", "stat_stage"): "stat_stages_opp",
```

##### `backend/vm/resolve.py` — `_NAMED_DICT_QUERIES` 新增

```python
_NAMED_DICT_QUERIES = frozenset({
    "counter_value", "abnormal_stacks", "devotion", "skill_count", "team_counter",
    "skill_count_by_element", "element_count", "stat_stage",  # 新增
})
```

**B. `positive_changed_stat`** — 追踪刚获得的增益属性名

##### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# Ctx 新增：
positive_changed_stat: str = ""    # which stat just gained (atk/def/sp_atk/sp_def/speed)

# positive_changed_of 属于 EventContext，见 ctx.py EventContext 定义

# ADDRESS_MAP 新增：
("sprite_self", "positive_changed_stat"): "positive_changed_stat",
("sprite_opp", "positive_changed_stat"): "positive_changed_stat",
```

##### `backend/engine/battle.py` — `_fire_mutation_events` StatChange 分支

```python
elif isinstance(m, StatChange):
    if getattr(m, 'is_positive', False):
        ctx.positive_changed_stat = m.stat
        ctx.event.positive_changed_of = "sprite_self" if m.target == "sprite_self" else "sprite_opp"
        trigger = "post_positive_change"
```

**代码修改清单汇总**（3 个文件，~10 行）：

| 文件 | A 改动 | B 改动 |
|---|---|---|
| `backend/vm/ctx.py` | +2 ADDRESS_MAP | +2 字段 + 2 ADDRESS_MAP |
| `backend/vm/resolve.py` | `_NAMED_DICT_QUERIES` +1 | — |
| `backend/engine/battle.py` | — | StatChange 分支 ~4行 |

**设计要点**：
- `compare stat_stage["atk"] > 0` 确保只复制正值（增益），敌方负面阶段被过滤
- `steps: {"q": "stat_stage", ...}` 直接读取敌方当前 stage 作为 steps，精确镜像
- Observer 2 每次敌方增益触发时**全量重设**（默认 mode="set" 覆盖），非增量追加。结果正确（最终值=敌方值），但可能产生冗余中间 StatChange
- `positive_changed_stat` 字段名与 ADDRESS_MAP query 名一致，`compare` 条件可直接用 `q: "positive_changed_stat"`
- `positive_changed_of` 区分己方/敌方增益，`on_positive_changed of: "sprite_opp"` 只响应敌方增益

---

### 扩展 #49: 公式路径 `player/opponent.marks[name=X].stacks` — 按名查询印记层数

**Trait**：敌方每有1层星陨印记，地系技能威力+15%

**IR**：
```json
{"op": "count", "name": "星陨地威", "scope": "persistent",
 "when": {"cond": "skill_use", "element": "地"},
 "then": [
   {"op": "mod", "target": "skill_off_0", "stat": "power_mult",
    "value": "=@opponent.marks[name=星陨].stacks * 0.15 + 1.0"}
 ]}
```

**扩展内容**：`_resolve_trait_ref` 新增 `player/opponent.marks[name=X].stacks` 正则分支，从 Ctx `mark_stacks_own` / `mark_stacks_opp` dict 按名查询印记层数。与已有的 `effects[name=X].stacks`（异常）、`counters[key]`（计数器）、`team_counters[key]`（队伍计数器）并列，补齐印记查询缺口。

**代码修改清单**（1 个文件，~6 行）：

##### `backend/vm/resolve.py` — `_resolve_trait_ref` 新增 mark 分支

```python
# player/opponent.marks[name=X].stacks
m = re.match(r'(player|opponent)\.marks\[name=([^\]]+)\]\.stacks', path)
if m:
    team, name = m.group(1), m.group(2)
    if team == "opponent":
        return ctx.mark_stacks_opp.get(name, 0)
    return ctx.mark_stacks_own.get(name, 0)
```

插入位置：在 `effects[name=X]` 分支后、`counters[key]` 分支前。

**设计要点**：
- `player` → 己方队伍、`opponent` → 敌方队伍，语义匹配 `mark_stacks_own` / `mark_stacks_opp`
- 公式求值保留浮点：`3 * 0.15 + 1.0 = 1.45`，避免 query `scale`/`offset` 管道的 int 截断
- `skill_use element: "地"` 限定只在地系技能使用时触发，`power_mult` 精确作用于当前技能
- `power_mult` 在 `collect_modifiers` 中始终 `*=` 累积（`modifiers.py:51`），多来源正确叠乘
- `mark_stacks_own` / `mark_stacks_opp` 已由 `build_ctx` 从 `globals.get_marks()` 填充，此处仅读取

---

### 扩展 #50: `InheritEffects.via_pending` 跨队伍推断

**Trait**：敌方精灵离场后，其增益和减益会被更换入场的精灵继承

**IR**：
```json
{"op": "count", "name": "敌方传承", "scope": "persistent",
 "when": {"cond": "sprite_left", "of": "sprite_opp"},
 "then": [
   {"op": "inherit_effects", "source": "target", "inherit_target": "enemy_new",
    "inherit_stat_effects": true, "via_pending": true}
 ]}
```

**扩展内容**：`_apply_inherit_effects_mutation` 中 `via_pending` 分支当前始终写入 `self.team`（己方 pending 队列）。当 source 是敌方精灵（`source_key != "self"`）且 target 是敌方新入精灵（`target_key == "enemy_new"`）时，应写入敌方队伍 pending 队列，敌方新精灵入场时消费。

**代码修改清单**（1 个文件，~3 行）：

##### `backend/engine/replayer.py` — `_apply_inherit_effects_mutation` via_pending 分支

```python
# 修改前：
if m.via_pending:
    self._battle.pending_effects.setdefault(self.team, [])
    self._battle.pending_effects[self.team].extend(inherited)

# 修改后：
if m.via_pending:
    # 推断 pending 归属队伍：敌方源+敌方目标 → 敌方队伍
    if m.source_key != "self" and m.target_key == "enemy_new":
        pending_team = "B" if self.team == "A" else "A"
    else:
        pending_team = self.team
    self._battle.pending_effects.setdefault(pending_team, [])
    self._battle.pending_effects[pending_team].extend(inherited)
```

**设计要点**：
- `sprite_left of: "sprite_opp"` 敌方离场触发；`source: "target"` → source_sprite = self.opp = 敌方离场精灵
- `inherit_stat_effects: true`（#30）→ 拷贝离场精灵所有 stat stage 效果（增益+减益双方向），与 #43 `effects`（固定效果）互补
- `inherit_target: "enemy_new"` 已在 replayer 中支持（非 "enemy_new" → self.self，是 → self.opp）
- 敌方新精灵入场时由 `consume_pending_modifiers` 消费 pending 队列，效果施加到新精灵
- 推断逻辑：`source_key != "self"` = 来源是敌方，`target_key == "enemy_new"` = 目标是敌方新精灵 → 归入敌方队伍 pending

---

### 扩展 #51: 迸发延长 — `_burst_extended_once` 引擎消费

**Trait**：技能的迸发效果延长1回合

**IR**：
```json
{"op": "count", "name": "迸发延长", "scope": "battlefield",
 "when": {"cond": "sprite_entered", "of": "sprite_self"},
 "then": [
   {"op": "counter_write", "key": "_burst_extended_once", "value": true}
 ]}
```

**扩展内容**：当前迸发机制为 `first_action=True` 时触发（入场后首次行动），行动后引擎直接设 `first_action=False`（`battle.py:467/598`）。引擎已预留 `_burst_extended_once` 计数器查询路径（`cond.py:533`），但缺少消费逻辑。此扩展补齐：首次行动后若计数器>0，清计数器而不清 `first_action`，使迸发窗口延长到第二次行动。

**代码修改清单**（1 个文件，~3 行）：

##### `backend/sim/battle.py` — 两处 `user.first_action = False` 前插入检查

```python
# 修改前：
user.first_action = False

# 修改后：
if self._vm_engine._counter_values.get("_burst_extended_once", 0) > 0:
    self._vm_engine._counter_values["_burst_extended_once"] = 0
else:
    user.first_action = False
```

两处位置：聚能分支（~L467）和技能后处理（~L598）。

**设计要点**：
- `counter_write value: true`（#39）→ `int(true)=1` 写入引擎计数器
- 首次行动：`first_action=True` → 迸发触发 → 引擎检查计数器=1 → 清计数器（设为 0），保留 `first_action=True`
- 第二次行动：`first_action=True`（保留）→ 迸发再次触发 → 计数器=0 → 正常执行 `first_action=False`
- `scope: "battlefield"`：离场再入场时重新注册 Observer，重新设置计数器 → 每次入场都获得 1 回合延长
- 与 #39 `counter_write` op 协作：IR 层无需新 opcode，引擎侧补齐消费即可

---

### 扩展 #52: `_FLAG_STATS` 新增 `"no_self_damage"` — 免疫自伤

**Trait**：能耗小于等于1的攻击技能，无法对自己造成伤害

**IR**：
```json
{"op": "count", "name": "低耗防护", "scope": "persistent",
 "when": {
   "cond": "and",
   "conditions": [
     {"cond": "or",
      "conditions": [
        {"cond": "skill_use", "skill_type": "物攻"},
        {"cond": "skill_use", "skill_type": "魔攻"}
      ]},
     {"cond": "compare", "q": "energy_cost", "of": "skill_off_0", "op": "lte", "value": 1}
   ]},
 "then": [
   {"op": "mod", "target": "skill_off_0", "stat": "no_self_damage", "value": true}
 ]}
```

**扩展内容**：`_FLAG_STATS` 新增 `"no_self_damage"` 标记。引擎 `_apply_damage` 在伤害目标为 `sprite_self` 时检查当前技能是否携带此 flag，若携带则跳过伤害。

**代码修改清单**（2 个文件，~4 行）：

##### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增

```python
_FLAG_STATS = frozenset({
    ...
    "no_self_damage",  # 新增：免疫自伤
})
```

##### `backend/engine/replayer.py` — `_apply_damage` 检查

```python
# 在 _apply_damage 开头：
if m.target == "sprite_self" and self._self_skill_has_flag("no_self_damage"):
    return ""
```

`_self_skill_has_flag` 检查当前执行技能（`self._self_skill` 或从 battle 上下文获取的 bs）的 modifiers/effects 中是否携带该 flag。

**设计要点**：
- `and` 外层：attack（物攻 `or` 魔攻）+ `energy_cost <= 1` 同时满足
- `skill_off_0` → `energy_cost_self`（ADDRESS_MAP 已有）
- `mod value: true` → `mode: "set"`（默认）不叠加，每次技能使用覆盖
- Observer 在 `post_skill` 触发，但 `_apply_damage` 在 replay 阶段执行；标记需在 damage 前生效。应走 `pre_calc` 触发（`infer_triggers` 需调整 skill_use → `pre_calc`），确保标记在 VM 执行前注入。若 `skill_use` 默认为 `post_skill`，改用 `pre_calc` 需要扩展——但 `skill_use` 已推断 `post_skill`，此处标记为技能级 modifier，注入时机在 `pre_calc`/`pre_modifier` 阶段更合适
- 备选：不用 Observer，直接用 sprite 级 `mod` 配合 `if_type` 过滤，入场时打上标记，引擎在技能执行时检查 sprite 上有无此标记。但范围覆盖不如技能级精确


### 扩展 #53: `_FLAG_STATS` 新增 `"tick_reduce"` — 回合结束效果触发次数-1

**Trait**：在场时，双方回合结束时效果触发次数-1

**IR**：
```json
{"op": "count", "name": "回合结束效果触发次数减少", "scope": "battlefield",
 "when": {"cond": "sprite_entered"},
 "then": [
   {"op": "mod", "target": "sprite_self", "stat": "tick_reduce", "value": 1}
 ]}
```

**扩展内容**：`_FLAG_STATS` 新增 `"tick_reduce"` 标记。引擎在回合结束处理异常 tick 时，检查战场上是否有精灵携带此 flag，若有则所有异常的回合结束触发次数各 -1（最低为 0）。

**代码修改清单**（2 个文件，~3 行）：

##### `backend/vm/ops/mod.py` — `_FLAG_STATS` 新增

```python
_FLAG_STATS = frozenset({
    ...
    "tick_reduce",  # 新增：回合结束效果触发次数-1
})
```

##### `backend/engine/replayer.py` — tick 处理检查 flag

```python
# 在回合结束 tick 循环中：
reduce = any(
    hasattr(s, 'modifiers') and any(
        m.stat == "tick_reduce" for m in s.modifiers if getattr(m, 'active', True)
    )
    for s in (self_sprite, opp_sprite)
)
effective_ticks = max(0, base_ticks - (1 if reduce else 0))
```

**设计要点**：
- `scope: "battlefield"` + `sprite_entered` 保证：在场时 flag 生效，离场时引擎清除 battlefield scope 的 modifier
- 这是战场光环——只要携带者在场上，双方都受影响
- 与 `extra_turn_end` 互为反义词，一个 +1 触发，一个 -1 触发
- `tick_reduce` 影响所有 tick 类型（中毒、灼烧等），统一减少，非 per-abnormal 多次减


### 扩展 #54: `elem_shared_skill_names` + `skill_where` 条件 `shared_with_elem`

**Trait**：对本精灵的技能，若其他翼系精灵携带相同技能，则获得迅捷。被敌方精灵击败时，自己额外损失1点魔力。

**IR**：
```json
{"effects": [
  {"op": "count", "name": "翼系共享技能迅捷", "scope": "battlefield",
   "when": {"cond": "sprite_entered"},
   "then": [
     {"op": "mod", "target": "skill_self_all", "stat": "swift", "value": true,
      "skill_where": {"cond": "shared_with_elem", "element": "翼"}}
   ]},
  {"op": "count", "name": "被击败额外损失魔力", "scope": "battlefield",
   "when": {"cond": "on_self_ko"},
   "then": [
     {"op": "lives_change", "target_team": "own", "delta": -1}
   ]}
]}
```

**扩展内容**（仅效果1，效果2 `lives_change` 已有）：

1. Ctx 新增 `elem_shared_skill_names: dict[str, frozenset]` — 按元素分组，存储 bench 中其他精灵的技能名集合（排除自身）
2. `eval_skill_where` 新增 `shared_with_elem` 条件 — 检查当前技能名是否在 `ctx.elem_shared_skill_names[element]` 中

**代码修改清单**（3 个文件，~15 行）：

##### `backend/vm/ctx.py` — 新增字段 + ADDRESS_MAP

```python
# Ctx 新增字段：
elem_shared_skill_names: dict = field(default_factory=dict)  # {"翼": frozenset({"升龙咆哮", ...}), ...}
```

##### `backend/engine/snapshot.py` — `build_ctx` 填充

```python
# 遍历 bench 精灵，按元素收集技能名（排除自身 active）
shared = {}
for sprite in bench_sprites:
    for elem in getattr(sprite.species, 'elements', []):
        if elem not in shared:
            shared[elem] = set()
        for skill in sprite.skills:
            shared[elem].add(skill.name)
ctx.elem_shared_skill_names = {k: frozenset(v) for k, v in shared.items()}
```

##### `backend/engine/modifiers.py` — `eval_skill_where` 新增

```python
# shared_with_elem 条件：
if cond.get("cond") == "shared_with_elem":
    element = cond["element"]
    shared_set = ctx.elem_shared_skill_names.get(element, frozenset())
    if skill_name not in shared_set:
        return False
```

`skill_self_all` target + `swift` flag 已在 #21 和 #44 中定义，此处复用。

**设计要点**：
- `scope: "battlefield"` + `sprite_entered` 保证入场时评估一次，在场期间持续生效
- `elem_shared_skill_names` 排除自身（`off_skill_self`），只统计 bench 中其他精灵
- `on_self_ko` + `lives_change` 效果2无需扩展，现有 IR 直接表示
- 若 bench 精灵同时有多系别（如翼+火），其技能名会同时出现在两个 key 下，语义正确

