# 进一步性能优化方案

## 当前状态回顾

**已完成优化**：
- Python层面优化 5次：提速 **2.48x**
- build_ctx: 从 45.98μs 降到 17.11μs
- 快照/恢复: 优化到 24μs

**剩余瓶颈**：
- build_ctx: 47-53% (17.11μs/call)
- 快照/恢复: 12-17% (24μs/call)
- 其他: ~30%

## 🚀 可行的进一步优化方案

### 方案 1: Cython 重写热路径 ⭐⭐⭐

**目标函数**：
- `build_ctx` 完整重写
- `_collect_skill_summary`
- `_compute_speed_self`

**实施方案**：
```python
# backend/engine/snapshot_cy.pyx
cimport cython
from libc.math cimport round as c_round

@cython.boundscheck(False)
@cython.wraparound(False)
cpdef double compute_speed_self(
    dict initial_stats,
    dict modifiers,
    dict stat_stages,
    int speed_step
):
    cdef int base = initial_stats.get("speed", 100)
    cdef int stage = stat_stages.get("speed", 0)
    cdef double speed_mod = modifiers.get("speed", 0.0)
    
    base = max(0, base + stage * speed_step)
    if speed_mod != 0:
        return max(1.0, c_round(base * (1.0 + speed_mod)))
    return <double>base
```

**预期收益**：
- build_ctx 加速 **2-3x**
- 整体 MCTS 加速 **20-30%**

**成本**：
- 实施时间：**2-3 周**
- 需要学习 Cython
- 增加编译步骤
- 维护成本增加

---

### 方案 2: 使用 __slots__ 优化 Ctx ⭐⭐

**问题**：Ctx dataclass 创建占 5.4%

**解决方案**：
```python
@dataclass(slots=True)  # Python 3.10+
class Ctx:
    """添加 __slots__ 减少内存和创建开销"""
    ...
```

**预期收益**：
- Ctx 创建加速 **30-50%**
- 整体 MCTS 加速 **2-3%**

**成本**：
- 实施时间：**1-2 小时**
- 风险：低

---

### 方案 3: 预计算和缓存 Ctx ⭐⭐

**思路**：
- 如果精灵状态没变，Ctx 大部分内容也不变
- 缓存最近的 Ctx，检测变化后复用

**实施**：
```python
class Battle:
    _ctx_cache: dict[tuple, Ctx] = {}
    
    def _make_ctx_cached(self, sprite_a, sprite_b, ...):
        # 计算 cache key（精灵状态哈希）
        key = (sprite_a.current_hp, sprite_a.energy, ...)
        if key in self._ctx_cache:
            return self._ctx_cache[key]
        
        ctx = build_ctx(...)
        self._ctx_cache[key] = ctx
        return ctx
```

**预期收益**：
- 缓存命中时加速 **10-20x**
- 但需要分析命中率
- 整体可能加速 **5-15%**

**成本**：
- 实施时间：**1-2 天**
- 需要验证正确性
- 内存开销增加

---

### 方案 4: 使用 C 扩展重写核心函数 ⭐⭐⭐

**最激进方案**：直接用 C 写核心计算

**示例**：
```c
// snapshot_c.c
#include <Python.h>

static PyObject* compute_speed_self(PyObject* self, PyObject* args) {
    int base_speed, speed_stage, speed_step;
    double speed_mod;
    
    if (!PyArg_ParseTuple(args, "iiid", 
        &base_speed, &speed_stage, &speed_step, &speed_mod))
        return NULL;
    
    int base = base_speed + speed_stage * speed_step;
    if (base < 0) base = 0;
    
    if (speed_mod != 0.0) {
        int result = (int)round(base * (1.0 + speed_mod));
        return PyLong_FromLong(result > 1 ? result : 1);
    }
    return PyLong_FromLong(base);
}
```

**预期收益**：
- 核心函数加速 **5-10x**
- 整体 MCTS 加速 **30-50%**

**成本**：
- 实施时间：**3-4 周**
- 需要 C 编程经验
- 维护成本很高
- 跨平台编译复杂

---

### 方案 5: 并行化 MCTS 模拟 ⭐⭐⭐

**思路**：
- MCTS 的每次模拟是独立的
- 可以并行运行多个模拟

**实施**：
```python
from multiprocessing import Pool

def run_mcts_parallel(root_state, num_simulations, num_workers=4):
    with Pool(num_workers) as pool:
        results = pool.starmap(
            run_single_simulation,
            [(root_state, i) for i in range(num_simulations)]
        )
    return aggregate_results(results)
```

**预期收益**：
- 理想情况：**4x** 加速（4核）
- 实际情况：**2-3x** 加速（开销）

**成本**：
- 实施时间：**1-2 周**
- 需要处理状态同步
- 内存开销增加（每个进程一份）

---

### 方案 6: 算法层优化 ⭐⭐⭐

**不优化代码，优化算法**：

#### 6.1 减少模拟次数
- 更聪明的 MCTS 策略
- 早停（提前判断胜负）
- 自适应模拟次数

#### 6.2 增量更新
- 不完全回滚，只恢复变化部分
- 保留部分计算结果

#### 6.3 剪枝
- 排除明显差的动作
- 使用启发式评估

**预期收益**：
- 可能 **50-200%** 加速
- 但可能影响质量

**成本**：
- 实施时间：**2-4 周**
- 需要研究和实验
- 风险：可能降低 AI 质量

---

### 方案 7: GPU 加速 ⭐

**思路**：
- 将大规模并行计算移到 GPU
- 使用 CUDA 或 PyTorch

**适用场景**：
- 批量状态评估
- 神经网络推理（已经在 GPU）

**预期收益**：
- 如果有大量并行计算：**10-100x**
- 但战斗模拟不太适合 GPU

**成本**：
- 实施时间：**4-6 周**
- 需要 CUDA 编程
- 只在有 GPU 时有效

---

## 📊 方案对比

| 方案 | 预期加速 | 实施时间 | 难度 | 风险 | 推荐度 |
|------|---------|---------|------|------|--------|
| 1. Cython 重写 | 20-30% | 2-3周 | 中 | 中 | ⭐⭐⭐ |
| 2. __slots__ | 2-3% | 1-2小时 | 低 | 低 | ⭐⭐ |
| 3. Ctx 缓存 | 5-15% | 1-2天 | 中 | 中 | ⭐⭐ |
| 4. C 扩展 | 30-50% | 3-4周 | 高 | 高 | ⭐⭐⭐ |
| 5. 并行化 | 2-3x | 1-2周 | 中 | 中 | ⭐⭐⭐ |
| 6. 算法优化 | 50-200% | 2-4周 | 高 | 高 | ⭐⭐⭐ |
| 7. GPU 加速 | ? | 4-6周 | 高 | 高 | ⭐ |

---

## 🎯 推荐的优化路径

### 短期（1周内）

**1. 立即实施 __slots__** ✅
- 工作量小（1-2小时）
- 风险低
- 收益 2-3%

### 中期（1-2个月）

**2. Cython 重写 build_ctx** ⭐⭐⭐
- 最佳性价比
- 收益 20-30%
- 风险可控

**3. 并行化 MCTS** ⭐⭐⭐
- 如果有多核 CPU
- 收益 2-3x
- 实施相对简单

### 长期（2-3个月）

**4. 算法层优化** ⭐⭐⭐
- 最大潜力（50-200%）
- 但需要研究
- 可能影响质量

**5. C 扩展（可选）**
- 如果 Cython 不够
- 最大单一优化（30-50%）
- 但维护成本高

---

## 💰 收益累计估算

**如果全部实施**：
- Python 优化：2.48x ✅ **已完成**
- __slots__: +2-3% → 2.53x
- Cython: +20-30% → 3.16x
- 并行化: +2-3x → 6.32-9.48x
- 算法优化: +50-200% → 9.48-28.44x

**总潜在加速**：**10-30x**

但注意：
- 收益不是简单相加
- 实施成本很高（3-6个月）
- 风险累积

---

## 🎬 立即可以做的

### 快速验证方案

**1. 测试 __slots__ 效果**（今天）
```python
@dataclass(slots=True)
class Ctx:
    ...
```
运行基准测试，看实际收益。

**2. 原型 Cython 函数**（本周）
- 只重写 `_compute_speed_self`
- 测试加速效果
- 如果有效，再扩展

**3. 原型并行化**（下周）
- 简单的多进程 MCTS
- 测试实际加速比
- 评估内存开销

---

## 结论

**立即行动**：
1. ✅ 实施 __slots__（1-2小时）
2. 🔬 原型 Cython（本周）
3. 🔬 原型并行化（下周）

**然后根据原型结果决定**：
- 如果 Cython 效果好 → 全面重写
- 如果并行化效果好 → 完整实施
- 如果都不够 → 考虑 C 扩展或算法优化

**不要一次性全做**：
- 逐步验证
- 控制风险
- 数据驱动决策

需要我先实施 __slots__ 优化并测试效果吗？
