# IR 类型化 + 编译层 + 运行层 三层重构设计

## 现状

```
Skill JSON → SkillLoader._normalize() → dict effects → VM executor (字符串分派)
                                                    → resolve() (isinstance 猜测)
                                                    → OP_DISPATCH (dict 解包)
```

裸 dict 贯穿全链路。没有类型化 IR，没有编译期验证，所有错误运行时暴露。

## 目标

三层一起改，IR 是共享契约：

| 层 | 职责 | 关键变化 |
|----|------|---------|
| IR 层 | 类型定义 | dict → frozen dataclass，不可变可缓存 |
| 编译层 | 验证+转换 | 替换 SkillLoader，4-Pass 管线 |
| 运行层 | 执行 | 字符串分派 → match/case 类型分派，Query 预解析 |

---

## 一、IR 层 — 类型化节点

### 1.1 Value 系统

```python
# backend/vm/ir.py

from dataclasses import dataclass, field

@dataclass(frozen=True)
class Literal:
    """编译期已知的字面量。"""
    value: int | float | str | bool

@dataclass(frozen=True)
class Query:
    """编译期已解析的寄存器查询。field 是 Ctx 属性名，运行时 O(1) getattr。"""
    field: str              # Ctx 属性名（编译期从 ADDRESS_MAP 解析）
    name: str | None = None # dict 寄存器的 sub-key
    scale: float = 1.0
    offset: int = 0
    per: float | None = None
    default: object = None

IRValue = Literal | Query
```

编译期 `{"q": "hp_ratio", "of": "sprite_self"}` → `Query(field="hp_self_ratio")`。运行时不再查 ADDRESS_MAP。

### 1.2 Op 节点（21 个）

```python
# 字面量类型
Target = str           # sprite_self | sprite_opp | team_own | ...
ModStat = str          # atk | def | power | combo | energy_cost | ...
Scope = str             # battlefield | persistent | permanent

@dataclass(frozen=True)
class ModOp:
    target: str
    stat: str
    value: IRValue
    mode: str = "set"               # set | add | multiply
    scope: str = "battlefield"
    steps: int = 0                  # 旧语法糖，内部转 value
    skill_filter: str | None = None
    skill_where: dict | None = None
    element: str | None = None
    per_element: int | None = None
    name: str | None = None
    on_next: bool = False
    if_type: str | None = None
    feeds: str = ""
    needs: str = ""
    delay: int = 0
    ttl: int = 0
    cooldown: int = 0
    priority: int = 0
    per_hit: bool = False

@dataclass(frozen=True)
class HitOp:
    power: IRValue
    type: str                       # 物攻 | 魔攻
    element: str | None = None
    combo: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class MarkOp:
    target: str                     # team_own | team_opp
    name: str
    stacks: int = 1
    value: IRValue | None = None    # 动态层数（与 stacks 互斥）
    then: tuple['IROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class AbnormalOp:
    target: str                     # sprite_self | sprite_opp
    name: str
    stacks: int = 1
    scope: str = "battlefield"
    heal_pct: float = 0.0
    energy_gain: int = 0
    then: tuple['IROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class WeatherOp:
    weather: str
    turns: int = 8
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class DispelOp:
    target: str
    what: str                       # positive | negative | mark | abnormal
    name: str | None = None
    limit: int | None = None
    type_limit: int | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class StealOp:
    target: str                     # sprite_opp | team_opp
    what: str                       # positive | mark | energy
    name: str | None = None
    amount: int = 0
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class TickOp:
    target: str                     # sprite_self | sprite_opp
    name: str                       # 异常名称
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class DoubleOp:
    target: str
    what: str                       # positive | negative | abnormal | mark
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
    target: str                     # sprite_self | sprite_opp
    inherit: bool = False
    urgent: bool = False
    then: tuple['IROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ReturnOp:
    target: str                     # sprite_self | sprite_opp
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
    what: str                       # hp_ratio | effects | skills | adjacent_skills
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
    from_: str                      # sprite_self | team_burst
    skill_filter: dict | None = None
    what: str = ""
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class BorrowOp:
    from_: str                      # skill_opp_current
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class CountOp:
    name: str = ""
    when: 'Condition' = None
    then: tuple['IROp', ...] = ()
    scope: str = "persistent"
    feeds: str = ""
    needs: str = ""
    priority: int = 0
```

### 1.3 Condition 系统

```python
@dataclass(frozen=True)
class CondExpr:
    """原子条件。cond 编译期已验证在 COND_EVAL 中存在。"""
    cond: str
    params: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class AndCond:
    conditions: tuple['Condition', ...]

@dataclass(frozen=True)
class OrCond:
    conditions: tuple['Condition', ...]

@dataclass(frozen=True)
class NotCond:
    condition: 'Condition'

Condition = CondExpr | AndCond | OrCond | NotCond
```

### 1.4 When 块

```python
@dataclass(frozen=True)
class WhenBranch:
    """elif 分支。"""
    cond: Condition
    then: tuple['IROp', ...]

@dataclass(frozen=True)
class WhenBlock:
    """条件块。与 op 节点互斥——有 when 就没有 op。"""
    cond: Condition
    then: tuple['IROp', ...]
    else_: tuple['IROp', ...] = ()
    elif_: tuple[WhenBranch, ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

IROp = (
    ModOp | HitOp | MarkOp | AbnormalOp | WeatherOp |
    DispelOp | StealOp | TickOp | DoubleOp | ChargeOp |
    EscapeOp | ReturnOp | LockOp | InterruptOp |
    ExchangeOp | ResetOp | RedirectOp | ReplayOp |
    BorrowOp | CountOp | WhenBlock
)
```

### 1.5 顶层

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
    effects: tuple[IROp, ...]       # 已排序、已验证
    description: str
    tag: str
    use_devotion: bool
    usable_while_charging: bool = False
    position_locked: bool = False
```

---

## 二、编译层 — 4-Pass 管线

### 2.1 接口

```python
# backend/vm/compiler/context.py

@dataclass
class CompileError:
    op_index: int
    message: str
    field: str | None = None

@dataclass
class CompilerContext:
    raw: dict                       # 原始 skill JSON
    ir: list[IROp]                  # 当前 IR 列表
    errors: list[CompileError]
    warnings: list[str]
    meta: dict                      # skill_type, power, etc.

class CompilationError(Exception):
    def __init__(self, errors: list[CompileError]):
        self.errors = errors
        super().__init__(f"{len(errors)} compilation error(s)")
```

```python
# backend/vm/compiler/__init__.py

from .passes.parse import ParsePass
from .passes.inject_hit import InjectHitPass
from .passes.validate import ValidatePass
from .passes.sort import SortPass

class SkillCompiler:
    def __init__(self, passes=None):
        self.passes = passes or [
            ParsePass(),
            InjectHitPass(),
            ValidatePass(),
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
```

### 2.2 ParsePass

输入 `CompilerContext(raw=data, ir=[], ...)`，输出 `ctx.ir` 为 typed IROp 列表。

递归处理：普通 effect → 构造对应 IROp；`when` 块 → 递归构造 WhenBlock。

Query 预解析：遇到 `{"q": ..., "of": ...}` 时查 ADDRESS_MAP，把 `(of, q)` 映射到 `field` 字符串存入 Query.field。如果映射不存在，记录 `CompileError`（缺失的 query 留到 ValidatePass 统一报）。

```python
# backend/vm/compiler/passes/parse.py

class ParsePass:
    name = "parse"

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        raw_effects = ctx.raw.get("effects", [])
        ir = []
        for i, eff in enumerate(raw_effects):
            try:
                ir.append(self._parse_one(eff))
            except Exception as e:
                ctx.errors.append(CompileError(i, str(e)))
        ctx.ir = ir
        return ctx

    def _parse_one(self, eff: dict) -> IROp:
        if "when" in eff and "op" not in eff:
            return self._parse_when(eff)
        op = eff["op"]
        parser = getattr(self, f"_parse_{op}", None)
        if parser is None:
            raise ValueError(f"Unknown opcode: {op}")
        return parser(eff)

    def _parse_value(self, v) -> IRValue:
        if isinstance(v, dict) and "q" in v:
            return self._parse_query(v)
        return Literal(v)

    def _parse_query(self, v: dict) -> Query:
        q = v["q"]
        of = v.get("of", "sprite_self")
        field = ADDRESS_MAP[(of, q)]   # KeyError → 上层 catch → CompileError
        return Query(
            field=field,
            name=v.get("name"),
            scale=v.get("scale", 1.0),
            offset=v.get("offset", 0),
            per=v.get("per"),
            default=v.get("default"),
        )

    # _parse_mod, _parse_hit, _parse_mark, ... 每个 op 一个方法
```

### 2.3 InjectHitPass

攻击技能（skill_type ∈ {物攻, 魔攻, 动态攻击} 且 power > 0），若 effects 中没有显式 HitOp，追加一个。

```python
class InjectHitPass:
    name = "inject_hit"
    ATTACK_TYPES = frozenset({"物攻", "魔攻", "动态攻击"})

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        st = ctx.meta["skill_type"]
        power = ctx.meta["power"]
        if st not in self.ATTACK_TYPES or power <= 0:
            return ctx
        has_explicit_hit = any(isinstance(op, HitOp) for op in ctx.ir)
        if has_explicit_hit:
            return ctx
        has_dynamic_power = any(
            isinstance(op, ModOp) and op.stat == "power" and op.feeds == "power"
            for op in ctx.ir
        )
        hit_power = (
            Query(field="power_self")
            if has_dynamic_power
            else Literal(power)
        )
        ctx.ir.append(HitOp(power=hit_power, type=st, feeds="mult"))
        return ctx
```

### 2.4 ValidatePass

编译期拦截所有曾经的运行时错误：

```python
class ValidatePass:
    name = "validate"

    VALID_TARGETS = frozenset({
        "sprite_self", "sprite_opp", "team_own", "team_opp",
        "team_both", "team_own_benched", "team_opp_benched",
        "skill_off_0", "skill_opp_current", "battle",
    })
    VALID_MOD_STATS = frozenset({
        "atk", "def", "sp_atk", "sp_def", "speed", "power",
        "priority", "energy_cost", "combo", "hp", "energy",
        "damage_mult", "damage_reduction", "power_mult",
        "life_drain", "devotion", "ignore_mods", "cooldown",
        # ... 完整列表见 IR spec
    })
    VALID_SCOPE = frozenset({"battlefield", "persistent", "permanent"})
    VALID_COND = frozenset(COND_EVAL.keys())
    VALID_SKILL_FILTER = frozenset({
        "attack", "defense", "status", "all", "others",
        "adjacent", "bare_attack", "bare_defense", "bare_status",
    })

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        for i, op in enumerate(ctx.ir):
            self._validate_op(i, op, ctx)
        return ctx

    def _validate_op(self, i: int, op: IROp, ctx: CompilerContext):
        match op:
            case WhenBlock():
                for sub in op.then:
                    self._validate_op(i, sub, ctx)
                for sub in op.else_:
                    self._validate_op(i, sub, ctx)
                for branch in op.elif_:
                    for sub in branch.then:
                        self._validate_op(i, sub, ctx)
            case ModOp():
                if op.target not in self.VALID_TARGETS:
                    ctx.errors.append(CompileError(i, f"Invalid target: {op.target}", "target"))
                if op.stat not in self.VALID_MOD_STATS:
                    ctx.errors.append(CompileError(i, f"Invalid stat: {op.stat}", "stat"))
                if op.scope not in self.VALID_SCOPE:
                    ctx.errors.append(CompileError(i, f"Invalid scope: {op.scope}", "scope"))
            case HitOp():
                if not op.power:
                    ctx.errors.append(CompileError(i, "HitOp missing power", "power"))
                if op.type not in ("物攻", "魔攻"):
                    ctx.errors.append(CompileError(i, f"Invalid hit type: {op.type}", "type"))
            # ... 每个 op 类型的检查
```

检查清单：

```
✓ opcode      在 ParsePass 支持的 _parse_* 方法中存在
✓ target      在 VALID_TARGETS 集合中
✓ stat        在 VALID_MOD_STATS 集合中（ModOp）
✓ scope       在 VALID_SCOPE 集合中
✓ cond        在 COND_EVAL 中存在
✓ Query       field 非空（ParsePass 已解析）
✓ 必填         HitOp.power/type, MarkOp.name, AbnormalOp.name
✓ 互斥         when 和 op 不同时存在
✓ 值域         ratio ∈ (0,1], stacks ≥ 0, turns ≥ 1
```

### 2.5 SortPass

复用现有 `sort_effects` 逻辑，适配 typed IROp：

```python
class SortPass:
    name = "sort"

    _PHASE = {"cost": 0, "power": 1, "mult": 2, "result": 3,
              "counter": 4, "turn_end": 5}
    _DEFAULT_PHASE = 3

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        if not ctx.ir:
            return ctx
        tagged = [
            (i, self._phase_of(op), -op.priority, op)
            for i, op in enumerate(ctx.ir)
        ]
        tagged.sort(key=lambda x: (x[1], x[2], x[0]))
        ctx.ir = [op for _, _, _, op in tagged]
        return ctx

    def _phase_of(self, op: IROp) -> int:
        feeds = op.feeds
        if feeds and feeds in self._PHASE:
            return self._PHASE[feeds]
        needs = op.needs
        if needs and needs in self._PHASE:
            return self._PHASE[needs]
        return self._DEFAULT_PHASE
```

---

## 三、运行层 — 类型分派

### 3.1 executor 改造

```python
# backend/vm/executor.py

def execute(ctx: Ctx, effects: tuple[IROp, ...]) -> list[Mutation]:
    """VM 入口。effects 已经是排序后的 typed IR。"""
    return process_effects(ctx, effects)

def process_effects(ctx: Ctx, effects: tuple[IROp, ...]) -> list[Mutation]:
    journal: list[Mutation] = []
    for op in effects:
        journal.extend(process_one(ctx, op))
    return journal

def process_one(ctx: Ctx, op: IROp) -> list[Mutation]:
    match op:
        case WhenBlock(cond=cond, then=then, else_=else_, elif_=elif_):
            if eval_one(ctx, cond):
                return process_effects(ctx, then)
            for branch in elif_:
                if eval_one(ctx, branch.cond):
                    return process_effects(ctx, branch.then)
            return process_effects(ctx, else_)

        case ModOp() if op.on_next:
            return _defer_mod(ctx, op)
        case ModOp():
            return op_mod(ctx, op)
        case HitOp():
            return op_hit(ctx, op)
        case MarkOp():
            return op_mark(ctx, op)
        case AbnormalOp():
            return op_abnormal(ctx, op)
        case WeatherOp():
            return op_weather(ctx, op)
        case DispelOp():
            return op_dispel(ctx, op)
        case StealOp():
            return op_steal(ctx, op)
        case TickOp():
            return op_tick(ctx, op)
        case DoubleOp():
            return op_double(ctx, op)
        case ChargeOp():
            return op_charge(ctx, op)
        case EscapeOp():
            return op_escape(ctx, op)
        case ReturnOp():
            return op_return(ctx, op)
        case LockOp():
            return op_lock(ctx, op)
        case InterruptOp():
            return op_interrupt(ctx, op)
        case ExchangeOp():
            return op_exchange(ctx, op)
        case ResetOp():
            return op_reset(ctx, op)
        case RedirectOp():
            return op_redirect(ctx, op)
        case ReplayOp():
            return op_replay(ctx, op)
        case BorrowOp():
            return op_borrow(ctx, op)
        case CountOp():
            return op_count(ctx, op)
        case _:
            raise TypeError(f"Unknown IROp: {type(op)}")
```

### 3.2 OP_DISPATCH 移除

`ops/__init__.py` 中的 `OP_DISPATCH` 字典删除。各 `op_*` handler 签名从 `(ctx, effect: dict)` 改为 `(ctx, op: XxxOp)`。

`"damage": _op_noop` 和 `_op_noop` 函数删除。

### 3.3 resolve 简化

```python
# backend/vm/resolve.py

def resolve(ctx: Ctx, value: IRValue) -> int | float | str | bool:
    if isinstance(value, Literal):
        return value.value
    # value is Query — field 已预解析
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
```

无 ADDRESS_MAP 运行时查找，无 `isinstance(value, dict)` 格式猜测。

### 3.4 cond 适配

`cond.py` 中 handler 签名从 `(ctx, cond: dict)` 改为 `(ctx, params: dict)`：

`eval_one` 从 `CondExpr` 中提取 `cond` key（已验证存在于 COND_EVAL），`params` 传给 handler。handler 不再需要自己取 `cond["ratio"]`，而是直接从 `params["ratio"]` 取。

```python
def eval_one(ctx: Ctx, cond: Condition) -> bool:
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

COND_EVAL 中 handler 对比（以 `hp_below` 为例）：
```python
# 之前：cond["ratio"] — 从顶层 cond dict 取
"hp_below": lambda ctx, cond: (
    _sprite_of(ctx, cond.get("of", "sprite_self"))["hp_ratio"] < cond["ratio"]
),

# 之后：params["ratio"] — 从 params dict 取，cond key 已在 eval_one 层消费
"hp_below": lambda ctx, params: (
    _sprite_of(ctx, params.get("of", "sprite_self"))["hp_ratio"] < params["ratio"]
),
```

---

## 四、删除清单

| 文件 | 删除内容 | 原因 |
|------|---------|------|
| `engine/skill_loader.py` | `_normalize()`, `_KIND_TO_COND`, `_KIND_TO_OP` | 旧格式已全部迁移 |
| `engine/skill_loader.py` | `_inject_hit()`, `_validate()`, `SkillRecord` | 逻辑移入编译器 |
| `vm/executor.py` | `if "kind" in effect: return []` | 死代码 |
| `vm/ops/__init__.py` | `"damage": _op_noop`, `_op_noop()` | 无技能使用 |
| `vm/ops/__init__.py` | `OP_DISPATCH` 字典 | 被 match/case 替代 |

`skill_loader.py` 删除后，`SkillLoader` 职责由 `SkillCompiler` 接管。

**SkillLoader 调用方（需同步更新）：**

| 文件 | 当前用法 | 改为 |
|------|---------|------|
| `engine/battle.py:83-84` | `from backend.engine.skill_loader import SkillLoader` | `from backend.vm.compiler import SkillCompiler` |
| `api/main.py` | 间接触发（通过 Battle） | 不变（Battle 内部改） |
| `tools/test_skills.py` | 直接使用 SkillLoader | 改用 SkillCompiler |
| `engine/test_battle_replay.py` | 直接使用 SkillLoader | 改用 SkillCompiler |

对外 API 变化：`loader.load_all(dir)` → `compiler.compile_all(dir)`，返回 `dict[str, CompiledSkill]`。

---

## 五、文件结构

```
backend/
  vm/
    ir.py                        # 新增：所有 IR 节点定义
    compiler/
      __init__.py                # 新增：SkillCompiler 入口
      context.py                 # 新增：CompilerContext, CompileError
      passes/
        __init__.py
        parse.py                 # ParsePass
        inject_hit.py            # InjectHitPass
        validate.py              # ValidatePass
        sort.py                  # SortPass（适配 typed IR）
    executor.py                  # 改：match/case 类型分派
    resolve.py                   # 改：接受 IRValue（Literal | Query）
    cond.py                      # 改：接受 Condition（CondExpr | And/Or/Not）
    ctx.py                       # 不变
    journal.py                   # 不变
    sort.py                      # 保留（SortPass 引用 _PHASE 常量）
    ops/
      mod.py, hit.py, mark.py, ...  # 改：handler 签名 (ctx, XxxOp)
      __init__.py                # 删：OP_DISPATCH, _op_noop, "damage"
  engine/
    skill_loader.py              # 删：整体被 compiler 替代
    ...
```

---

## 六、测试策略

```
backend/vm/compiler/
  test_parse_pass.py             # 每种 op 的 dict→IROp 转换
  test_parse_query.py            # Query 预解析：q+of → field
  test_inject_hit_pass.py        # 攻击技能补 hit，显式 hit 不重复补
  test_validate_pass.py          # 每种检查项的合法/非法 case
  test_sort_pass.py              # 拓扑排序正确性
  test_compiler_e2e.py           # compile() 端到端，包含 470 技能全量编译
  test_ir_frozen.py              # IR 不可变、可哈希、可 pickle
```

每个 Pass 独立测试，不需要 VM 环境。

---

## 七、迁移步骤

1. **新增** `backend/vm/ir.py` — 所有 IR 节点定义
2. **新增** `backend/vm/compiler/` — 整个编译管线
3. **改造** `backend/vm/executor.py` — match/case 类型分派
4. **改造** `backend/vm/resolve.py` — 接受 IRValue
5. **改造** `backend/vm/cond.py` — 接受 Condition
6. **改造** `backend/vm/ops/*.py` — handler 签名更新
7. **删除** `backend/engine/skill_loader.py` + 相关死代码
8. **更新** 所有调用方（engine/battle.py, api/main.py, tools/test_skills.py）
9. **运行** 全量测试 + 470 技能编译验证
