# AI 训练全管线性能分析报告

## 执行总结

基于真实 profile 数据，识别出 AI 训练管线中最值得优化的热点。

## 背景数据

从训练日志可见：
- **自博弈占训练时间：96.6%**
- 训练占时间：3.4%
- 因此优化重点必须在自博弈（MCTS + Battle）

## Profile 分析结果

### 测试 1: IR 技能执行 (1000 次)

**性能指标**:
- 总耗时: 0.263s
- 每次技能: 0.26ms
- 吞吐量: 3,805 skills/s

**Top 10 热点 (按自身时间)**:

| 排名 | 函数 | 自身时间 | 累计时间 | 调用次数 | 单次耗时 |
|------|------|---------|---------|---------|---------|
| 1 | `snapshot.py:build_ctx` | 0.044s | 0.083s | 2,010 | 21.9μs |
| 2 | `trait_loader.py:load_for_sprite` | 0.016s | 0.084s | 2,000 | 8.0μs |
| 3 | `battle.py:__init__` | 0.013s | 0.240s | 1,000 | 13.0μs |
| 4 | `dict.get` | 0.012s | 0.012s | 200,893 | 0.06μs |
| 5 | `snapshot.py:_battle_skill_summary_key` | 0.010s | 0.014s | 4,020 | 2.5μs |
| 6 | `skill_parse.py:_parse_mod` | 0.010s | 0.024s | 2,000 | 5.0μs |
| 7 | `__init__.py:dispatch_entry` | 0.009s | 0.096s | 2,000 | 4.5μs |
| 8 | `observer.py:_index` | 0.007s | 0.042s | 2,000 | 3.5μs |
| 9 | `pathlib.py:parse_parts` | 0.006s | 0.011s | 3,000 | 2.0μs |
| 10 | `builtins.getattr` | 0.005s | 0.005s | 62,353 | 0.08μs |

**关键发现**:
- ✅ `_battle_skill_summary_key` 已优化（技能摘要缓存）
- ⚠️ `build_ctx` 仍是热点（21.9μs × 每次技能执行 2-3 次调用）
- ⚠️ `Battle.__init__` 重复初始化开销大（每次技能执行都创建 Battle？）

---

### 测试 2: build_ctx 调用性能 (10,000 次)

**性能指标**:
- 总耗时: 0.460s
- 每次调用: **45.98μs**
- 吞吐量: 21,751 calls/s

**Top 10 热点 (按自身时间)**:

| 排名 | 函数 | 自身时间 | 累计时间 | 调用次数 | 占比 |
|------|------|---------|---------|---------|-----|
| 1 | `snapshot.py:build_ctx` | **0.189s** | 0.406s | 10,000 | **42.7%** |
| 2 | `snapshot.py:_battle_skill_summary_key` | 0.047s | 0.064s | 20,000 | 10.6% |
| 3 | `dict.get` | 0.028s | 0.028s | 550,000 | 6.3% |
| 4 | `<string>:__init__` (namedtuple) | 0.024s | 0.024s | 10,000 | 5.4% |
| 5 | `builtins.getattr` | 0.021s | 0.026s | 230,000 | 4.7% |
| 6 | `battle.py:_ctx_team_kwargs` | 0.018s | 0.024s | 10,000 | 4.1% |
| 7 | `battle.py:_make_ctx` | 0.015s | 0.445s | 10,000 | 3.4% |
| 8 | `snapshot.py:_collect_skill_summary` | 0.012s | 0.079s | 20,000 | 2.7% |
| 9 | `builtins.hasattr` | 0.008s | 0.019s | 50,000 | 1.8% |
| 10 | `builtins.id` | 0.008s | 0.008s | 160,000 | 1.8% |

**关键发现**:
- 🔥 **`build_ctx` 占总时间 42.7%！** 这是最大瓶颈
- ⚠️ `_battle_skill_summary_key` 虽已优化，但仍占 10.6%（缓存键计算本身有开销）
- ⚠️ namedtuple 创建开销 5.4%（每次都创建新的 Ctx 对象）
- ⚠️ `dict.get` 和 `getattr` 调用频繁（550k + 230k 次）

---

## 优化建议（按优先级）

### 🔥 【最高优先级】build_ctx 深度优化

**当前状况**:
- `build_ctx` 占单次调用 42.7% 时间
- 每次技能执行调用 2-3 次
- 在 MCTS 中被调用数万次

**优化方向 1: Ctx 对象池复用**
```python
# 当前: 每次创建新 namedtuple (5.4% 开销)
return Ctx(
    sprite=sprite,
    sprite_opp=sprite_opp,
    ...  # 40+ 字段
)

# 优化: 对象池 + 可变 Ctx
class MutableCtx:
    __slots__ = ['sprite', 'sprite_opp', ...]  # 40+ 字段
    
# MCTS 模拟期间复用同一个 Ctx 对象，只更新必要字段
```

**预期收益**: 5-10% (消除 namedtuple 创建 + 减少 GC)

**优化方向 2: 懒求值字段**
```python
# 当前: 每次都计算所有字段
ctx = build_ctx(...)  # 计算 40+ 字段

# 优化: 只在访问时计算
class LazyCtx:
    @property
    def team_counters_own(self):
        if not hasattr(self, '_team_counters_own'):
            self._team_counters_own = self._compute_team_counters()
        return self._team_counters_own
```

**预期收益**: 10-15% (很多字段在某些技能中不被使用)

**优化方向 3: 批量属性读取**
```python
# 当前: 大量 getattr (230k 次调用)
sprite.hp
sprite.max_hp
sprite.energy
...

# 优化: 一次性提取到局部变量
hp, max_hp, energy = sprite.hp, sprite.max_hp, sprite.energy
```

**预期收益**: 2-5%

**总预期收益: 15-30%**

---

### 🎯 【高优先级】快照/恢复机制优化

**当前状况**:
- 每次 MCTS 模拟需要 1 次 save + N 次 restore (N = 树深度)
- 典型场景: 200 模拟 × 10 恢复 = 2,000 次恢复操作

**优化方向 1: 增量快照**
```python
# 当前: 每次保存完整状态
snapshot = {
    'player_a': deep_copy(player_a),
    'player_b': deep_copy(player_b),
    'globals': deep_copy(globals),
    ...
}

# 优化: 只保存变更
snapshot = {
    'delta': changed_fields_only,
    'base': reference_to_initial_state
}
```

**预期收益**: 20-30%

**优化方向 2: Copy-on-Write**
```python
# 当前: 恢复时深拷贝所有对象
restore_state(snapshot)  # deep_copy everything

# 优化: 共享不变数据，只拷贝变化部分
class CowSprite:
    def __setattr__(self, name, value):
        if self._shared:
            self._make_exclusive()
        object.__setattr__(self, name, value)
```

**预期收益**: 15-25%

**总预期收益: 30-50%**

---

### 🔧 【中优先级】_battle_skill_summary_key 进一步优化

**当前状况**:
- 已实现缓存，但缓存键计算仍占 10.6%
- 每次调用需要遍历 4 个技能槽 × 6 个字段 = 24 次取值

**优化方向: 预计算缓存键组件**
```python
# 当前: 每次动态计算
def _battle_skill_summary_key(skills):
    key = []
    for sk in skills:
        key.append((
            id(sk),
            id(sk.replaced_by or sk.base),
            sk.nullified,  # ← 每次取属性
            sk._element_override,
            sk._mech_energy_reduction,
            sk._modifiers.get("energy_cost", 0),
        ))
    return tuple(key)

# 优化: 在 BattleSkill 上缓存键片段
class BattleSkill:
    def _invalidate_cache_key(self):
        self._cache_key_fragment = (
            id(self),
            id(self.replaced_by or self.base),
            self.nullified,
            self._element_override,
            self._mech_energy_reduction,
            self._modifiers.get("energy_cost", 0),
        )

def _battle_skill_summary_key(skills):
    return tuple(sk._cache_key_fragment for sk in skills)
```

**预期收益**: 3-5%

---

### 📊 【低优先级】其他微优化

1. **__slots__ 使用**
   - 对热路径类添加 `__slots__`
   - 预期收益: 2-3%

2. **字典操作优化**
   - 减少 `dict.get` 调用（550k 次）
   - 使用局部变量缓存查询结果
   - 预期收益: 1-2%

3. **内置函数调用减少**
   - 减少 `hasattr`/`getattr` 调用
   - 预期收益: 1-2%

---

## 综合优化路线图

### 阶段 1: 快速收益（1-2 天）
1. ✅ **技能摘要缓存** - 已完成（2.29x 加速）
2. **build_ctx 对象池** - 预期 5-10% 收益
3. **批量属性读取** - 预期 2-5% 收益

**阶段目标**: 15-20% 整体提升

### 阶段 2: 深度优化（3-5 天）
1. **build_ctx 懒求值** - 预期 10-15% 收益
2. **增量快照** - 预期 20-30% 收益
3. **_battle_skill_summary_key 预计算** - 预期 3-5% 收益

**阶段目标**: 累计 40-50% 整体提升

### 阶段 3: 架构优化（1-2 周）
1. **Copy-on-Write 快照** - 预期 15-25% 收益
2. **__slots__ 全面应用** - 预期 2-3% 收益
3. **IR 执行内联优化** - 预期 5-10% 收益

**阶段目标**: 累计 60-80% 整体提升

---

## 立即行动建议

### 优先做这个：build_ctx 对象池

**理由**:
1. 收益明显（5-10%）
2. 实现简单（1-2 天）
3. 无风险（不改变语义）
4. 有 profile 数据支撑（42.7% 占比）

**实现步骤**:
```python
# 1. 创建可变 Ctx 类
class MutableCtx:
    __slots__ = [所有字段...]
    
# 2. 在 Battle 上维护 Ctx 池
class Battle:
    def __init__(self):
        self._ctx_pool = MutableCtx()
    
    def _make_ctx(self, ...):
        ctx = self._ctx_pool
        ctx.sprite = sprite
        ctx.sprite_opp = sprite_opp
        ...
        return ctx
```

### 次优选择：增量快照

**理由**:
1. 收益最大（20-30%）
2. 实现复杂度中等（3-5 天）
3. 有明确优化路径

---

## 测试和验证

每个优化后必须：
1. ✅ 运行完整测试套件（463 个测试）
2. ✅ 运行性能基准测试
3. ✅ 对比优化前后的 profile 数据
4. ✅ 验证缓存命中率（如适用）

---

## 总结

基于真实 profile 数据，最值得优化的是：

1. **build_ctx（最高优先级）** - 占时 42.7%，预期收益 15-30%
2. **快照/恢复（高优先级）** - MCTS 核心开销，预期收益 30-50%
3. **_battle_skill_summary_key（中优先级）** - 已优化但仍可提升，预期收益 3-5%

**推荐立即开始：build_ctx 对象池优化**
