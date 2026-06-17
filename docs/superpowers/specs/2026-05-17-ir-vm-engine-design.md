# 格斗小九 IR VM 引擎设计规格

## 概述

将现有原型引擎 (`scripts/sim/`) 的技能执行核心替换为基于 `data/SKILL_IR_RISC.md` 的纯函数 VM，然后围绕 VM 构建生产级引擎。

### 目标

1. **技能 VM** — 纯函数核心：`(Ctx, effects[]) → Journal[Mutation]`
2. **引擎包装器** — 拥有可变状态，调用 VM，应用 mutation 并触发被动
3. **被动系统** — 被动效果与技能共享相同的 IR 原语和条件评估

### 非目标

- 修改技能 JSON 格式（数据已经是 IR 格式）
- 替换外部系统 — AI 代理、Gradio UI、日志记录以及 `scripts/sim/` 的其他非效果部分保持不变

---

## 第 1 节：VM 核心架构

### 设计原则

VM 是一个**纯函数**。无副作用。无随机数。无对象引用。给定相同的 `(Ctx, effects[])`，它总是产生相同的 `Journal`。

```
输入：Ctx（回合快照）+ effects[]（排序后的效果树）
输出：Journal[Mutation] + 下次调用的更新计数器
```

### Mutation 类型

VM 只产生以下内容，不产生其他：

| Mutation | 字段 |
|----------|-------|
| `StatChange` | target, stat, steps, scope |
| `ModifierInjection` | target, stat, value, scope, mode |
| `Damage` | target, amount, element, type |
| `Heal` | target, amount |
| `EnergyChange` | target, delta |
| `MarkChange` | target_team, name, delta |
| `AbnormalChange` | target, name, delta |
| `WeatherSet` | weather, turns |
| `Dispel` | target, what, name?, limit?, type_limit? |
| `Steal` | from_target, what, name?, amount? |
| `Tick` | target, abnormal_name |
| `Double` | target, what, name? |
| `Charge` | target |
| `Escape` | target, inherit?, urgent?, then? |
| `Return` | target |
| `Lock` | target, turns |
| `Interrupt` | target |
| `Exchange` | target, what |
| `Reset` | target, stat |
| `Redirect` | target |
| `Replay` | from_, skill_filter |
| `Borrow` | from_skill |
| `CounterRegister` | name, cond, then, scope |

注意：`ModifierInjection` 是一个内部使用的 mutation，不会直接应用于精灵/队伍状态。引擎在伤害计算期间读取以收集 power_mult、damage_mult、damage_reduction 等修正器。

### Ctx（回合快照）

按照 IR 文档规范。所有查询表达式都针对此只读结构进行解析。为每个技能调用构建一个新的 Ctx — 而不是每个回合一次 — 因此技能 #2 能观察到技能 #1 的效果。

### resolve() — 值解析

按照 IR 文档规范。查询表达式 `{"q": "hp_ratio", "of": "sprite_opp"}` 通过 ADDRESS_MAP 映射到 Ctx 字段。字面量原样传递。

运行时支持：scale、per、offset、default。

### 条件评估

`COND_EVAL` 调度表，`and`/`or`/`not` 作为递归组合器。每个条件都是一个纯函数 `(ctx, cond) → bool`。添加新条件 = 添加一行注册到表中。

### 管道阶段与伤害流

效果声明 `feeds`（"我往 cost/power/mult 池子放东西"）或 `needs`（"我消费 result/counter/turn_end"）。引擎在 VM 执行前对效果进行排序：

```
feeds:cost → Gate（支付能量）→ feeds:power → 确定威力 → feeds:mult
  → VM 计算伤害 → 产生 Damage(amount) mutation → result 就绪
  → 默认效果 + needs:result
  → 反击 → counter 就绪 → needs:counter
  → 回合末 → needs:turn_end
```

**伤害公式在 VM 内部。** 在 `mult` 阶段之后，VM 已经收集了所有修正器（power_mult、damage_mult、damage_reduction 等来自 `ModifierInjection` mutation）。然后，VM 自己运行伤害公式（与原型相同的公式：`37/41 × atk/def × power_term × stab × type_mult × ...`）并产生 `Damage(target, amount)` mutation 带有最终计算值。引擎只需应用结果。

对于显式的 `hit` 操作码：VM 使用 hit 的 power/type/element 运行相同的伤害公式并产生一个 `Damage` mutation。

拓扑排序在加载时对每个技能预计算一次（效果之间没有改变执行顺序的战斗状态依赖）。

### Count 状态

Count 效果注册跨回合保持但在 VM 内管理的计数器。状态作为 `vm.execute()` 的第二个输出返回，并由引擎保存/恢复。

---

## 第 2 节：特性与被动效果

对 142 个特性描述的语义分析表明，特性分属两个正交维度：

### 维度 A：触发时机

| 时机 | 说明 | 典型条件 |
|------|------|----------|
| **计算前** | 在公式运行前注入修正器，影响当前计算 | `on_damage_taken`（偏振）、`have_skill_of`（不移） |
| **事件后** | 在动作完成后执行效果，影响后续状态 | `skill_use`（助燃）、`on_ko`（付给恶魔的赎价） |
| **回合边界** | 回合开始/结束、入场/离场时触发 | `turn_end`（养分内循环）、`sprite_entered`（全神贯注）、`opp_switched`（下黑手） |

### 维度 B：效果形式

| 形式 | IR 表达 | 示例 |
|------|---------|------|
| **持久修正器** | `mod` 无 `when`，或者 `mod` 条件始终为真 | 不移："无额外效果攻击技能威力+30%"，冰封："敌方全技能能耗+1" |
| **事件触发效果** | `count` + `when` → `then` | 助燃："使用火系技能后双攻+20%"，下黑手："敌方离场后换入精灵获得中毒" |

### 架构：两阶段集成

特性不改变 VM 的纯函数性质。引擎在两个阶段集成特性：

```
阶段 1 — 修正器收集（计算前）
  引擎遍历活跃特性，评估条件，收集该阶段相关的修正器
    → ModifierInjection mutation 列表
    → 输入到当前计算（威力确定、伤害公式等）

阶段 2 — 事件触发效果（事件后）
  引擎触发匹配条件的 count 观察者
    → VM 执行 then 效果 → Journal[Mutation]
    → 应用 mutations + 递归（深度限制）
```

关键洞察：**`on_damage_taken` 条件在计算前触发，而非计算后。** 当特性偏振注册 `count(on_damage_taken) → mod(damage_reduction)` 时，引擎在伤害公式运行**之前**触发该观察者，因此修正器能够及时被采集到。这不同于"受到伤害后"的语义——后者用 `on_ko` 或 HP 变化后的观察者来处理。

### 观察者模型（用于事件触发效果）

```python
@dataclass
class Observer:
    """持久的条件监听器。挂载在精灵上，跨回合存活直到 scope 清除。"""
    cond: dict          # 触发条件（共享 COND_EVAL 表）
    then: list[dict]    # 触发时执行的 IR 效果
    scope: str          # "battlefield" | "persistent" | "permanent"
    cooldown: int = 0   # 冷却（回合数），0 = 无冷却
```

观察者注册 = `count` 操作码。引擎在以下位置触发观察者：

| 触发点 | 触发条件 | 时期 |
|--------|---------|------|
| 伤害公式前 | `on_damage_taken` | 计算前 — 注入 damage_reduction 等 |
| 技能执行后 | `skill_use` | 事件后 — 获得 buff/层数 |
| 换宠结算后 | `opp_switched` / `self_switched` | 事件后 |
| 入场结算后 | `sprite_entered` | 事件后 |
| 异常 tick 后 | `on_abnormal_tick` | 事件后 |
| 异常层数变化后 | `on_abnormal_changed` / `on_abnormal_applied` | 事件后 |
| KO 结算后 | `on_ko` / `on_self_ko` | 事件后 |
| 能量变化后 | `on_energy_changed` | 事件后 |
| 增益变化后 | `on_positive_changed` | 事件后 |
| 技能能耗变化后 | `on_skills_energy_changed` | 事件后 |
| 回合结束时 | `turn_end` | 回合边界 |

### 与技能的完全统一

特性效果和技能效果共享：
- 同一套 IR 操作码（mod、abnormal、mark、dispel、steal 等）
- 同一个 `COND_EVAL` 条件调度表
- 同一个 `resolve()` 查询解析器
- 同一个 VM `execute()` 入口

添加新条件或新操作码会同时惠及技能和特性。没有重复代码路径。

### 递归与深度限制

观察者可能触发新的观察者（例如：受到伤害 → 回复 HP → HP 变化触发另一个观察者）。引擎维护触发深度计数器，超过限制（默认为 5）时终止并记录警告。

### 持久修正器收集

对于无条件的 `mod` 特性（如不移），引擎在构建 Ctx 时直接将其注入寄存器的对应字段，无需经过观察者机制。这降低了运行时开销——不需要的观察者不会被遍历。

---

## 第 3 节：引擎包装器

### 职责

引擎拥有所有可变状态。VM 永远看不到活动对象 — 引擎将它们快照到 Ctx 中，调用 VM，然后重放。

```
引擎拥有：
  teams[2]: list[Sprite]    marks: dict
  weather: str              全局效果: GlobalEffects
  counters: dict            turn: int
  observers: list[Observer] 日志: list[TurnRecord]
  trait_modifiers: list     特性持久修正器缓存
```

### 边界规则

> 如果你可以只用 `Ctx + effects[] → Journal[Mutation]` 为它编写单元测试，它就是 VM。如果测试需要一个 `Sprite` 或 `Battle`，它就是 Engine。

**VM 不负责处理的内容**（引擎负责处理）：
- 随机决策（随机奉献类型、随机目标）
- AI 选择（使用哪个技能、是否换宠）
- 精灵对象管理
- 日志 / 事件字符串
- 特性/被动分发（引擎触发观察者；特性共享 IR 操作码和条件表）

**引擎不负责处理的内容**（VM 负责处理）：
- 解析查询表达式
- 评估条件
- 计算伤害/治疗量（VM 通过带有计算值的 mutation 来产生这些）
- 效果排序

### 技能解析（每次技能一个快照）

```python
def resolve_skill(self, team, action) -> Journal:
    # 1. 将当前世界快照到 Ctx 中
    ctx = Ctx.snapshot(self_=self.active(team), opp=self.active(opponent), ...)
    
    # 2. 收集特性持久修正器（无条件 mod），注入 Ctx
    ctx = self.inject_trait_modifiers(ctx)
    
    # 3. 使用 feeds/needs 排序效果（在加载时预计算）
    sorted_effects = action.skill.sorted_effects
    
    # 4. 触发计算前观察者（on_damage_taken 等），收集修正器注入 effects
    pre_mutations = self.fire_pre_calc_observers(ctx)
    augmented_effects = sorted_effects + pre_mutations
    
    # 5. 纯 VM 执行 — (Ctx, effects[]) → Journal[Mutation]
    journal = vm.execute(ctx, augmented_effects)
    
    # 6. 重放 + 事件后观察者交织
    for mutation in journal:
        self.apply(mutation)
        self.fire_post_action_observers(ctx, mutation)  # skill_use、on_ko 等
    
    return journal
```

### 优先级和应对规则

这些是决定哪个技能何时发动的元规则，而不是效果如何解析。它们属于引擎，而不是 VM：
- 优先级比较（速度平局决胜、先手等级属性）
- 应对匹配（攻击↔应对攻击、防御↔应对防御、状态↔应对状态）
- 先手/后手排序

### 回合生命周期

```
回合开始
  ├─ 构建回合前 Ctx，注入特性修正器，触发回合开始观察者
  ├─ 传动系统
  └─ 延迟效果结算（ttl 递减）

选择
  ├─ 代理选择行动
  └─ 道具循环（使用道具 → 重新选择）

结算（按优先级排序）
  ├─ 对于每个技能（先手，然后是后手）：
  │   ├─ 构建每次技能的 Ctx 快照 + 注入特性修正器
  │   ├─ VM.execute() → Journal（含计算前观察者触发）
  │   ├─ 重放 Journal 并交织事件后观察者
  │   └─ 力竭中断检查
  └─ 应对钩子

回合结束
  ├─ 异常 tick（中毒/灼烧/寄生/冻结）→ 触发 on_abnormal_tick 观察者
  ├─ 天气 tick + 效果
  ├─ 印记 tick 效果
  ├─ 冷却递减
  ├─ 延迟效果结算（phase=end）
  ├─ 返场结算
  ├─ 力竭检查 + 强制换宠
  └─ 回合结束观察者（turn_end）
```

---

## 第 4 节：数据流

### 加载管道

```
磁盘上的 data/skills/*.json
    │
    ▼ （启动时一次性）
SkillLoader.load(path)
    ├─ validate()           ← 在加载时，而非战斗时捕获结构错误
    ├─ normalize()          ← 填充默认值；VM 永远不需要防御性 .get()
    ├─ pre_resolve_static() ← 内联仅依赖于技能定义的值
    ├─ pre_index_queries()  ← 构建 QueryRef 对象，O(1) 查找
    └─ pre_sort_effects()   ← 对 feeds/needs 进行拓扑排序（一次性）
    │
    ▼
SkillObject（内存中，不可变，在所有战斗中共享）
    │
    ▼ （每次战斗，每次技能调用）
Engine.resolve_skill()
    ├─ Ctx.snapshot(battle_state)   ← 每次技能调用时刷新
    └─ vm.execute(ctx, skill.sorted_effects)
```

### 预解析

**静态查询**（仅依赖于技能定义，而非战斗状态）在加载时解析：

```python
{"q": "power_base", "of": "skill_off_0"} → 80  # 从技能定义中内联
```

**动态查询**（依赖于 HP、能量、印记等）保持为查询对象并在运行时解析。

### 查询索引

每个查询在加载时预先索引到其 ADDRESS_MAP 键。在运行时，`resolve()` 执行 O(1) 字典查找，而不是线性搜索：

```python
@dataclass
class QueryRef:
    field: str            # Ctx 属性名，例如 "mark_count_opp"
    name: str | None      # 用于字典类型的寄存器
    scale: float = 1.0
    offset: int = 0
    per: float | None = None
```

### 特性加载管道

```
磁盘上的 data/traits/*.json
    │
    ▼ （启动时一次性）
TraitLoader.load(path)
    ├─ validate()              ← 验证 IR 结构
    ├─ normalize()             ← 填充默认值
    ├─ classify()              ← 分类：持久修正器（无条件 mod）vs 观察者（count + when）
    └─ pre_index_queries()     ← 与 SkillLoader 共享同一 ADDRESS_MAP
    │
    ▼
TraitObject（内存中，不可变，在所有战斗中共享）
    ├─ modifiers: list[Modifier]  ← 持久修正器，每次 Ctx 构建时注入
    └─ observers: list[Observer]  ← 事件观察者，引擎在事件触发时执行
```

分类发生在加载时：无条件 `mod` → 直接进入修正器列表；`count` + `when` → 注册为观察者。

### 缓存属性

由于 SkillObject 和 TraitObject 在加载后是不可变的：
- 全局共享：10,000 场同时进行的战斗都引用同一个"回旋踢"的 SkillObject 和"偏振"的 TraitObject
- 拓扑排序是预计算的 — 技能效果的执行顺序在不同战斗之间不会改变
- 特性分类是预计算的 — 无需每回合判断一个特性是修正器还是观察者
- 加载后零分配 — 没有解析，没有验证，只是执行

---

## 第 5 节：测试策略 + 迁移

### VM 测试（纯函数 — 易于测试）

没有模拟、没有设置、没有拆卸。每个测试：构造 Ctx，调用 execute，断言日志。

```python
def test_mod_steps():
    ctx = Ctx(atk_self=100)
    effects = [{"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 3}]
    journal = vm.execute(ctx, effects)
    assert journal == [StatChange("sprite_self", "atk", 3, "battlefield")]

def test_dynamic_power_by_marks():
    ctx = Ctx(mark_count_opp=5, power_self=80)
    effects = [{"op": "mod", "target": "skill_off_0", "stat": "energy_cost",
                "value": {"q": "mark_count", "of": "team_opp", "scale": -1, "name": "any"},
                "feeds": "cost"}]
    journal = vm.execute(ctx, effects)
    assert journal == [ModifierInjection("skill_off_0", "energy_cost", -5, "battlefield")]
```

**覆盖率策略**：每个操作码一个测试文件。每个文件测试该操作码的字面量路径、动态路径、边界情况以及与真实技能中出现的其他操作码的交互。

### 引擎测试（与原型进行集成和 golden-test）

```python
@pytest.mark.parametrize("skill_name", ALL_SKILL_NAMES)
def test_skill_matches_prototype(skill_name):
    ctx = standard_test_battle()
    proto_result = run_prototype(ctx, skill_name)
    new_result = run_new_engine(ctx, skill_name)
    assert new_result.damage == proto_result.damage
    assert new_result.hp_changes == proto_result.hp_changes
```

### 迁移顺序

**第 1 阶段：构建 VM 核心**（无引擎更改）
- Ctx、resolve()、COND_EVAL、ADDRESS_MAP
- 10 个操作码作为纯函数
- when、count（带状态）
- feeds/needs 拓扑排序
- 单元测试：100+ 个测试，无原型依赖

**第 2 阶段：对原型进行 Golden-test VM**
- 对于每个技能 JSON，在一致的战斗状态下运行两个引擎
- 修复差异直到 100% 匹配

**第 3 阶段：构建引擎包装器**
- 快照构造函数（Sprite + Battle → Ctx）
- 带有观察者交错的日志重放
- 回合生命周期（与原型结构相同）
- 针对原型的集成测试

**第 4 阶段：替换原型**
- 将 `scripts/sim/` 切换到使用新引擎
- 全面战斗回归（1000 场随机战斗）
- 删除旧的 effects.py、resolver.py 特殊调度

**第 5 阶段：特性系统**
- 实现观察者注册/触发引擎（基于 IR count 操作码）
- 实现特性修正器收集（加载时分类：持久修正器 vs 观察者）
- 将已重构的 28 个特性 JSON 接入引擎
- 对原型特性行为进行 Golden test
- 删除旧的被动调度基础设施

### 为什么按这个顺序

- 第 1 阶段完全独立 — 与 `scripts/sim/` 中的任何内容都没有耦合
- 第 2 阶段在 VM 触及真实精灵之前就捕获了所有语义错误
- 第 3 阶段重用了原型的战斗循环结构 — 相同的优先级规则、相同的应对匹配、相同的回合生命周期。只有效果执行核心发生变化
- 第 4 阶段是干净的切换 — `scripts/sim/` 的其余部分（AI 代理、Gradio UI、日志记录）完全不需要更改，因为引擎暴露了相同的接口

---

## 第 6 节：目录结构

```
scripts/
├── sim/                    # 现有原型（将被逐步淘汰）
│   └── ...
│
├── vm/                     # 新 IR VM 核心
│   ├── __init__.py
│   ├── ctx.py              # Ctx 数据类 + ADDRESS_MAP + snapshot()
│   ├── resolve.py          # resolve() 函数 + QueryRef
│   ├── ops/                # 每个操作码一个文件
│   │   ├── __init__.py
│   │   ├── mod.py
│   │   ├── mark.py
│   │   ├── abnormal.py
│   │   ├── weather.py
│   │   ├── dispel.py
│   │   ├── steal.py
│   │   ├── tick.py
│   │   ├── double.py
│   │   ├── charge.py
│   │   ├── escape.py
│   │   ├── hit.py
│   │   ├── exchange.py
│   │   ├── reset.py
│   │   ├── redirect.py
│   │   ├── replay.py
│   │   ├── borrow.py
│   │   ├── return_.py
│   │   ├── lock.py
│   │   ├── interrupt.py
│   │   └── count.py
│   ├── cond.py             # COND_EVAL 调度表
│   ├── sort.py             # feeds/needs 拓扑排序器
│   ├── journal.py          # Mutation 数据类
│   └── executor.py         # 主 VM execute() 入口点
│
├── engine/                 # 新引擎包装器
│   ├── __init__.py
│   ├── battle.py           # 回合生命周期、优先级、应对规则
│   ├── skill_loader.py     # 技能验证、规范化、预解析、预索引
│   ├── trait_loader.py     # 特性加载：分类为持久修正器/观察者
│   ├── replayer.py         # 日志重放 + 观察者交织
│   ├── observer.py         # Observer 数据类 + 注册/触发引擎
│   └── snapshot.py         # Sprite + Battle → Ctx 构造函数
│
└── tests/
    ├── vm/                 # VM 单元测试（每个操作码一个文件）
    │   ├── test_mod.py
    │   ├── test_mark.py
    │   ├── test_when.py
    │   ├── test_count.py
    │   ├── test_dynamic_queries.py
    │   └── ...
    ├── engine/             # 引擎集成测试
    │   ├── test_battle.py
    │   ├── test_observers.py
    │   ├── test_traits.py
    │   └── test_golden.py  # 针对原型的 ~150 技能 + 28 特性回归测试
    └── conftest.py         # 共享的 Ctx 和战斗装置
```

---

## 总结

1. **VM 是纯函数式的** — `(Ctx, effects[]) → Journal[Mutation]`。易于推理，易于测试。
2. **特性分两级集成** — 持久修正器在 Ctx 构建时注入，事件触发效果通过观察者在管道各阶段触发。特性与技能共享 IR 操作码和条件评估，没有重复基础设施。
3. **引擎是命令式的** — 拥有可变状态，调用 VM，应用 mutation，触发观察者。
4. **加载时预处理** — 验证、规范化、静态解析、查询索引、效果排序都在加载时完成。运行时快速且无分配。
5. **迁移有黄金测试支持** — 原型产生正确的结果。新引擎必须针对它进行回归测试，直到 100% 匹配。
6. **清理边界** — 如果你可以为它编写一个只接受 `(Ctx, effects[])` 的单元测试，它就是 VM。否则，它就是 Engine。
