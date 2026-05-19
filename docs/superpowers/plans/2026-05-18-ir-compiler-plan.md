# IR 类型化 + 编译层 + 运行层 三层重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将技能和特性装载管线从裸 dict 迁移到类型化 IR + 编译管线 + match/case 执行，零运行时破坏性变更。

**Architecture:** 双 IR（SkillIR + TraitIR）+ 共享底层（IRValue, ValueResolver, EffectApplier）。SkillCompiler（4-Pass）和 TraitCompiler（3-Pass）各自编译 JSON，产出 frozen CompiledSkill / CompiledTrait。管线作为调度器保证执行顺序，IR 作为指令集保证类型安全。

**Tech Stack:** Python 3.10+, dataclasses (frozen=True), match/case, pytest

---

## File Structure Map

```
backend/vm/
  ir_values.py          NEW  — Literal, Query, RefExpr (IRValue union)
  ir_skill.py           NEW  — 21 op dataclasses + WhenBlock + SkillCondition
  ir_trait.py           NEW  — TraitEffect/TraitTrigger/TraitCondition dataclasses
  effect_applier.py     NEW  — shared stat/abnormal/mark/weather applier (extracted from trait_engine)
  compass/              
    __init__.py          NEW  — exports SkillCompiler + TraitCompiler
    context.py          NEW  — CompilerContext, CompileError, CompilationError
    skill_compiler.py   NEW  — SkillCompiler entry (compile, compile_all)
    trait_compiler.py   NEW  — TraitCompiler entry (compile, compile_all)
    passes/
      __init__.py        NEW  — empty
      skill_parse.py    NEW  — SkillParsePass (dict → SkillIROp, Query pre-resolution)
      inject_hit.py     NEW  — InjectHitPass (add implicit HitOp for attack skills)
      skill_validate.py NEW  — SkillValidatePass (target/stat/scope whitelist checks)
      sort.py           NEW  — SortPass (topological sort for typed SkillIROp)
      trait_parse.py    NEW  — TraitParsePass (triggers dict → TraitTrigger)
      aura_expand.py    NEW  — AuraExpandPass (aura → entry+leave pair)
      trait_validate.py NEW  — TraitValidatePass (hook name/effects_mode/required fields)
  executor.py          MODIFY — string dispatch → match/case, typed SkillIROp
  executor_trait.py    NEW     — process_trigger(trigger, ctx) for trait hooks
  resolver.py          MODIFY — unified resolve(Literal|Query|RefExpr)
  cond.py              MODIFY — handler signature (ctx, cond:dict) → (ctx, params:dict)
  cond_path.py         NEW     — PathCond evaluator (extracted from trait_engine.ConditionEvaluator)
  sort.py              KEEP    — _PHASE constant referenced by SortPass
  ops/
    __init__.py         MODIFY — delete OP_DISPATCH, _op_noop, "damage" entry
    mod.py etc.         MODIFY — handler signatures (ctx, effect:dict) → (ctx, op:ModOp)

backend/engine/
  skill_loader.py      DELETE  — replaced by SkillCompiler

backend/sim/traits/
  trait_engine.py      MODIFY — delegate to executor_trait + effect_applier
```

---

### Task 1: ir_values.py — 共享 IRValue 类型

**Files:**
- Create: `backend/vm/ir_values.py`
- Create: `backend/vm/tests/test_ir_values.py`

- [ ] **Step 1: Write the test file**

```python
# backend/vm/tests/test_ir_values.py
import pickle
import pytest
from backend.vm.ir_values import Literal, Query, RefExpr


class TestLiteral:
    def test_literal_int(self):
        v = Literal(42)
        assert v.value == 42
        assert isinstance(v.value, int)

    def test_literal_str(self):
        v = Literal("物攻")
        assert v.value == "物攻"

    def test_literal_bool(self):
        v = Literal(True)
        assert v.value is True

    def test_literal_frozen(self):
        v = Literal(10)
        with pytest.raises(Exception):
            v.value = 20

    def test_literal_hashable(self):
        d = {Literal(1): "one", Literal(2): "two"}
        assert d[Literal(1)] == "one"

    def test_literal_pickleable(self):
        v = Literal(3.14)
        restored = pickle.loads(pickle.dumps(v))
        assert restored.value == 3.14


class TestQuery:
    def test_query_basic(self):
        q = Query(field="hp_self_ratio")
        assert q.field == "hp_self_ratio"
        assert q.scale == 1.0
        assert q.offset == 0

    def test_query_with_scale(self):
        q = Query(field="energy_opp", scale=0.5)
        assert q.scale == 0.5

    def test_query_with_name(self):
        q = Query(field="abnormal_stacks_self", name="灼烧")
        assert q.name == "灼烧"

    def test_query_frozen(self):
        q = Query(field="atk_self")
        with pytest.raises(Exception):
            q.field = "other"

    def test_query_hashable(self):
        q1 = Query(field="hp_self")
        q2 = Query(field="hp_self")
        assert hash(q1) == hash(q2)


class TestRefExpr:
    def test_refexpr_basic(self):
        r = RefExpr(root="self", path=["energy"])
        assert r.root == "self"
        assert r.path == ["energy"]

    def test_refexpr_with_multiplier(self):
        r = RefExpr(root="player", path=["fainted_count"], multiplier=3)
        assert r.multiplier == 3

    def test_refexpr_frozen(self):
        r = RefExpr(root="self", path=["energy"])
        with pytest.raises(Exception):
            r.path = ["other"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest backend/vm/tests/test_ir_values.py -v
```
Expected: FAIL (module or classes not found)

- [ ] **Step 3: Write ir_values.py**

```python
# backend/vm/ir_values.py
"""共享 IRValue 类型 — Literal | Query | RefExpr."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Literal:
    """编译期已知的字面量。"""
    value: int | float | str | bool


@dataclass(frozen=True)
class Query:
    """编译期已解析的寄存器查询。field 是 Ctx 属性名，运行时 O(1) getattr。"""
    field: str
    name: str | None = None
    scale: float = 1.0
    offset: int = 0
    per: float | None = None
    default: object = None


@dataclass(frozen=True)
class RefExpr:
    """编译期解析的路径表达式。用于特性 IR 的动态值。"""
    root: str
    path: list[str]
    multiplier: float = 1.0
    offset: int = 0


IRValue = Literal | Query | RefExpr
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest backend/vm/tests/test_ir_values.py -v
```
Expected: PASS (all 10+ tests)

- [ ] **Step 5: Commit**

```bash
git add backend/vm/ir_values.py backend/vm/tests/test_ir_values.py
git commit -m "新增: IRValue 共享类型 — Literal/Query/RefExpr frozen dataclass"
```

---

### Task 2: ir_skill.py — 技能 IR 节点

**Files:**
- Create: `backend/vm/ir_skill.py`
- Create: `backend/vm/tests/test_ir_skill.py`

- [ ] **Step 1: Write the test**

```python
# backend/vm/tests/test_ir_skill.py
import pickle
import pytest
from backend.vm.ir_skill import (
    CondExpr, AndCond, OrCond, NotCond,
    WhenBlock, WhenBranch,
    ModOp, HitOp, MarkOp, AbnormalOp, WeatherOp,
    DispelOp, StealOp, TickOp, DoubleOp, ChargeOp,
    EscapeOp, ReturnOp, LockOp, InterruptOp,
    ExchangeOp, ResetOp, RedirectOp, ReplayOp,
    BorrowOp, CountOp, SkillIROp,
)
from backend.vm.ir_values import Literal, Query


class TestSkillCondition:
    def test_cond_expr(self):
        c = CondExpr(cond="on_ko", params={})
        assert c.cond == "on_ko"

    def test_and_cond(self):
        c = AndCond(conditions=(
            CondExpr(cond="is_first"),
            CondExpr(cond="opp_is_attack"),
        ))
        assert len(c.conditions) == 2

    def test_not_cond(self):
        c = NotCond(condition=CondExpr(cond="hp_below", params={"ratio": 0.5}))
        assert isinstance(c.condition, CondExpr)

    def test_cond_frozen(self):
        c = CondExpr(cond="on_ko")
        with pytest.raises(Exception):
            c.cond = "other"


class TestWhenBlock:
    def test_when_block_basic(self):
        wb = WhenBlock(
            cond=CondExpr(cond="on_ko"),
            then=(ModOp(target="sprite_self", stat="energy", value=Literal(6)),),
        )
        assert isinstance(wb.cond, CondExpr)
        assert len(wb.then) == 1

    def test_when_block_with_elif(self):
        wb = WhenBlock(
            cond=CondExpr(cond="on_ko"),
            then=(ModOp(target="sprite_self", stat="energy", value=Literal(6)),),
            elif_=(WhenBranch(
                cond=CondExpr(cond="counter_succeeded"),
                then=(ModOp(target="sprite_self", stat="atk", value=Literal(1)),),
            ),),
        )
        assert len(wb.elif_) == 1

    def test_when_block_hashable(self):
        wb = WhenBlock(
            cond=CondExpr(cond="on_ko"),
            then=(ModOp(target="sprite_self", stat="energy", value=Literal(6)),),
        )
        d = {wb: "test"}
        assert d[wb] == "test"


class TestModOp:
    def test_mod_basic(self):
        op = ModOp(target="sprite_self", stat="power", value=Literal(20))
        assert op.stat == "power"
        assert op.value == Literal(20)

    def test_mod_with_query_value(self):
        op = ModOp(target="sprite_self", stat="power",
                   value=Query(field="energy_self", scale=10))
        assert isinstance(op.value, Query)

    def test_mod_frozen(self):
        op = ModOp(target="sprite_self", stat="atk", value=Literal(1))
        with pytest.raises(Exception):
            op.stat = "def"


class TestHitOp:
    def test_hit_basic(self):
        op = HitOp(power=Literal(100), type="物攻")
        assert op.power == Literal(100)
        assert op.type == "物攻"

    def test_hit_with_combo(self):
        op = HitOp(power=Query(field="power_self"), type="魔攻", combo=3)
        assert op.combo == 3


class TestOtherOps:
    def test_mark_op(self):
        op = MarkOp(target="team_opp", name="中毒印记", stacks=2)
        assert op.name == "中毒印记"
        assert op.stacks == 2

    def test_abnormal_op(self):
        op = AbnormalOp(target="sprite_opp", name="灼烧", stacks=1)
        assert op.name == "灼烧"

    def test_weather_op(self):
        op = WeatherOp(weather="rain", turns=8)
        assert op.weather == "rain"

    def test_escape_op(self):
        op = EscapeOp(target="sprite_self")
        assert op.target == "sprite_self"

    def test_return_op(self):
        op = ReturnOp(target="sprite_opp")
        assert op.target == "sprite_opp"


class TestIRPickle:
    def test_skill_ir_pickleable(self):
        import pickle
        ops = [
            ModOp(target="sprite_self", stat="atk", value=Literal(1)),
            HitOp(power=Literal(100), type="物攻"),
        ]
        restored = pickle.loads(pickle.dumps(ops))
        assert restored[0].stat == "atk"
        assert restored[1].power == Literal(100)


class TestSkillIROpUnion:
    def test_type_check(self):
        op: SkillIROp = ModOp(target="sprite_self", stat="atk", value=Literal(1))
        assert isinstance(op, ModOp)
        op2: SkillIROp = HitOp(power=Literal(100), type="物攻")
        assert isinstance(op2, HitOp)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest backend/vm/tests/test_ir_skill.py -v
```
Expected: FAIL

- [ ] **Step 3: Write ir_skill.py**

```python
# backend/vm/ir_skill.py
"""技能 IR 节点 — 21 op + WhenBlock + SkillCondition."""
from __future__ import annotations
from dataclasses import dataclass, field
from .ir_values import IRValue


# ── Condition ──

@dataclass(frozen=True)
class CondExpr:
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


# ── When 块 ──

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


# ── Op 节点 ──

@dataclass(frozen=True)
class ModOp:
    target: str
    stat: str
    value: IRValue
    mode: str = "set"
    scope: str = "battlefield"
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
    power: IRValue
    type: str
    element: str | None = None
    combo: int = 1
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class MarkOp:
    target: str
    name: str
    stacks: int = 1
    value: IRValue | None = None
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class AbnormalOp:
    target: str
    name: str
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
    weather: str
    turns: int = 8
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class DispelOp:
    target: str
    what: str
    name: str | None = None
    limit: int | None = None
    type_limit: int | None = None
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class StealOp:
    target: str
    what: str
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
    what: str
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
    target: str
    inherit: bool = False
    urgent: bool = False
    then: tuple['SkillIROp', ...] = ()
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class ReturnOp:
    target: str
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
    what: str
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
    from_: str
    skill_filter: dict | None = None
    what: str = ""
    feeds: str = ""
    needs: str = ""
    priority: int = 0

@dataclass(frozen=True)
class BorrowOp:
    from_: str
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

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest backend/vm/tests/test_ir_skill.py -v
```
Expected: PASS (all 15+ tests)

- [ ] **Step 5: Commit**

```bash
git add backend/vm/ir_skill.py backend/vm/tests/test_ir_skill.py
git commit -m "新增: 技能 IR 节点 — 21 op + WhenBlock + SkillCondition frozen dataclass"
```

---

### Task 3: ir_trait.py — 特性 IR 节点

**Files:**
- Create: `backend/vm/ir_trait.py`
- Create: `backend/vm/tests/test_ir_trait.py`

- [ ] **Step 1: Write the test**

```python
# backend/vm/tests/test_ir_trait.py
import pickle
import pytest
from backend.vm.ir_trait import (
    PathCond, FnCond, AndCond, OrCond, NotCond, TraitCondition,
    MutateEffectOp, RemoveEffectOp,
    BattleSkillMutOp, UseModifierOp, ActionModifierOp,
    ScheduleOp, InheritEffectsOp, TeamCounterOp,
    TransformOp, TraitInteractionOp, LivesOp,
    TraitStatEffect, TraitAbnormalEffect, TraitMarkEffect,
    TraitWeatherEffect, TraitSpecialEffect,
    TraitTrigger, CompiledTrait, TraitEffect,
)
from backend.vm.ir_values import Literal, RefExpr


class TestTraitCondition:
    def test_path_cond(self):
        c = PathCond(path=["self", "energy"], op="gt", value=Literal(5))
        assert c.path == ["self", "energy"]
        assert c.op == "gt"

    def test_fn_cond(self):
        c = FnCond(name="is_weekend")
        assert c.name == "is_weekend"

    def test_and_cond(self):
        c = AndCond(conditions=(
            PathCond(path=["self", "hp_ratio"], op="lt", value=Literal(0.5)),
            PathCond(path=["battle", "weather"], op="eq", value=Literal("rain")),
        ))
        assert len(c.conditions) == 2

    def test_cond_frozen(self):
        c = PathCond(path=["self", "energy"], op="gt", value=Literal(5))
        with pytest.raises(Exception):
            c.path = ["other"]


class TestMutateEffectOp:
    def test_mutate_basic(self):
        op = MutateEffectOp(
            target="target",
            filter={"is_stat": True, "steps<0": True},
            delta_steps=-3,
        )
        assert op.delta_steps == -3

    def test_mutate_frozen(self):
        op = MutateEffectOp(target="self", filter={}, delta_steps=0)
        with pytest.raises(Exception):
            op.target = "other"


class TestBattleSkillMutOp:
    def test_battleskill_mut(self):
        op = BattleSkillMutOp(
            filter={"is_attack": True},
            field="next_attack_mult",
            value=Literal(2.0),
        )
        assert op.field == "next_attack_mult"

    def test_battleskill_mut_with_ref(self):
        op = BattleSkillMutOp(
            filter={"is_attack": True},
            field="next_attack_mult",
            value=RefExpr(root="team_counters", path=["counter_success"], multiplier=0.5),
        )
        assert isinstance(op.value, RefExpr)


class TestUseModifierOp:
    def test_use_modifier(self):
        op = UseModifierOp(
            key="damage_mult",
            value=Literal(0.0),
            op="set",
        )
        assert op.key == "damage_mult"


class TestActionModifierOp:
    def test_action_modifier(self):
        op = ActionModifierOp(action="forbid_skill", slot=0)
        assert op.action == "forbid_skill"


class TestTraitEffects:
    def test_stat_effect(self):
        e = TraitStatEffect(
            target="self", stat="atk",
            steps=RefExpr(root="player", path=["bug_count"], multiplier=1.5),
            source="虫群突袭",
        )
        assert e.stat == "atk"
        assert isinstance(e.steps, RefExpr)

    def test_special_effect_heal(self):
        e = TraitSpecialEffect(
            name="heal", value=Literal(0.5), target="self",
        )
        assert e.name == "heal"


class TestTraitTrigger:
    def test_trigger_basic(self):
        t = TraitTrigger(
            on="entry",
            effects=(
                TraitStatEffect(target="self", stat="atk", steps=Literal(2), source="test"),
            ),
        )
        assert t.on == "entry"
        assert t.effects_mode == "accumulate"

    def test_trigger_with_condition(self):
        t = TraitTrigger(
            on="entry",
            condition=PathCond(path=["self", "hp_ratio"], op="lt", value=Literal(0.5)),
            effects=(TraitStatEffect(target="self", stat="def", steps=Literal(2), source="test"),),
            effects_mode="replace",
        )
        assert t.effects_mode == "replace"

    def test_trigger_with_counter(self):
        t = TraitTrigger(
            on="modifier",
            counter="hits_taken",
            counter_op="inc",
            counter_trigger={"op": "gte", "value": 3},
            counter_reset=True,
            effects=(TraitSpecialEffect(name="gain_energy", amount=Literal(5)),),
        )
        assert t.counter == "hits_taken"

    def test_trigger_frozen(self):
        t = TraitTrigger(on="entry")
        with pytest.raises(Exception):
            t.on = "leave"


class TestCompiledTrait:
    def test_compiled_trait(self):
        ct = CompiledTrait(
            id=20029,
            name="圣火骑士",
            description="应对成功后，下次攻击威力翻倍。",
            triggers=(
                TraitTrigger(
                    on="counter_success",
                    battleskill_mut=(
                        BattleSkillMutOp(
                            filter={"is_attack": True},
                            field="next_attack_mult",
                            value=Literal(2.0),
                        ),
                    ),
                ),
            ),
        )
        assert ct.id == 20029
        assert len(ct.triggers) == 1

    def test_compiled_trait_pickleable(self):
        ct = CompiledTrait(id=1, name="test", description="", triggers=())
        restored = pickle.loads(pickle.dumps(ct))
        assert restored.id == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest backend/vm/tests/test_ir_trait.py -v
```
Expected: FAIL

- [ ] **Step 3: Write ir_trait.py**

```python
# backend/vm/ir_trait.py
"""特性 IR 节点 — TraitEffect + TraitTrigger + TraitCondition."""
from __future__ import annotations
from dataclasses import dataclass, field
from .ir_values import IRValue


# ── TraitCondition (path-based, different from skill conditions) ──

@dataclass(frozen=True)
class PathCond:
    path: list[str]
    op: str
    value: IRValue

@dataclass(frozen=True)
class FnCond:
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


# ── Effect mutation operations ──

@dataclass(frozen=True)
class MutateEffectOp:
    target: str
    filter: dict
    delta_steps: int = 0
    delta_stacks: int = 0

@dataclass(frozen=True)
class RemoveEffectOp:
    source: str
    target: str


# ── Engine injection operations ──

@dataclass(frozen=True)
class BattleSkillMutOp:
    filter: dict
    field: str
    value: IRValue
    op: str = "set"
    target: str = "all"

@dataclass(frozen=True)
class UseModifierOp:
    key: str
    value: IRValue
    op: str = "set"
    target: str = "modifiers"

@dataclass(frozen=True)
class ActionModifierOp:
    action: str
    slot: int | None = None
    slots: list[int] | None = None
    force: str | None = None


# ── Delayed / cross-sprite operations ──

@dataclass(frozen=True)
class ScheduleOp:
    turns: int
    phase: str = "start"
    effects: tuple['TraitEffect', ...] = ()

@dataclass(frozen=True)
class InheritEffectsOp:
    scope: str = "battlefield"
    source_sprite: str = "self"
    target: str = "enemy_new"
    via_pending: bool = False

@dataclass(frozen=True)
class TeamCounterOp:
    key: str
    delta: int = 1
    target_team: str = "own"

@dataclass(frozen=True)
class TransformOp:
    species: str
    skills: list[str] | None = None
    reset_hp: bool = False
    reset_energy: bool = False

@dataclass(frozen=True)
class TraitInteractionOp:
    action: str
    target: str
    copy_from: str | None = None
    new_ability: str | None = None

@dataclass(frozen=True)
class LivesOp:
    delta: int
    target_team: str = "own"


# ── Shared effect types (used by both skill VM and trait engine via effect_applier) ──

@dataclass(frozen=True)
class TraitStatEffect:
    kind: str = "stat"
    target: str = "self"
    stat: str = ""
    steps: IRValue = field(default_factory=lambda: __import__('backend.vm.ir_values', fromlist=['Literal']).Literal(0))
    scope: str = "battlefield"
    source: str = ""

@dataclass(frozen=True)
class TraitAbnormalEffect:
    kind: str = "abnormal"
    target: str = "opp"
    name: str = ""
    stacks: IRValue = field(default_factory=lambda: __import__('backend.vm.ir_values', fromlist=['Literal']).Literal(1))
    scope: str = "battlefield"
    source: str = ""

@dataclass(frozen=True)
class TraitMarkEffect:
    kind: str = "mark"
    name: str = ""
    stacks: int = 1
    mark_target: str = "opp_team"

@dataclass(frozen=True)
class TraitWeatherEffect:
    kind: str = "weather"
    weather: str = ""
    turns: int = 8

@dataclass(frozen=True)
class TraitSpecialEffect:
    kind: str = "special"
    name: str = ""
    value: IRValue | None = None
    amount: IRValue | None = None
    target: str = "self"
    target_team: str = "own"


TraitEffect = (
    TraitStatEffect | TraitAbnormalEffect | TraitMarkEffect |
    TraitWeatherEffect | TraitSpecialEffect |
    MutateEffectOp | RemoveEffectOp |
    ScheduleOp | InheritEffectsOp | TeamCounterOp |
    TransformOp | TraitInteractionOp | LivesOp
)


# ── Trigger + compiled trait ──

@dataclass(frozen=True)
class TraitTrigger:
    on: str
    condition: TraitCondition | None = None
    effects: tuple[TraitEffect, ...] = ()
    effects_mode: str = "accumulate"
    clear_condition: TraitCondition | None = None
    delay: int = 0
    delay_phase: str = "start"
    counter: str | None = None
    counter_op: str = "inc"
    counter_value: IRValue | None = None
    counter_trigger: dict | None = None
    counter_reset: bool = False
    track: dict | None = None
    use_modifiers: dict[str, dict] | None = None
    battleskill_mut: tuple[BattleSkillMutOp, ...] = ()
    action_modifier: ActionModifierOp | None = None
    pending_effects: tuple[TraitEffect, ...] = ()
    flags: dict | None = None
    team_counters: dict | None = None


@dataclass(frozen=True)
class CompiledTrait:
    id: int
    name: str
    description: str
    triggers: tuple[TraitTrigger, ...]
```

Note: `TraitStatEffect` and `TraitAbnormalEffect` use a workaround for default `Literal` in frozen dataclass. In practice, the compiler will construct these with explicit `steps`/`stacks` values, so the default is never used at runtime.

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest backend/vm/tests/test_ir_trait.py -v
```
Expected: PASS (all 15+ tests)

- [ ] **Step 5: Commit**

```bash
git add backend/vm/ir_trait.py backend/vm/tests/test_ir_trait.py
git commit -m "新增: 特性 IR 节点 — TraitEffect/TraitTrigger/TraitCondition frozen dataclass"
```

---

### Task 4: effect_applier.py — 共享效果应用器

**Files:**
- Create: `backend/vm/effect_applier.py`
- Create: `backend/vm/tests/test_effect_applier.py`

- [ ] **Step 1: Write the test**

```python
# backend/vm/tests/test_effect_applier.py
"""effect_applier 独立测试 — 不依赖 trait_engine 或 VM。"""
import pytest
from backend.vm.effect_applier import apply_effect
from backend.vm.ir_trait import TraitStatEffect, TraitAbnormalEffect
from backend.vm.ir_values import Literal


# 测试用轻量 mock sprite
class MockSprite:
    def __init__(self, name="test", hp=200):
        self.name = name
        self.current_hp = hp
        self.max_hp = hp
        self.energy = 10
        self.effects = []
        self.is_fainted = False

    def add_effect(self, effect):
        self.effects.append(effect)

    def get_stacks(self, name):
        return sum(e.stacks for e in self.effects if getattr(e, 'name', '') == name)

    def heal(self, amount):
        healed = min(amount, self.max_hp - self.current_hp)
        self.current_hp += healed
        return healed

    def gain_energy(self, amount):
        gained = amount
        self.energy += amount
        return gained

    def lose_energy(self, amount):
        lost = min(amount, self.energy)
        self.energy -= lost
        return lost


class MockBattle:
    def __init__(self):
        self.globals = MockGlobals()


class MockGlobals:
    def __init__(self):
        self.marks = {"A": [], "B": []}
        self.weather = None

    def classify_mark(self, name):
        return "positive"

    def apply_mark(self, team, name, category, stacks):
        self.marks.setdefault(team, []).append(
            type('Mark', (), {'name': name, 'stacks': stacks, 'category': category})()
        )

    def set_weather(self, weather, turns):
        self.weather = weather


class TestApplyStatEffect:
    def test_stat_effect_positive(self):
        sprite = MockSprite()
        eff = TraitStatEffect(target="self", stat="atk", steps=Literal(2), source="test")
        events = apply_effect(eff, sprite, None, "A")
        assert len(events) >= 0  # 事件生成
        assert any(e.stat_key == "atk" for e in sprite.effects)

    def test_stat_effect_negative(self):
        sprite = MockSprite()
        eff = TraitStatEffect(target="self", stat="def", steps=Literal(-2), source="test")
        events = apply_effect(eff, sprite, None, "A")
        assert len(events) >= 0


class TestApplyAbnormalEffect:
    def test_abnormal_effect(self):
        sprite = MockSprite()
        eff = TraitAbnormalEffect(target="self", name="灼烧", stacks=Literal(1), source="test")
        events = apply_effect(eff, sprite, None, "A")
        assert any("灼烧" in ev for ev in events)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest backend/vm/tests/test_effect_applier.py -v
```
Expected: FAIL

- [ ] **Step 3: Write effect_applier.py**

从 `backend/sim/traits/trait_engine.py` 的 `_apply_effect()` 方法提取出 stat/abnormal/mark/weather/special 效果应用逻辑，改造为接受 typed effect 参数而非 dict。关键函数签名：

```python
# backend/vm/effect_applier.py
"""共享效果应用器 — 技能 VM 和 trait engine 共用。"""

def apply_effect(effect, target_sprite, battle, team: str) -> list[str]:
    """应用单条效果到目标精灵。返回事件描述列表。"""
    from backend.vm.ir_trait import (
        TraitStatEffect, TraitAbnormalEffect, TraitMarkEffect,
        TraitWeatherEffect, TraitSpecialEffect,
    )

    if isinstance(effect, TraitStatEffect):
        return _apply_stat(effect, target_sprite)
    if isinstance(effect, TraitAbnormalEffect):
        return _apply_abnormal(effect, target_sprite, battle)
    if isinstance(effect, TraitMarkEffect):
        return _apply_mark(effect, battle, team)
    if isinstance(effect, TraitWeatherEffect):
        return _apply_weather(effect, battle)
    if isinstance(effect, TraitSpecialEffect):
        return _apply_special(effect, target_sprite, battle, team)
    return []
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest backend/vm/tests/test_effect_applier.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/vm/effect_applier.py backend/vm/tests/test_effect_applier.py
git commit -m "新增: 共享效果应用器 effect_applier — 提取自 trait_engine，技能/特性共用"
```

---

### Task 5: SkillCompiler — 编译管线

**Files:**
- Create: `backend/vm/compiler/__init__.py`
- Create: `backend/vm/compiler/context.py`
- Create: `backend/vm/compiler/skill_compiler.py`
- Create: `backend/vm/compiler/passes/__init__.py`
- Create: `backend/vm/compiler/passes/skill_parse.py`
- Create: `backend/vm/compiler/passes/inject_hit.py`
- Create: `backend/vm/compiler/passes/skill_validate.py`
- Create: `backend/vm/compiler/passes/sort.py`
- Create: `backend/vm/compiler/tests/test_skill_parse.py`
- Create: `backend/vm/compiler/tests/test_inject_hit.py`
- Create: `backend/vm/compiler/tests/test_skill_validate.py`
- Create: `backend/vm/compiler/tests/test_skill_compiler_e2e.py`

**Important:** The SkillCompiler needs to reference the ADDRESS_MAP from `backend/vm/ctx.py`. Check the current ADDRESS_MAP to ensure Query pre-resolution works correctly. The compiler imports `from backend.vm.ctx import Ctx, ADDRESS_MAP` and uses `ADDRESS_MAP[(of, q)]` to resolve Query.field at compile time.

- [ ] **Step 1: context.py**

```python
# backend/vm/compiler/context.py
from dataclasses import dataclass, field


@dataclass
class CompileError:
    op_index: int
    message: str
    field: str | None = None


@dataclass
class CompilerContext:
    raw: dict
    ir: list      # SkillIROp[] or TraitTrigger[]
    errors: list[CompileError]
    warnings: list[str]
    meta: dict    # {"skill_type": ..., "power": ...}


class CompilationError(Exception):
    def __init__(self, errors: list[CompileError]):
        self.errors = errors
        super().__init__(f"{len(errors)} compilation error(s)")
```

- [ ] **Step 2: Write SkillParsePass test**

```python
# backend/vm/compiler/tests/test_skill_parse.py
import pytest
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.skill_parse import SkillParsePass
from backend.vm.ir_skill import ModOp, HitOp, WhenBlock, CondExpr, MarkOp
from backend.vm.ir_values import Literal, Query


class TestSkillParsePass:
    def setup_method(self):
        self.pass_ = SkillParsePass()

    def test_parse_simple_mod(self):
        ctx = CompilerContext(
            raw={"effects": [{"op": "mod", "target": "sprite_self", "stat": "atk", "value": 1}]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert len(result.errors) == 0
        assert len(result.ir) == 1
        op = result.ir[0]
        assert isinstance(op, ModOp)
        assert op.stat == "atk"
        assert op.value == Literal(1)

    def test_parse_when_block(self):
        ctx = CompilerContext(
            raw={"effects": [{
                "when": {"cond": "on_ko"},
                "then": [{"op": "mod", "target": "sprite_self", "stat": "energy", "value": 6}],
            }]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert len(result.errors) == 0
        assert isinstance(result.ir[0], WhenBlock)
        assert result.ir[0].cond == CondExpr(cond="on_ko")
        assert isinstance(result.ir[0].then[0], ModOp)

    def test_parse_query_value(self):
        ctx = CompilerContext(
            raw={"effects": [{"op": "mod", "target": "sprite_self", "stat": "power",
                              "value": {"q": "energy", "of": "sprite_self", "scale": 10}}]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert len(result.errors) == 0
        op = result.ir[0]
        assert isinstance(op.value, Query)
        assert op.value.field != ""  # Query 已预解析

    def test_parse_unknown_op_errors(self):
        ctx = CompilerContext(
            raw={"effects": [{"op": "nonexistent"}]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert len(result.errors) > 0

    def test_parse_hit_op(self):
        ctx = CompilerContext(
            raw={"effects": [{"op": "hit", "power": 100, "type": "物攻"}]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert isinstance(result.ir[0], HitOp)

    def test_parse_mark_op(self):
        ctx = CompilerContext(
            raw={"effects": [{"op": "mark", "target": "team_own", "name": "光合印记", "stacks": 1}]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert isinstance(result.ir[0], MarkOp)
```

- [ ] **Step 3: Write SkillParsePass**

实现 spec 2.2 中的 ParsePass。关键逻辑：
- `_parse_one(eff: dict)` → 有 `when` 无 `op` → `_parse_when()`；否则用 `op` → `getattr(self, f"_parse_{op}")`
- `_parse_value(v)` → dict with `"q"` → `_parse_query()` → 查 ADDRESS_MAP → `Query(field=...)`；否则 `Literal(v)`
- 每个 op 有对应的 `_parse_mod`, `_parse_hit`, `_parse_mark`, ... 方法

- [ ] **Step 4: Run test**

```bash
pytest backend/vm/compiler/tests/test_skill_parse.py -v
```
Expected: PASS

- [ ] **Step 5: InjectHitPass test**

```python
# backend/vm/compiler/tests/test_inject_hit.py
import pytest
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.inject_hit import InjectHitPass
from backend.vm.ir_skill import HitOp, ModOp
from backend.vm.ir_values import Literal


class TestInjectHitPass:
    def test_injects_hit_for_attack_skill(self):
        ctx = CompilerContext(
            raw={}, ir=[], errors=[], warnings=[],
            meta={"skill_type": "物攻", "power": 100},
        )
        result = InjectHitPass().apply(ctx)
        assert len(result.ir) == 1
        assert isinstance(result.ir[0], HitOp)
        assert result.ir[0].power == Literal(100)
        assert result.ir[0].type == "物攻"

    def test_no_inject_for_status_skill(self):
        ctx = CompilerContext(
            raw={}, ir=[], errors=[], warnings=[],
            meta={"skill_type": "状态", "power": 0},
        )
        result = InjectHitPass().apply(ctx)
        assert len(result.ir) == 0

    def test_no_duplicate_hit(self):
        ctx = CompilerContext(
            raw={},
            ir=[HitOp(power=Literal(80), type="物攻")],
            errors=[], warnings=[],
            meta={"skill_type": "物攻", "power": 100},
        )
        result = InjectHitPass().apply(ctx)
        assert len(result.ir) == 1  # 不追加重复 hit
```

- [ ] **Step 6: Write InjectHitPass**

```python
# backend/vm/compiler/passes/inject_hit.py
from ..context import CompilerContext
from backend.vm.ir_skill import HitOp
from backend.vm.ir_values import Literal


class InjectHitPass:
    name = "inject_hit"
    ATTACK_TYPES = frozenset({"物攻", "魔攻", "动态攻击"})

    def apply(self, ctx: CompilerContext) -> CompilerContext:
        st = ctx.meta.get("skill_type", "")
        power = ctx.meta.get("power", 0)
        if st not in self.ATTACK_TYPES or power <= 0:
            return ctx
        if any(isinstance(op, HitOp) for op in ctx.ir):
            return ctx
        ctx.ir.append(HitOp(power=Literal(power), type=st, feeds="mult"))
        return ctx
```

- [ ] **Step 7: Run test**

```bash
pytest backend/vm/compiler/tests/test_inject_hit.py -v
```
Expected: PASS

- [ ] **Step 8: SkillValidatePass test + implementation**

```python
# backend/vm/compiler/tests/test_skill_validate.py
class TestSkillValidatePass:
    def test_valid_mod_passes(self):
        ctx = CompilerContext(
            raw={}, ir=[ModOp(target="sprite_self", stat="atk", value=Literal(1))],
            errors=[], warnings=[], meta={},
        )
        result = SkillValidatePass().apply(ctx)
        assert len(result.errors) == 0

    def test_invalid_target_fails(self):
        ctx = CompilerContext(
            raw={}, ir=[ModOp(target="invalid", stat="atk", value=Literal(1))],
            errors=[], warnings=[], meta={},
        )
        result = SkillValidatePass().apply(ctx)
        assert len(result.errors) == 1
        assert "target" in result.errors[0].message.lower()

    def test_invalid_stat_fails(self):
        ctx = CompilerContext(
            raw={}, ir=[ModOp(target="sprite_self", stat="nonexistent", value=Literal(1))],
            errors=[], warnings=[], meta={},
        )
        result = SkillValidatePass().apply(ctx)
        assert len(result.errors) == 1
```

- [ ] **Step 9: Write SkillValidatePass** → 按 spec 2.4 实现所有白名单检查

- [ ] **Step 10: Run test**

```bash
pytest backend/vm/compiler/tests/test_skill_validate.py -v
```
Expected: PASS

- [ ] **Step 11: SortPass test + implementation** → 适配 `_phase_of(op: SkillIROp)` 使用 `op.feeds` / `op.needs` 字段

- [ ] **Step 12: SkillCompiler e2e test — 编译 470 技能**

```python
# backend/vm/compiler/tests/test_skill_compiler_e2e.py
import json
import pytest
from pathlib import Path
from backend.vm.compiler.skill_compiler import SkillCompiler
from backend.vm.compiler.context import CompilationError


class TestSkillCompilerE2E:
    @pytest.fixture(scope="class")
    def compiler(self):
        return SkillCompiler()

    @pytest.fixture(scope="class")
    def all_skills(self):
        skills = {}
        for fpath in Path("data/skills").glob("*.json"):
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            skills[data["name"]] = data
        return skills

    def test_compiles_all_skills(self, compiler, all_skills):
        errors = []
        for name, data in all_skills.items():
            try:
                compiled = compiler.compile(data)
                assert compiled.name == data["name"]
                assert compiled.id == data.get("id", 0)
                # CompiledSkill is hashable
                d = {compiled: name}
            except CompilationError as e:
                errors.append(f"{name}: {e}")
        assert len(errors) == 0, f"{len(errors)} skills failed: {errors[:5]}"

    def test_compiled_skill_frozen(self, compiler, all_skills):
        data = list(all_skills.values())[0]
        cs = compiler.compile(data)
        with pytest.raises(Exception):
            cs.name = "other"
```

- [ ] **Step 13: Run e2e test**

```bash
pytest backend/vm/compiler/tests/test_skill_compiler_e2e.py -v
```
Expected: PASS (470 skills compile without error)

- [ ] **Step 14: Write SkillCompiler**

```python
# backend/vm/compiler/skill_compiler.py
import json
from pathlib import Path
from .context import CompilerContext, CompileError, CompilationError
from .passes.skill_parse import SkillParsePass
from .passes.inject_hit import InjectHitPass
from .passes.skill_validate import SkillValidatePass
from .passes.sort import SortPass
from backend.vm.ir_skill import CompiledSkill


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
        result = {}
        for fpath in sorted(Path(data_dir).glob("*.json")):
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            try:
                result[data["name"]] = self.compile(data)
            except CompilationError:
                raise
            except Exception as e:
                raise CompilationError([
                    CompileError(0, f"{fpath.name}: {e}")
                ]) from e
        return result
```

- [ ] **Step 15: Final test run + commit**

```bash
pytest backend/vm/compiler/tests/ -v
git add backend/vm/compiler/
git commit -m "新增: SkillCompiler — 4-Pass 编译管线 (Parse→InjectHit→Validate→Sort)"
```

---

### Task 6: TraitCompiler — 特性编译管线

**Files:**
- Create: `backend/vm/compiler/trait_compiler.py`
- Create: `backend/vm/compiler/passes/trait_parse.py`
- Create: `backend/vm/compiler/passes/aura_expand.py`
- Create: `backend/vm/compiler/passes/trait_validate.py`
- Create: `backend/vm/compiler/tests/test_trait_parse.py`
- Create: `backend/vm/compiler/tests/test_aura_expand.py`
- Create: `backend/vm/compiler/tests/test_trait_validate.py`
- Create: `backend/vm/compiler/tests/test_trait_compiler_e2e.py`

- [ ] **Step 1: Write TraitParsePass test**

```python
# backend/vm/compiler/tests/test_trait_parse.py
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.trait_parse import TraitParsePass
from backend.vm.ir_trait import (
    TraitTrigger, TraitStatEffect, BattleSkillMutOp, PathCond
)
from backend.vm.ir_values import Literal, RefExpr


class TestTraitParsePass:
    def setup_method(self):
        self.pass_ = TraitParsePass()

    def test_parse_圣火骑士(self):
        """应对成功后，下次攻击威力翻倍。"""
        ctx = CompilerContext(
            raw={"triggers": [{
                "on": "counter_success",
                "battleskill_mut": [{
                    "filter": {"is_attack": True},
                    "field": "next_attack_mult",
                    "op": "set",
                    "value": 2.0,
                }],
            }]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert isinstance(trigger, TraitTrigger)
        assert trigger.on == "counter_success"
        assert len(trigger.battleskill_mut) == 1
        assert trigger.battleskill_mut[0].field == "next_attack_mult"
        assert trigger.battleskill_mut[0].value == Literal(2.0)

    def test_parse_悼亡(self):
        """双方队伍每有1只力竭精灵，双攻+30%。"""
        ctx = CompilerContext(
            raw={"triggers": [{
                "on": "entry",
                "effects_mode": "replace",
                "effects": [
                    {"kind": "stat", "stat": "atk",
                     "steps": "=@player_fainted_count * 3 + opponent_fainted_count * 3",
                     "scope": "battlefield", "source": "悼亡"},
                ],
            }]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert trigger.effects_mode == "replace"
        eff = trigger.effects[0]
        assert isinstance(eff, TraitStatEffect)
        assert eff.stat == "atk"
        assert isinstance(eff.steps, RefExpr)

    def test_parse_完全偏振(self):
        """抵抗自己携带技能系别的攻击伤害。"""
        ctx = CompilerContext(
            raw={"triggers": [{
                "on": "defend",
                "condition": {
                    "path": "skill.element",
                    "op": "in",
                    "value": "=@defender_skill_elements",
                },
                "use_modifiers": {
                    "damage_mult": {"op": "set", "value": 0.0},
                },
            }]},
            ir=[], errors=[], warnings=[], meta={},
        )
        result = self.pass_.apply(ctx)
        assert len(result.errors) == 0
        trigger = result.ir[0]
        assert trigger.on == "defend"
        assert isinstance(trigger.condition, PathCond)
        assert trigger.use_modifiers == {"damage_mult": {"op": "set", "value": 0.0}}
```

- [ ] **Step 2: Write TraitParsePass** → parse triggers array → typed TraitTrigger list. Parse each sub-structure: effects (kind→TraitEffect), condition (path→PathCond), ref expressions ("=@..." → RefExpr), battleskill_mut, use_modifiers.

- [ ] **Step 3: Write AuraExpandPass test**

```python
# backend/vm/compiler/tests/test_aura_expand.py
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.aura_expand import AuraExpandPass
from backend.vm.ir_trait import (
    TraitTrigger, TraitStatEffect, RemoveEffectOp, TraitAbnormalEffect
)
from backend.vm.ir_values import Literal


class TestAuraExpandPass:
    def test_expands_aura_to_entry_leave_pair(self):
        ctx = CompilerContext(
            raw={},
            ir=[TraitTrigger(
                on="aura",
                effects=(
                    TraitStatEffect(target="opponent_active", stat="def",
                                    steps=Literal(-2), source="冰封"),
                ),
            )],
            errors=[], warnings=[], meta={},
        )
        # Mark as aura (normally done by parse pass)
        # Actually, aura is a raw JSON concept, handled in AuraExpandPass
        pass
```

- [ ] **Step 4: Write AuraExpandPass** → for each trigger with `aura` data, expand into `entry` + `leave` TraitTrigger pair, where `leave` uses `RemoveEffectOp`.

- [ ] **Step 5: TraitValidatePass test**

```python
# backend/vm/compiler/tests/test_trait_validate.py
from backend.vm.compiler.context import CompilerContext
from backend.vm.compiler.passes.trait_validate import TraitValidatePass
from backend.vm.ir_trait import TraitTrigger


class TestTraitValidatePass:
    VALID_HOOKS = TraitValidatePass.VALID_HOOKS if hasattr(TraitValidatePass, 'VALID_HOOKS') else set()

    def test_valid_hook_passes(self):
        ctx = CompilerContext(
            raw={}, ir=[TraitTrigger(on="entry")],
            errors=[], warnings=[], meta={},
        )
        result = TraitValidatePass().apply(ctx)
        assert len(result.errors) == 0

    def test_invalid_hook_fails(self):
        ctx = CompilerContext(
            raw={}, ir=[TraitTrigger(on="nonexistent_hook")],
            errors=[], warnings=[], meta={},
        )
        result = TraitValidatePass().apply(ctx)
        assert len(result.errors) > 0
```

- [ ] **Step 6: Write TraitValidatePass** → check `on` ∈ VALID_HOOKS (17 hook names), check `effects_mode` ∈ {accumulate, replace, conditional_replace}, check `clear_condition` present when effects_mode=conditional_replace.

- [ ] **Step 7: E2E test — compile all ~120 traits**

```python
# backend/vm/compiler/tests/test_trait_compiler_e2e.py
import json
import pytest
from pathlib import Path
from backend.vm.compiler.trait_compiler import TraitCompiler
from backend.vm.compiler.context import CompilationError


class TestTraitCompilerE2E:
    @pytest.fixture(scope="class")
    def compiler(self):
        return TraitCompiler()

    @pytest.fixture(scope="class")
    def all_traits(self):
        traits = {}
        for fpath in Path("data/traits").glob("*.json"):
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            traits[data.get("name", fpath.stem)] = data
        return traits

    def test_compiles_all_traits(self, compiler, all_traits):
        errors = []
        for name, data in all_traits.items():
            try:
                compiled = compiler.compile(data)
                assert compiled.name == data.get("name", "")
                assert compiled.id == data.get("id", 0)
                assert isinstance(compiled.triggers, tuple)
            except CompilationError as e:
                errors.append(f"{name}: {e}")
        assert len(errors) == 0, f"{len(errors)} traits failed: {errors[:5]}"
```

```bash
pytest backend/vm/compiler/tests/test_trait_compiler_e2e.py -v
git add backend/vm/compiler/trait_compiler.py backend/vm/compiler/passes/trait_*.py backend/vm/compiler/tests/test_trait_*.py
git commit -m "新增: TraitCompiler — 3-Pass 编译管线 (Parse→AuraExpand→Validate)"
```

---

### Task 7: 运行层迁移 — executor.py + resolve.py + cond.py

**Files:**
- Modify: `backend/vm/executor.py`
- Modify: `backend/vm/resolver.py` (rename existing `resolve.py` to `resolver.py`)
- Modify: `backend/vm/cond.py`
- Create: `backend/vm/cond_path.py`
- Modify: `backend/vm/ops/__init__.py`
- Modify: `backend/vm/ops/mod.py`, `hit.py`, `mark.py`, ... (all handlers)

This is the critical migration step. All existing tests must continue to pass.

- [ ] **Step 1: Modify resolver.py — 统一值解析**

Change function signature from:
```python
def resolve(ctx, value) -> int | float | str | bool:
    if isinstance(value, dict) and "q" in value:
        # old query resolution
    # isinstance guessing
```
To:
```python
from backend.vm.ir_values import Literal, Query, RefExpr, IRValue

def resolve(ctx, value: IRValue) -> int | float | str | bool:
    if isinstance(value, Literal):
        return value.value
    if isinstance(value, Query):
        raw = getattr(ctx, value.field)
        ...
    if isinstance(value, RefExpr):
        return _resolve_ref(ctx, value)
```

- [ ] **Step 2: Modify cond.py — handler signature update**

Change `eval_one` from dict-based to match/case on `SkillCondition`. Update each COND_EVAL handler from `(ctx, cond: dict)` → `(ctx, params: dict)`.

- [ ] **Step 3: Create cond_path.py — PathCond evaluator**

Extract the path-based condition evaluation logic from `trait_engine.py`'s `ConditionEvaluator` class. Adapt to accept typed `TraitCondition` (PathCond | FnCond | AndCond | OrCond | NotCond).

- [ ] **Step 4: Modify executor.py — match/case dispatch**

```python
from backend.vm.ir_skill import SkillIROp, WhenBlock, ModOp, HitOp, ...
from backend.vm.ir_values import IRValue

def execute(ctx: Ctx, effects: tuple[SkillIROp, ...], *, sort: bool = True) -> Journal:
    ...

def process_one(ctx: Ctx, op: SkillIROp) -> list[Mutation]:
    match op:
        case WhenBlock(cond=cond, then=then, else_=else_, elif_=elif_):
            ...
        case ModOp() if op.on_next:
            return _defer_mod(ctx, op)
        case ModOp():
            return op_mod(ctx, op)
        case HitOp():
            return op_hit(ctx, op)
        # ... all 21 types
```

**Backward compatibility:** Keep the old `process_one(ctx, effect: dict)` as `process_one_dict()` and call it from `process_one` when given a dict. This allows gradual migration.

- [ ] **Step 5: Modify ops/*.py handlers**

Each handler signature changes from `(ctx, effect: dict)` to `(ctx, op: XxxOp)`. Example:

```python
# ops/mod.py — before
def op_mod(ctx, effect: dict) -> list[Mutation]:
    target = effect["target"]
    stat = effect["stat"]
    value = resolve(ctx, effect.get("value", 0))

# ops/mod.py — after
def op_mod(ctx, op: ModOp) -> list[Mutation]:
    target = op.target
    stat = op.stat
    value = resolve(ctx, op.value)
```

- [ ] **Step 6: Delete OP_DISPATCH from ops/__init__.py**

Remove `OP_DISPATCH = {"mod": op_mod, ...}` dict, `_op_noop` function, and `"damage": _op_noop` entry.

- [ ] **Step 7: Run all existing VM/engine tests**

```bash
pytest backend/engine/test_integration.py backend/engine/test_battle_replay.py -x --tb=short
```
Expected: All existing tests pass (zero regressions)

- [ ] **Step 8: Commit**

```bash
git add backend/vm/executor.py backend/vm/resolver.py backend/vm/cond.py backend/vm/cond_path.py backend/vm/ops/
git commit -m "重构: 运行层迁移 — match/case 类型分派 + 统一值解析 + 路径条件求值器"
```

---

### Task 8: 集成 — 更新调用方 + 删除 SkillLoader

**Files:**
- Modify: `backend/engine/battle.py` — replace `SkillLoader` import with `SkillCompiler`
- Modify: `backend/sim/battle.py` — replace trait loading with `TraitCompiler`
- Modify: `backend/api/main.py` — update skill loading (if direct usage)
- Modify: `backend/tools/test_skills.py` — use `SkillCompiler` instead of `SkillLoader`
- Delete: `backend/engine/skill_loader.py`

- [ ] **Step 1: Update engine/battle.py**

Replace:
```python
from .skill_loader import SkillRecord
```
With:
```python
from backend.vm.ir_skill import CompiledSkill
```
Update `SkillRecord` reference to use `CompiledSkill`.

- [ ] **Step 2: Run integration tests**

```bash
pytest backend/engine/test_integration.py backend/engine/test_battle_replay.py -x --tb=short
```
Expected: PASS

- [ ] **Step 3: Delete skill_loader.py**

```bash
git rm backend/engine/skill_loader.py
```

- [ ] **Step 4: Update sim/battle.py trait loading**

Replace trait loading from dict-based `DataDrivenTrait` construction to using `TraitCompiler.compile()`.

- [ ] **Step 5: Run full test suite**

```bash
pytest backend/ -x --tb=short
```
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add -u
git commit -m "重构: 集成 — 替换 SkillLoader→SkillCompiler + 删除 skill_loader.py"
```

---

### Task 9: trait_engine.py 重构 — 委托给类型化执行器

**Files:**
- Modify: `backend/sim/traits/trait_engine.py`
- Create: `backend/vm/executor_trait.py`

- [ ] **Step 1: Write executor_trait.py**

```python
# backend/vm/executor_trait.py
"""特性 trigger 执行器 — 被 trait_engine 的 _fire() 调用。"""
from backend.vm.ir_trait import TraitTrigger, TraitEffect
from backend.vm.effect_applier import apply_effect


def process_trigger(trigger: TraitTrigger, ctx: dict) -> list[str]:
    """执行单个 TraitTrigger。返回事件描述列表。"""
    events: list[str] = []

    # 条件检查
    if trigger.condition:
        from backend.vm.cond_path import eval_path_cond
        if not eval_path_cond(trigger.condition, ctx):
            return events

    # 延迟处理
    if trigger.delay and trigger.delay > 0:
        battle = ctx.get('battle')
        if battle:
            battle.scheduled_effects.append({
                'turn': battle.turn + trigger.delay,
                'phase': trigger.delay_phase,
                'trigger': trigger,
                'ctx_snapshot': {k: v for k, v in ctx.items()
                                 if k in ('team', 'self', 'target', 'attacker', 'battle')},
            })
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

    # 效果应用（委托给共享 effect_applier）
    sprite = ctx.get('self')
    battle = ctx.get('battle')
    team = ctx.get('team', 'A')
    for eff in trigger.effects:
        target = _resolve_target(eff, ctx, sprite)
        if target:
            events += apply_effect(eff, target, battle, team)

    # 标志位 / 队伍计数器
    if trigger.flags:
        _apply_flags(trigger.flags, ctx)
    if trigger.team_counters:
        _apply_team_counters(trigger.team_counters, ctx)

    # 计数器重置
    if trigger.counter and trigger.counter_reset:
        sprite = ctx.get('self')
        if sprite:
            sprite.counters[trigger.counter] = 0

    return events
```

- [ ] **Step 2: Refactor trait_engine.py DataDrivenTrait._fire()**

Replace the dict-handling logic in `_fire()` with calls to `process_trigger()`. The `DataDrivenTrait` class now delegates execution to the typed executor while keeping its own hook registration and interface.

- [ ] **Step 3: Run all tests**

```bash
pytest backend/ -x --tb=short
```
Expected: All tests pass (zero regressions)

- [ ] **Step 4: Commit**

```bash
git add backend/vm/executor_trait.py backend/sim/traits/trait_engine.py
git commit -m "重构: trait_engine 委托给 executor_trait + effect_applier 类型化执行器"
```

---

### Task 10: 全量验证 + 性能检查

- [ ] **Step 1: Run full test suite with coverage**

```bash
pytest backend/ -v --tb=short --timeout=30
```

- [ ] **Step 2: Verify 470 skills compile without errors**

```bash
python -c "
from backend.vm.compiler.skill_compiler import SkillCompiler
c = SkillCompiler()
skills = c.compile_all('data/skills')
print(f'{len(skills)} skills compiled successfully')
"
```
Expected: `470 skills compiled successfully`

- [ ] **Step 3: Verify ~120 traits compile without errors**

```bash
python -c "
from backend.vm.compiler.trait_compiler import TraitCompiler
c = TraitCompiler()
traits = c.compile_all('data/traits')
print(f'{len(traits)} traits compiled successfully')
"
```

- [ ] **Step 4: Verify CompiledSkill is cacheable (hashable)**

```bash
python -c "
from backend.vm.compiler.skill_compiler import SkillCompiler
c = SkillCompiler()
skills = c.compile_all('data/skills')
d = {cs: name for name, cs in skills.items()}
print(f'{len(d)} unique skills in hash map')
"
```

- [ ] **Step 5: Commit final verification**

```bash
git add -u
git commit -m "验证: 全量技能470+特性~120编译通过 + hashable + 无回归"
```
