# IR VM 引擎实现计划

基于 `docs/superpowers/specs/2026-05-17-ir-vm-engine-design.md`

---

## 架构总览

```
Layer 1: scripts/vm/     -- 纯函数 VM: (Ctx, effects[]) -> Journal[Mutation]
Layer 2: scripts/engine/ -- 命令式引擎包装器：拥有可变状态，调用 VM，应用 mutations
```

---

## Phase 1: 构建 VM 核心 (第1-2周)

零依赖 `scripts/sim/`。独立可测试。

### 构建序列

```
 1. journal.py          (无依赖，纯数据)
 2. ctx.py              (无依赖)
 3. resolve.py          (依赖 ctx)
 4. cond.py             (依赖 ctx, resolve)
 5. damage.py           (无依赖，纯数学)
 6. ops/mod.py          (依赖 resolve, journal)
 7. ops/mark.py         (依赖 resolve, journal)
 8. ops/abnormal.py     (依赖 resolve, journal)
 9. ops/weather.py      (依赖 journal)
10. ops/dispel.py       (依赖 journal)
11. ops/steal.py        (依赖 journal)
12. ops/tick.py         (依赖 journal)
13. ops/double.py       (依赖 journal)
14. ops/charge.py       (依赖 journal)
15. ops/hit.py          (依赖 resolve, damage, journal)
16. ops/escape.py       (依赖 resolve, journal)
17. ops/exchange.py     (依赖 journal)
18. ops/reset.py        (依赖 journal)
19. ops/redirect.py     (依赖 journal)
20. ops/replay.py       (依赖 journal)
21. ops/borrow.py       (依赖 journal)
22. ops/return_.py      (依赖 journal)
23. ops/lock.py         (依赖 journal)
24. ops/interrupt.py    (依赖 journal)
25. ops/count.py        (依赖 resolve, cond, journal)
26. ops/when.py         (依赖 cond，控制流工具)
27. ops/__init__.py     (依赖所有 ops)
28. sort.py             (无依赖)
29. executor.py         (依赖 ctx, cond, sort, ops)
30. __init__.py         (依赖以上全部)
```

### 关键设计决策

- **伤害公式**：SkillLoader 在加载时为攻击技能注入合成 `hit` effect，带有动态威力查询。VM 不需要知道"隐式伤害"——它只处理 effects 数组中的 `hit` opcode。
- **when 控制流**：不在 OP_TABLE 中注册。executor 内联处理条件分支和递归。
- **ModifierInjection**：内部使用的 mutation，引擎在威力/伤害阶段采集，不直接应用于精灵状态。

### 测试目标

每个操作码一个测试文件。120+ 单元测试。模式：
```python
ctx = Ctx(atk_self=100)
effects = [{"op": "mod", "target": "sprite_self", "stat": "atk", "steps": 3}]
result = vm.execute(ctx, effects)
assert result.journal == [StatChange("sprite_self", "atk", 3, "battlefield")]
```

---

## Phase 2: 对原型进行 Golden-test VM (第2-3周)

### 策略

对 `data/skills/*.json` 中每个技能：
1. 构建相同的原型战斗状态
2. 运行原型管道 → 获取结果
3. 从相同状态构建等价 Ctx → VM.execute()
4. 比较 damage、HP 变化、stat 变化、abnormal 应用

输出：`scripts/tests/engine/test_golden.py`，~150 参数化测试

### 关键挑战

- 大多数技能已是 IR 格式（`op`/`when.cond`），少量旧格式由 SkillLoader.normalize() 转换
- 伤害公式的微妙差异通过 golden test 发现和修正

---

## Phase 3: 构建引擎包装器 (第3-4周)

### 构建序列

```
1. skill_loader.py   (依赖 vm.sort, vm.resolve)
2. trait_loader.py   (依赖 vm)
3. observer.py       (依赖 vm.cond, vm.executor)
4. snapshot.py       (依赖 sim.sprite, sim.battle, vm.ctx)
5. replayer.py       (依赖 observer, vm.journal, sim.sprite)
6. battle.py         (依赖以上全部 + sim.battle)
```

### 新文件清单

```
scripts/vm/__init__.py
scripts/vm/ctx.py               # Ctx dataclass (76字段) + ADDRESS_MAP (~46条目)
scripts/vm/resolve.py           # resolve() + QueryRef
scripts/vm/journal.py           # 21 Mutation dataclasses
scripts/vm/cond.py              # COND_EVAL (34条件) + eval_one()
scripts/vm/sort.py              # feeds/needs 拓扑排序
scripts/vm/executor.py          # execute(ctx, effects) -> VMResult
scripts/vm/damage.py            # 共享伤害公式
scripts/vm/ops/__init__.py      # OP_TABLE
scripts/vm/ops/mod.py           # 最复杂：20+ stat targets, 3 modes
scripts/vm/ops/mark.py
scripts/vm/ops/abnormal.py
scripts/vm/ops/weather.py
scripts/vm/ops/dispel.py
scripts/vm/ops/steal.py
scripts/vm/ops/tick.py
scripts/vm/ops/double.py
scripts/vm/ops/charge.py
scripts/vm/ops/escape.py
scripts/vm/ops/hit.py           # + 共享伤害公式
scripts/vm/ops/exchange.py
scripts/vm/ops/reset.py
scripts/vm/ops/redirect.py
scripts/vm/ops/replay.py
scripts/vm/ops/borrow.py
scripts/vm/ops/return_.py
scripts/vm/ops/lock.py
scripts/vm/ops/interrupt.py
scripts/vm/ops/count.py
scripts/vm/ops/when.py          # 控制流工具（不在 OP_TABLE）
scripts/engine/__init__.py
scripts/engine/battle.py        # BattleEngine 继承 Battle
scripts/engine/skill_loader.py  # SkillLoader: validate/normalize/pre_resolve/pre_index/pre_sort
scripts/engine/trait_loader.py  # TraitLoader: validate/classify
scripts/engine/replayer.py      # JournalReplayer: 应用 mutations + 触发观察者
scripts/engine/observer.py      # Observer + ObserverRegistry
scripts/engine/snapshot.py      # Sprite + Battle -> Ctx（唯一的 sim 依赖）
```

### 文件删除/降级

| 文件 | 处理 |
|------|------|
| `scripts/sim/effects.py` | 保留 `effect_from_dict()` 过渡期使用，其余删除 |
| `scripts/sim/resolver.py` | 保留 `resolve_counter()` 和 `calc_damage()`（golden 对照），其余降级 |
| `scripts/sim/pipeline.py` | `SkillPipeline` 替换，`TurnPipeline.execute_turn_start()` 保留并重构 |
| `scripts/sim/traits/trait_engine.py` | Phase 5 后删除 |

---

## Phase 4: 替换原型 (第4-5周)

### 集成点

```python
# factory.py: 一行改动
from scripts.engine.battle import BattleEngine
battle = BattleEngine(player_a=..., player_b=..., weather=...)
```

### 外部系统零改动

以下文件完全不需要修改：
- `scripts/sim/agent.py` (AI 代理)
- `scripts/sim/ui_gradio.py` (Gradio UI)
- `scripts/sim/__main__.py` (入口)
- `scripts/sim/logging.py` (日志)

因为它们依赖 `Battle` 接口（execute_turn、run、log），而 `BattleEngine` 继承了该接口。

### 回归测试

1000 场随机战斗，固定种子。比较每回合 HP/能量/印记/天气/状态。

---

## Phase 5: 特性系统 (第5-6周)

### 观察者触发点

| 触发点 | 条件 | 时期 |
|--------|------|------|
| Ctx 构建后、VM 调用前 | `on_damage_taken` | 计算前 |
| Mutation 应用后 | `skill_use` | 事件后 |
| 换宠结算后 | `opp_switched` / `self_switched` | 事件后 |
| 入场结算后 | `sprite_entered` | 事件后 |
| 异常 tick 后 | `on_abnormal_tick` | 事件后 |
| 异常层数变化后 | `on_abnormal_changed` / `on_abnormal_applied` | 事件后 |
| KO 后 | `on_ko` / `on_self_ko` | 事件后 |
| 能量变化后 | `on_energy_changed` | 事件后 |
| 增益变化后 | `on_positive_changed` | 事件后 |
| 回合末 | `turn_end` | 回合边界 |

### 迁移步骤

1. 实现 ObserverRegistry 注册/触发（Phase 3）
2. 实现特性修正器收集（Phase 3 — trait_loader + inject_trait_modifiers）
3. 接入已重构的 28 个特性 JSON
4. Golden test 特性行为 vs 原型
5. 删除旧 trait_engine.py

---

## 关键接口匹配

| 原型接口 | 新引擎等效 |
|----------|----------|
| `Battle(player_a, player_b, weather)` | `BattleEngine` extends `Battle` |
| `Battle.execute_turn(agent_a, agent_b)` | 继承，内部重写 |
| `Battle.run(agent_a, agent_b) -> str` | 继承 |
| `Battle.log: list[TurnRecord]` | 继承 |
| `Skill.load(data)` | `SkillLoader.load(path) -> SkillObject` |
| `Sprite` 类 | 不变 |
| `GlobalEffects` | 不变 |

---

## 数据流：前 vs 后

**原型**:
```
Skill JSON -> Skill.load() -> effect_from_dict() -> Effect 对象
  -> SkillPipeline.execute() -> SkillResolver._check_condition()
  -> SkillResolver.dispatch_L3() -> SkillResolver.calc_damage()
  -> dispatch_* trait hooks
```

**新引擎**:
```
Skill JSON -> SkillLoader.load() -> validate -> normalize -> pre_resolve -> pre_index -> pre_sort
  -> SkillObject (不可变，全局共享)

BattleEngine.resolve_skill():
  make_ctx() -> Ctx (76字段快照)
  inject_trait_modifiers() -> 增强 Ctx
  fire_pre_calc_observers("on_damage_taken") -> ModifierInjection[]
  vm.execute(ctx, augmented_effects) -> VMResult(journal, counters)
  JournalReplayer.replay(journal) -> 应用 + 触发事件后观察者 + 生成事件
```

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 技能 JSON 格式不统一 | SkillLoader.normalize() 处理旧格式转换 |
| 伤害公式差异 | Phase 2 golden test 捕获所有差异并修正 |
| 特性系统复杂度 | 新 IR 格式特性原生支持，旧格式逐步迁移 |
| Ctx 快照构建开销 | Ctx 是 flat dataclass，O(1) 字段访问；Phase 3 后基准测试 |
| 观察者递归无限 | MAX_RECURSION_DEPTH=5，超限记录警告 |

---

## Phase 依赖关系

```
Phase 1 (VM 核心)
  |
  v
Phase 2 (Golden-test VM)
  |
  v
Phase 3 (引擎包装器)
  |
  +---> Phase 4 (替换原型)  [Phase 3 后期可并行]
  |
  +---> Phase 5 (特性系统)   [依赖 Phase 3；可与 Phase 4 重叠]
```
