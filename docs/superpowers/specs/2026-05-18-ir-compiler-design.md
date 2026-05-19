# IR 类型化 + 编译层 + 运行层 三层重构设计（方案 B：双 IR + 共享层）

## 现状

代码库已有两套并行的执行系统，各自用裸 dict 贯通全链路：

```
技能: Skill JSON → SkillLoader._normalize() → dict effects → VM executor (字符串分派)
特性: Trait JSON → DataDrivenTrait.__init__() → dict triggers → _fire() (字符串分派)
```

两套系统的条件、效果、值表达式、执行模型全都不一样。没有类型化 IR，没有编译期验证。

## 目标

方案 B：保持技能和特性两套 IR 独立（它们执行模型本质不同），但提取共享底层。

| 层 | 职责 | 关键变化 |
|----|------|---------|
| IR 层 | 类型定义 | 技能 IR + 特性 IR，各自 frozen dataclass |
| 编译层 | 验证+转换 | SkillCompiler + TraitCompiler，各自 N-Pass 管线 |
| 运行层 | 执行 | 字符串分派 → match/case 类型分派 |
| 共享层 | 底层原语 | ValueResolver + EffectApplier，两个编译器共用 |

架构总览：

```
                  ┌─ Shared Layer ─┐
                  │ IRValue        │  Literal | Query | RefExpr
                  │ Condition      │  skill: CondExpr | And/Or/Not
                  │                │  trait: PathCond | And/Or/Not
                  │ EffectApplier  │  stat/abnormal/mark/weather 共用
                  │ ValueResolver  │  resolve → int | float | str | bool
                  └────────────────┘
                    ↑            ↑
         SkillCompiler         TraitCompiler
              │                    │
     ┌────────┴────────┐  ┌───────┴──────────┐
     │ ParsePass       │  │ TriggerParsePass  │
     │ InjectHitPass   │  │ ConditionCheck    │
     │ ValidatePass    │  │ AuraExpandPass    │
     │ SortPass        │  │ ValidatePass      │
     └────────┬────────┘  └───────┬──────────┘
              │                    │
     CompiledSkill          CompiledTrait
              │                    │
     ┌────────┴────────────────────┴──────────┐
     │         Battle Pipeline (调度器)         │
     │  L0: trait.on_modifier() → skill L0     │
     │  L1: trait.on_damage()   → skill L1     │
     │  L2: trait.on_defend()   → skill L2     │
     │  L3: skill VM effects    → trait .on_skill_use  │
     │  L5: skill escape/return → trait counters        │
     └─────────────────────────────────────────┘
```

**核心原则：管线是调度器，IR 是指令集。调度器保证执行顺序（无写冲突），指令集保证类型安全。**

---

## 一、共享层

### 1.1 IRValue — 统一值系统

```python
# backend/vm/ir_values.py

from dataclasses import dataclass, field

@dataclass(frozen=True)
class Literal:
    """编译期已知的字面量。"""
    value: int | float | str | bool

@dataclass(frozen=True)
class Query:
    """编译期已解析的寄存器查询。field 是 Ctx 属性名，运行时 O(1) getattr。
    用于技能 IR。"""
    field: str              # Ctx 属性名（编译期从 ADDRESS_MAP 解析）
    name: str | None = None # dict 寄存器的 sub-key
    scale: float = 1.0
    offset: int = 0
    per: float | None = None
    default: object = None

@dataclass(frozen=True)
class RefExpr:
    """编译期解析的路径表达式。用于特性 IR。
    "=@self.energy * 2" → RefExpr(root="self", path=["energy"], multiplier=2)"""
    root: str               # self | target | attacker | player | opponent | battle
    path: list[str]         # ["energy"] 或 ["effects[name=灼烧]", "stacks"]
    multiplier: float = 1.0
    offset: int = 0

IRValue = Literal | Query | RefExpr
```

### 1.2 ValueResolver — 统一值解析

```python
# backend/vm/resolver.py

def resolve(ctx, value: IRValue, trait_ctx: dict | None = None) -> int | float | str | bool:
    """统一解析三种值类型。技能用 Ctx，特性用 trait_ctx dict。"""
    if isinstance(value, Literal):
        return value.value
    if isinstance(value, Query):
        return _resolve_query(ctx, value)
    if isinstance(value, RefExpr):
        return _resolve_ref(trait_ctx, value)
```

### 1.3 EffectApplier — 共享效果原语

从 `trait_engine.py` 的 `_apply_effect()` 提取为独立模块。技能 VM 和 trait engine 共用 stat/abnormal/mark/weather 的应用逻辑。

```python
# backend/vm/effect_applier.py

def apply_effect(effect, target_sprite, battle, team) -> list[str]:
    """应用单条效果到目标精灵。返回事件描述列表。"""
    match effect:
        case StatEffect():  return _apply_stat(effect, target_sprite)
        case AbnormalEffect(): return _apply_abnormal(effect, target_sprite, battle)
        case MarkEffect():  return _apply_mark(effect, battle, team)
        case WeatherEffect(): return _apply_weather(effect, battle)
```

技能 VM 的 ops 模块和 trait engine 的 `_fire()` 都调用此模块，消除当前两套独立实现。

---

## 二、技能 IR

### 2.1 底层类型

```python
# backend/vm/ir_skill.py

Target = str           # sprite_self | sprite_opp | team_own | ...
ModStat = str          # atk | def | power | combo | energy_cost | ...
Scope = str            # battlefield | persistent | permanent
```

### 2.2 Condition（技能专用——命名条件）

```python
@dataclass(frozen=True)
class CondExpr:
    """原子条件。cond 编译期已验证在 COND_EVAL 中存在。"""
    cond: str
    params: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class AndCond:
    conditions: tuple['SkillCondition', ...]

@dataclass(frozen=True)
class OrCond:
    conditions: tuple['SkillCondition', ...]

@dataclass(frozen=True)
class NotCond:
    condition: 'SkillCondition'

SkillCondition = CondExpr | AndCond | OrCond | NotCond
```

### 2.3 When 块

```python
@dataclass(frozen=True)
class WhenBranch:
    cond: SkillCondition
    then: tuple['SkillIROp', ...]

@dataclass(frozen=True)
class WhenBlock:
    cond: SkillCondition
    then: tuple['SkillIROp', ...]
    else_: tuple['SkillIROp', ...] = ()
    elif_: tuple[WhenBranch, ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

### 2.4 Op 节点（21 个）

```python
# 注意：以下只列关键字段，完整字段见原版 spec。
# 每个 op 都有 feeds/needs/priority 用于拓扑排序。

@dataclass(frozen=True)
class ModOp:
    """属性变化。372 个技能使用此 opcode。"""
    target: Target
    stat: ModStat
    value: IRValue
    mode: str = "set"           # set | add | multiply
    scope: Scope = "battlefield"
    steps: int = 0
    on_next: bool = False
    per_hit: bool = False
    skill_filter: str | None = None
    skill_where: dict | None = None
    if_type: str | None = None
    element: str | None = None
    per_element: int | None = None
    name: str | None = None
    delay: int = 0
    ttl: int = 0
    cooldown: int = 0
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class HitOp:
    """伤害。InjectHitPass 为攻击技能隐式注入。"""
    power: IRValue
    type: str                   # 物攻 | 魔攻
    element: str | None = None
    combo: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class MarkOp:
    target: str                 # team_own | team_opp
    name: str
    stacks: int = 1
    value: IRValue | None = None
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class AbnormalOp:
    target: str                 # sprite_self | sprite_opp
    name: str                   # 中毒|灼烧|冻结|寄生|眩晕|萌化
    stacks: int = 1
    scope: str = "battlefield"
    heal_pct: float = 0.0
    energy_gain: int = 0
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class WeatherOp:
    weather: str                # rain | sand | snow
    turns: int = 8
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class DispelOp:
    target: str
    what: str                   # positive | negative | mark | abnormal
    name: str | None = None
    limit: int | None = None
    type_limit: int | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class StealOp:
    target: str                 # sprite_opp | team_opp
    what: str                   # positive | mark | energy
    name: str | None = None
    amount: int = 0
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class TickOp:
    target: str
    name: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class DoubleOp:
    target: str
    what: str                   # positive | negative | abnormal | mark
    name: str | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ChargeOp:
    target: str = "sprite_self"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class EscapeOp:
    target: str                 # sprite_self | sprite_opp
    inherit: bool = False
    urgent: bool = False
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ReturnOp:
    target: str                 # sprite_self | sprite_opp
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class LockOp:
    target: str
    turns: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class InterruptOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ExchangeOp:
    what: str                   # hp_ratio | effects | skills | adjacent_skills
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ResetOp:
    target: str
    stat: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class RedirectOp:
    target: str
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ReplayOp:
    from_: str                  # sprite_self | team_burst
    skill_filter: dict | None = None
    what: str = ""
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class BorrowOp:
    from_: str                  # skill_opp_current
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class CountOp:
    name: str = ""
    when: SkillCondition | None = None
    then: tuple['SkillIROp', ...] = ()
    scope: str = "persistent"
    feeds: str = ""
    needs: str = ""
    priority: int = 0

SkillIROp = (
    ModOp | HitOp | MarkOp | AbnormalOp | WeatherOp |
    DispelOp | StealOp | TickOp | DoubleOp | ChargeOp |
    EscapeOp | ReturnOp | LockOp | InterruptOp |
    ExchangeOp | ResetOp | RedirectOp | ReplayOp |
    BorrowOp | CountOp | WhenBlock
)
```

### 2.5 CompiledSkill

```python
@dataclass(frozen=True)
class CompiledSkill:
    id: int
    name: str
    element: str | IRValue
    skill_type: str
    power: int
    energy_cost: int
    priority: int
    combo: int
    counter: str
    effects: tuple[SkillIROp, ...]      # 已排序、已验证
    description: str
    tag: str
    use_devotion: bool
    usable_while_charging: bool = False
    position_locked: bool = False
```

---

## 三、特性 IR

### 3.1 Condition（特性专用——路径条件）

特性条件系统与技能不同：特性需要在任意 context 对象上进行路径反射求值。

```python
# backend/vm/ir_trait.py

@dataclass(frozen=True)
class PathCond:
    """路径条件: self.energy > 5 → PathCond(path=["self","energy"], op="gt", value=5)"""
    path: list[str]             # ["self", "energy"] | ["battle", "globals", "weather"]
    op: str                     # eq | neq | gt | gte | lt | lte | in | not_in | contains
    value: IRValue

@dataclass(frozen=True)
class FnCond:
    """注册函数条件: {"kind": "fn", "name": "is_weekend"}"""
    name: str

@dataclass(frozen=True)
class AndCond:
    conditions: tuple['TraitCondition', ...]

@dataclass(frozen=True)
class OrCond:
    conditions: tuple['TraitCondition', ...]

@dataclass(frozen=True)
class NotCond:
    condition: 'TraitCondition'

TraitCondition = PathCond | FnCond | AndCond | OrCond | NotCond
```

### 3.2 特性 Op 节点

特性有 3 类独特的操作，技能 IR 没有：

**A) 效果变异类**（修改已有状态，而非创建新状态）

```python
@dataclass(frozen=True)
class MutateEffectOp:
    """修改目标已有属性的 steps/stacks。灰色肖像等。"""
    target: str                     # self | target | killer
    filter: dict                    # {is_stat: true, steps<0: true}
    delta_steps: int = 0
    delta_stacks: int = 0

@dataclass(frozen=True)
class RemoveEffectOp:
    """按 source 清除效果。aura 离场用。"""
    source: str
    target: str                     # self | target | opponent_active
```

**B) 引擎注入类**（修改技能对象/行动，而非 Sprite）

```python
@dataclass(frozen=True)
class BattleSkillMutOp:
    """修改技能属性。圣火骑士等。"""
    filter: dict                    # {is_attack: true}
    field: str                      # next_attack_mult | element | energy_cost | power
    value: IRValue
    op: str = "set"                 # set | mult | add
    target: str = "all"             # all | current

@dataclass(frozen=True)
class UseModifierOp:
    """修改 SkillUse.modifiers。完全偏振、绝对秩序等。"""
    key: str                        # power_mult | damage_mult | damage_reduction | ...
    value: IRValue
    op: str = "set"                 # set | mult | add
    target: str = "modifiers"       # modifiers | battleskill

@dataclass(frozen=True)
class ActionModifierOp:
    """修改/禁止可用行动。"""
    action: str                     # forbid_skill | forbid_gather | restrict_slots | seal_all_but
    slot: int | None = None
    slots: list[int] | None = None
    force: str | None = None        # gather | skill:N | switch:N
```

**C) 延迟/继承/队伍类**

```python
@dataclass(frozen=True)
class ScheduleOp:
    """延迟 N 回合执行效果。"""
    turns: int
    phase: str = "start"
    effects: tuple['TraitEffect', ...]

@dataclass(frozen=True)
class InheritEffectsOp:
    """效果继承给下一个入场精灵。"""
    scope: str = "battlefield"
    source_sprite: str = "self"
    target: str = "enemy_new"
    via_pending: bool = False

@dataclass(frozen=True)
class TeamCounterOp:
    """写入队伍级共享计数器。慢热型等。"""
    key: str
    delta: int = 1
    target_team: str = "own"        # own | opp

@dataclass(frozen=True)
class TransformOp:
    """形态变换：替换 species + skills。"""
    species: str
    skills: list[str] | None = None
    reset_hp: bool = False
    reset_energy: bool = False

@dataclass(frozen=True)
class TraitInteractionOp:
    """特性交互：压制/删除/复制目标特性。"""
    action: str                     # suppress | remove | copy
    target: str                     # target | self
    copy_from: str | None = None    # copy 的源
    new_ability: str | None = None  # remove 后的新特性名

@dataclass(frozen=True)
class LivesOp:
    """修改队伍魔力值。诈死等。"""
    delta: int
    target_team: str = "own"
```

### 3.3 共享效果（特性与技能共用 EffectApplier）

这些效果类型与技能 IR 中对应节点在语义上等价，但作为特性效果的载体：

```python
@dataclass(frozen=True)
class TraitStatEffect:
    """属性变化（特性版）。与 ModOp 语义等价但结构略有不同。"""
    kind: str = "stat"
    target: str = "self"            # self | target | killer | random_bench
    stat: str = ""                  # atk | def | sp_atk | sp_def | speed
    steps: IRValue                  # 支持 ref 表达式动态值
    scope: str = "battlefield"
    source: str = ""

@dataclass(frozen=True)
class TraitAbnormalEffect:
    kind: str = "abnormal"
    target: str = "opp"
    name: str = ""
    stacks: IRValue = 1
    scope: str = "battlefield"
    source: str = ""

@dataclass(frozen=True)
class TraitMarkEffect:
    kind: str = "mark"
    name: str = ""
    stacks: int = 1
    mark_target: str = "opp_team"   # own_team | opp_team

@dataclass(frozen=True)
class TraitWeatherEffect:
    kind: str = "weather"
    weather: str = ""
    turns: int = 8

@dataclass(frozen=True)
class TraitSpecialEffect:
    """特殊效果（回复/能量/印记操作等）。"""
    kind: str = "special"
    name: str = ""                  # heal | direct_heal | gain_energy | energy_set
                                    # steal_energy | steal_energy_all | lose_energy
                                    # lives_delta | lives_add | take_damage
                                    # dispel_mark | steal_mark | convert_mark
                                    # team_counter_add
    value: IRValue | None = None
    amount: IRValue | None = None
    target: str = "self"
    target_team: str = "own"

# 特性效果联合类型
TraitEffect = (
    TraitStatEffect | TraitAbnormalEffect | TraitMarkEffect |
    TraitWeatherEffect | TraitSpecialEffect |
    MutateEffectOp | RemoveEffectOp |
    ScheduleOp | InheritEffectsOp | TeamCounterOp |
    TransformOp | TraitInteractionOp | LivesOp
)
```

### 3.4 TraitTrigger 和 CompiledTrait

```python
@dataclass(frozen=True)
class TraitTrigger:
    """单个触发器。on 是 hook 名，条件+效果绑定。"""
    on: str                         # entry | leave | turn_start | turn_end
                                    # modifier | damage | defend | skill_use
                                    # take_damage | ko_enemy | counter_success | faint
                                    # energy_change | gain_effect | inflict | enemy_leave
                                    # abnormal_tick | before_take_damage | before_action
    condition: TraitCondition | None = None
    effects: tuple[TraitEffect, ...] = ()
    effects_mode: str = "accumulate"    # accumulate | replace | conditional_replace
    clear_condition: TraitCondition | None = None  # conditional_replace 用
    delay: int = 0                      # 延迟回合数
    delay_phase: str = "start"
    counter: str | None = None          # 累积计数器 key
    counter_op: str = "inc"             # inc | dec | set
    counter_value: IRValue | None = None
    counter_trigger: dict | None = None # {op: gte, value: N}
    counter_reset: bool = False
    track: dict | None = None           # {key: ..., expr: ..., trigger_on_change: bool}
    use_modifiers: dict[str, dict] | None = None   # key → {op, value}
    battleskill_mut: tuple[BattleSkillMutOp, ...] = ()
    action_modifier: ActionModifierOp | None = None
    pending_effects: tuple[TraitEffect, ...] = ()
    flags: dict | None = None           # {_escape_pending: true, counters.xxx: 0}
    team_counters: dict | None = None   # {key: delta}

@dataclass(frozen=True)
class CompiledTrait:
    id: int
    name: str
    description: str
    triggers: tuple[TraitTrigger, ...]  # 已展开 aura、已验证
```

---

## 四、编译层

### 4.1 SkillCompiler — 4-Pass 管线

```python
# backend/vm/compiler/skill_compiler.py

from .context import CompilerContext, CompileError, CompilationError
from .passes.skill_parse import SkillParsePass
from .passes.inject_hit import InjectHitPass
from .passes.skill_validate import SkillValidatePass
from .passes.sort import SortPass

class SkillCompiler:
    def __init__(self, passes=None):
        self.passes = passes or [
            SkillParsePass(),
            InjectHitPass(),
            SkillValidatePass(),
            SortPass(),
        ]

    def compile(self, data: dict) -> CompiledSkill:
        ctx = CompilerContext(
            raw=data, ir=[], errors=[], warnings=[],
            meta={"skill_type": data.get("skill_type", ""),
                  "power": data.get("power", 0)},
        )
        for p in self.passes:
            ctx = p.apply(ctx)
        if ctx.errors:
            raise CompilationError(ctx.errors)
        return CompiledSkill(
            id=data.get("id", 0),
            name=data["name"],
            element=data.get("element", "普通"),
            skill_type=data["skill_type"],
            power=data.get("power", 0),
            energy_cost=data.get("energy_cost", 0),
            priority=data.get("priority", 0),
            combo=data.get("combo", 1),
            counter=data.get("counter", ""),
            effects=tuple(ctx.ir),
            description=data.get("description", ""),
            tag=data.get("tag", ""),
            use_devotion=data.get("use_devotion", False),
            usable_while_charging=data.get("usable_while_charging", False),
            position_locked=data.get("position_locked", False),
        )

    def compile_all(self, data_dir: str) -> dict[str, CompiledSkill]:
        """批量编译目录下所有 JSON。"""
        import json, os
        from pathlib import Path
        result = {}
        for fpath in Path(data_dir).glob("*.json"):
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            try:
                result[data["name"]] = self.compile(data)
            except CompilationError as e:
                raise CompilationError([
                    CompileError(0, f"{fpath.name}: {e}")
                ]) from e
        return result
```

### 4.2 SkillParsePass

输入 `CompilerContext(raw=data, ir=[], ...)`，输出 `ctx.ir` 为 typed SkillIROp 列表。

递归处理：普通 effect → 构造对应 IROp；`when` 块 → 递归构造 WhenBlock。

Query 预解析：遇到 `{"q": ..., "of": ...}` 时查 ADDRESS_MAP，把 `(of, q)` 映射到 `field` 字符串存入 Query.field。

### 4.3 InjectHitPass

攻击技能（skill_type ∈ {物攻, 魔攻} 且 power > 0），若 effects 中没有显式 HitOp，追加一个。

### 4.4 SkillValidatePass

编译期拦截所有曾经的运行时错误：检查 target, stat, scope, cond 的白名单；检查 Query field 非空；检查 HitOp 必填字段；检查值域约束。

### 4.5 SortPass

复用现有 topological sort 逻辑，适配 typed SkillIROp 的 feeds/needs/priority 字段。

---

### 4.6 TraitCompiler — 3-Pass 管线

```python
# backend/vm/compiler/trait_compiler.py

class TraitCompiler:
    def __init__(self, passes=None):
        self.passes = passes or [
            TraitParsePass(),
            AuraExpandPass(),
            TraitValidatePass(),
        ]

    def compile(self, data: dict) -> CompiledTrait:
        ctx = CompilerContext(
            raw=data, ir=[], errors=[], warnings=[],
            meta={},
        )
        for p in self.passes:
            ctx = p.apply(ctx)
        if ctx.errors:
            raise CompilationError(ctx.errors)
        return CompiledTrait(
            id=data.get("id", 0),
            name=data["name"],
            description=data.get("description", ""),
            triggers=tuple(ctx.ir),  # ctx.ir 存的是 TraitTrigger 列表
        )
```

### 4.7 TraitParsePass

解析 raw triggers 数组 → typed TraitTrigger 列表。递归解析每个 trigger 内的 effects、condition、use_modifiers、battleskill_mut 等子结构。

### 4.8 AuraExpandPass

`aura` 定义展开为 `entry` + `leave` 触发器对：
- entry: 对目标施加效果
- leave: 按 source 清除效果（RemoveEffectOp）

### 4.9 TraitValidatePass

检查：
- `on` hook 名在合法 hook 集合中
- `condition` path 路径合法（root 在 CONTEXT_KEYS 中）
- `effects_mode` 在 {accumulate, replace, conditional_replace}
- 必填字段完整（stat effect 有 stat，abnormal 有 name）

---

## 五、运行层

### 5.1 技能 executor

```python
# backend/vm/executor.py

def execute(ctx: Ctx, effects: tuple[SkillIROp, ...]) -> list[Mutation]:
    return process_effects(ctx, effects)

def process_effects(ctx: Ctx, effects: tuple[SkillIROp, ...]) -> list[Mutation]:
    journal: list[Mutation] = []
    for op in effects:
        journal.extend(process_one(ctx, op))
    return journal

def process_one(ctx: Ctx, op: SkillIROp) -> list[Mutation]:
    match op:
        case WhenBlock(cond=cond, then=then, else_=else_, elif_=elif_):
            if eval_one(ctx, cond):
                return process_effects(ctx, then)
            for branch in elif_:
                if eval_one(ctx, branch.cond):
                    return process_effects(ctx, branch.then)
            return process_effects(ctx, else_)
        # ... 21 个 op handler（match/case 类型分派）
```

### 5.2 特性 executor

```python
# backend/vm/executor_trait.py

def process_trigger(ctx: dict, trigger: TraitTrigger) -> list[str]:
    """执行单个 TraitTrigger。管线在特定 hook 点调用。"""
    events: list[str] = []

    # 条件检查
    if trigger.condition and not eval_path_cond(trigger.condition, ctx):
        return events

    # 延迟处理
    if trigger.delay and trigger.delay > 0:
        _schedule_trigger(trigger, ctx)
        return events

    # 计数器累积
    if trigger.counter:
        if not _handle_counter(trigger, ctx):
            return events

    # 技能修改器
    if trigger.use_modifiers:
        _apply_use_modifiers(trigger.use_modifiers, ctx)

    # 技能属性变异
    for mut in trigger.battleskill_mut:
        _apply_battleskill_mut(mut, ctx)

    # 效果应用（调用共享 EffectApplier）
    for eff in trigger.effects:
        events += apply_effect(eff, ...)

    # 标志位
    if trigger.flags:
        _apply_flags(trigger.flags, ctx)

    # 队伍计数器
    if trigger.team_counters:
        _apply_team_counters(trigger.team_counters, ctx)

    return events
```

### 5.3 管线集成

管线保持不变——trait hooks 在固定节点被调用：

```
L0 (modifier):
  skill L0 resolver → trait dispatch_modifier() → burst/dynamic calcs
L1 (power):
  dynamic power/combo → trait dispatch_damage()
L2 (damage):
  trait dispatch_defend() → before_take_damage → damage → dispatch_take_damage → dispatch_ko_enemy
L3 (state):
  skill VM execute() — 产出 Mutation[]
L4 (counter):
  反击结算
L5 (field):
  escape/return/borrow → dispatch_skill_use() → team counters

TurnPipeline:
  trait dispatch_turn_start() → transmission → position scan → trait entry/leave
```

**写冲突不会发生**：因为 trait hook 和 skill phases 在管线中有确定的先后顺序，它们在不同阶段修改相同状态（如 use.modifiers），但从不"同时"修改。

### 5.4 resolve 简化

```python
# backend/vm/resolve.py

def resolve(ctx: Ctx, value: IRValue) -> int | float | str | bool:
    if isinstance(value, Literal):
        return value.value
    if isinstance(value, Query):
        # field 已预解析，O(1) getattr
        raw = getattr(ctx, value.field)
        if value.name is not None and isinstance(raw, dict):
            raw = raw.get(value.name, value.default or 0)
        if value.default is not None and not raw:
            raw = value.default
        if isinstance(raw, (str, bool)):
            return raw
        if value.per is not None and value.per != 0:
            raw = int(raw / value.per)
        if value.scale != 1.0:
            raw = int(raw * value.scale)
        if value.offset:
            raw = int(raw + value.offset)
        return raw
    if isinstance(value, RefExpr):
        # 在 trait ctx dict 中解析路径
        return _resolve_ref(ctx, value)
```

无 ADDRESS_MAP 运行时查找，无 `isinstance(value, dict)` 格式猜测。

### 5.5 cond 适配

`cond.py` 中 handler 签名从 `(ctx, cond: dict)` 改为 `(ctx, params: dict)`：

```python
def eval_one(ctx: Ctx, cond: SkillCondition) -> bool:
    match cond:
        case CondExpr(cond=key, params=params):
            return COND_EVAL[key](ctx, params)
        case AndCond(conditions=conds):
            return all(eval_one(ctx, c) for c in conds)
        case OrCond(conditions=conds):
            return any(eval_one(ctx, c) for c in conds)
        case NotCond(condition=c):
            return not eval_one(ctx, c)
```

### 5.6 OP_DISPATCH 移除

`ops/__init__.py` 中的 `OP_DISPATCH` 字典删除。各 `op_*` handler 签名从 `(ctx, effect: dict)` 改为 `(ctx, op: XxxOp)`。

`"damage": _op_noop` 和 `_op_noop` 函数删除。

---

## 六、删除清单

| 文件 | 删除内容 | 原因 |
|------|---------|------|
| `engine/skill_loader.py` | 整个文件 | 被 SkillCompiler 替代 |
| `vm/executor.py` | `if "kind" in effect: return []` | 死代码 |
| `vm/ops/__init__.py` | `"damage": _op_noop`, `_op_noop()` | 无技能使用 |
| `vm/ops/__init__.py` | `OP_DISPATCH` 字典 | 被 match/case 替代 |
| `sim/traits/trait_engine.py` | `ConditionEvaluator._resolve_path()`, `_apply_effect()` | 移入共享层 |
| `sim/traits/trait_engine.py` | `_process_triggers()`, `_expand_aura()` | 移入 AuraExpandPass |

**SkillLoader 调用方（需同步更新）：**

| 文件 | 当前用法 | 改为 |
|------|---------|------|
| `engine/battle.py:83-84` | `from backend.engine.skill_loader import SkillLoader` | `from backend.vm.compiler import SkillCompiler` |
| `api/main.py` | 间接触发（通过 Battle） | 不变（Battle 内部改） |
| `tools/test_skills.py` | 直接使用 SkillLoader | 改用 SkillCompiler |
| `engine/test_battle_replay.py` | 直接使用 SkillLoader | 改用 SkillCompiler |
| `sim/battle.py` | Trait 加载/注册 | 改用 TraitCompiler |

---

## 七、文件结构

```
backend/
  vm/
    ir_values.py                  # 新增：IRValue (Literal | Query | RefExpr)
    ir_skill.py                   # 新增：技能 IR 节点（21 op + WhenBlock + SkillCondition）
    ir_trait.py                   # 新增：特性 IR 节点（TraitEffect + TraitTrigger + TraitCondition）
    effect_applier.py             # 新增：共享效果应用器（提取自 trait_engine）
    resolver.py                   # 改：统一 resolve(Literal|Query|RefExpr)
    compiler/
      __init__.py                 # 改：导出 SkillCompiler + TraitCompiler
      context.py                  # 新增：CompilerContext, CompileError, CompilationError
      skill_compiler.py           # 新增：SkillCompiler 入口
      trait_compiler.py           # 新增：TraitCompiler 入口
      passes/
        __init__.py
        skill_parse.py            # SkillParsePass
        inject_hit.py             # InjectHitPass
        skill_validate.py         # SkillValidatePass
        sort.py                   # SortPass（适配 typed SkillIROp）
        trait_parse.py            # TraitParsePass
        aura_expand.py            # AuraExpandPass
        trait_validate.py         # TraitValidatePass
    executor.py                   # 改：match/case 类型分派
    executor_trait.py             # 新增：特性 trigger 执行器
    cond.py                       # 改：接受 SkillCondition
    cond_path.py                  # 新增：路径条件求值器（提取自 trait_engine.ConditionEvaluator）
    ctx.py                        # 不变
    journal.py                    # 不变
    sort.py                       # 保留（SortPass 引用 _PHASE 常量）
    ops/
      mod.py, hit.py, mark.py, ... # 改：handler 签名 (ctx, XxxOp)
      __init__.py                 # 删：OP_DISPATCH, _op_noop, "damage"
  engine/
    skill_loader.py               # 删：整体被 SkillCompiler 替代
  sim/
    traits/
      trait_engine.py             # 改：DataDrivenTrait 委托给 executor_trait + effect_applier
```

---

## 八、测试策略

```
backend/vm/compiler/
  test_skill_parse.py             # 每种 op 的 dict→IROp 转换
  test_parse_query.py             # Query 预解析：q+of → field
  test_inject_hit.py              # 攻击技能补 hit
  test_skill_validate.py          # 每种检查项的合法/非法 case
  test_sort.py                    # 拓扑排序正确性
  test_trait_parse.py             # triggers 解析
  test_aura_expand.py             # aura → entry+leave 配对
  test_trait_validate.py          # 特性字段验证
  test_compiler_e2e.py            # 470 技能 + ~120 特性全量编译
  test_ir_frozen.py               # IR 不可变、可哈希、可 pickle
  test_effect_applier.py          # 共享效果应用器独立测试
  test_value_resolver.py          # Literal/Query/RefExpr 三种解析
```

每个 Pass 独立测试，不需要 VM 环境。共享层独立测试。

---

## 九、迁移步骤

1. **新增** `backend/vm/ir_values.py` — IRValue 定义
2. **新增** `backend/vm/ir_skill.py` — 技能 IR 节点
3. **新增** `backend/vm/ir_trait.py` — 特性 IR 节点
4. **新增** `backend/vm/effect_applier.py` — 共享效果应用器（提取自 trait_engine）
5. **新增** `backend/vm/compiler/` — 整个编译管线（两个编译器）
6. **改造** `backend/vm/executor.py` — match/case 类型分派
7. **新增** `backend/vm/executor_trait.py` — 特性 trigger 执行器
8. **改造** `backend/vm/resolve.py` — 统一值解析
9. **改造** `backend/vm/cond.py` — 接受 SkillCondition
10. **新增** `backend/vm/cond_path.py` — 路径条件求值器
11. **改造** `backend/vm/ops/*.py` — handler 签名更新
12. **改造** `backend/sim/traits/trait_engine.py` — 委托给 executor_trait + effect_applier
13. **删除** `backend/engine/skill_loader.py` + 相关死代码
14. **更新** 所有调用方（engine/battle.py, sim/battle.py, api/main.py, tools/*.py）
15. **运行** 全量测试 + 470 技能 + ~120 特性编译验证

---

## 十、已知设计约束

### 10.1 ModOp 的 stringly-typing

ModOp 承载 25+ 种不同语义（atk, def, sp_atk, sp_def, speed, power, energy, priority, energy_cost, combo, accuracy, evasion, crit_rate, damage_mult, damage_reduction, hp, life_drain, devotion...）。类型系统无法在编译期区分 `ModOp(stat="power")` 和 `ModOp(stat="energy")`。拆分会带来 25+ 个新 opcode，在 470 技能规模下收益不明显。**作为已知技术债务记录，不做现在就改的事。**

### 10.2 条件系统的双轨制

技能用命名条件（`on_ko`, `counter_succeeded`），特性用路径条件（`self.energy > 5`）。两者服务于不同的上下文对象（Ctx vs hook ctx dict），求值时机不同，不统一。共享 And/Or/Not 组合器。

### 10.3 空壳技能

128 个技能（27%）的 `effects` 数组为空——纯攻击技能依赖 InjectHitPass 隐式注入 HitOp。这意味着 27% 的技能 IR 在编译前是空壳。暂无更好的表达方式。

### 10.4 缺少的能力（刻意不做）

- **迭代/循环**：不需要。multi_hit 是批量处理。
- **随机分支**：不需要。引擎随机性在伤害浮动和命中判定。
- **变量绑定**：不需要。纯函数式模型——输入 Ctx，输出 Mutation[]。
- **嵌套执行**：不需要。禁止嵌套调用避免递归复杂度。
- **N回合延迟**：特性已通过 ScheduleOp + delay 字段支持。
- **互斥组**：通过条件天然的互斥性避免。未提供显式互斥语法。

### 10.5 两个编译器、一个管线

技能和特性各自编译，但执行时由管线统一调度。管线中 hook 的调用顺序是固定的（L0→L1→L2→L3→L4→L5），不会出现两个 IR 同时修改同一状态的情况。**管线本身就是同步机制。**
