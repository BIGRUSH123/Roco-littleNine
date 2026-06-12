# MCTS 并行化性能问题分析

## 🚨 问题：并行版本比串行慢 25 倍！

### 测试结果

- **串行耗时**: 0.080s
- **并行耗时**: 1.902s  
- **加速比**: 0.04x（**慢了 25 倍**）

### 🔍 原因分析

#### 1. 进程启动开销 ⚠️⚠️⚠️

**问题**：每次调用都创建新的进程池

```python
with multiprocessing.Pool(num_workers) as pool:
    results = pool.starmap(_worker_mcts, worker_args)
```

**开销**：
- 创建 4 个进程：~0.5-1.0s
- 序列化/反序列化 Battle：~0.2-0.5s  
- 进程间通信：~0.1-0.2s

**总开销**：~0.8-1.7s（**几乎等于实际耗时**）

#### 2. Battle Pickle 序列化开销

**问题**：Battle 对象很大（6.75 KB）

```python
pickled = pickle.dumps(battle)  # 每次调用都序列化
```

**开销**：
- 序列化：~0.01s × 4 workers = 0.04s
- 反序列化：~0.01s × 4 workers = 0.04s
- 通过 IPC 传输：~0.05s

**总开销**：~0.13s

#### 3. 模拟次数太少

**问题**：每个 worker 只运行 25 次模拟（100 / 4）

```
串行：100 次模拟 = 0.080s
并行：每个 worker 25 次 = 0.02s 计算时间
```

**开销/计算比**：1.9s / 0.08s = **24:1**

### 📊 开销分解

| 组件 | 耗时 (估算) | 占比 |
|------|-------------|------|
| 进程创建/销毁 | 0.5-1.0s | 26-53% |
| Battle 序列化 | 0.13s | 7% |
| IPC 通信 | 0.1-0.2s | 5-10% |
| 实际计算 | 0.08s | 4% |
| **总计** | **1.9s** | **100%** |

**结论**：96% 的时间浪费在开销上！

---

## 💡 解决方案

### 方案 1: 进程池复用 ⭐⭐⭐⭐

**问题**：每次调用都创建新进程池

**解决**：复用进程池（在训练循环中）

```python
class ParallelMCTSAgent:
    def __init__(self, num_workers=4):
        self.pool = multiprocessing.Pool(num_workers)
        self._initialized = False
    
    def search(self, battle, ...):
        # 首次调用：初始化 worker
        if not self._initialized:
            self.pool.map(init_worker, [model_path] * self.pool._processes)
            self._initialized = True
        
        # 复用进程池
        return parallel_mcts_search_root(
            battle, ..., pool=self.pool
        )
    
    def close(self):
        self.pool.close()
        self.pool.join()
```

**预期收益**：消除 0.5-1.0s 的进程创建开销

### 方案 2: 增加模拟次数 ⭐⭐⭐

**问题**：100 次模拟太少，开销占比过大

**解决**：增加到 800-1600 次

```python
# 开销固定 ~1.0s
num_simulations = 1600
# 计算时间：~1.3s (1600 / 100 * 0.08)
# 总耗时：~2.3s
# 串行耗时：~1.3s
# 加速比：1.3 / 0.6 = 2.2x ✅
```

**预期收益**：开销占比降低，加速比 >2x

### 方案 3: 轻量级状态传输 ⭐⭐

**问题**：传输完整 Battle 对象

**解决**：只传输 `save_mutable_state()` + 初始化信息

```python
# 主进程：一次性初始化 worker
init_data = {
    'team_a_species': [...],  # 只传精灵种类
    'team_b_species': [...],
    'skills': [...],
}
pool.map(init_worker_once, [init_data] * num_workers)

# 每次搜索：只传可变状态（更小）
mutable_state = battle.save_mutable_state()  # 更小的数据
results = pool.starmap(worker_search, [...])
```

**预期收益**：减少 50-70% 的序列化开销

### 方案 4: 批处理多个搜索 ⭐⭐

**问题**：单次搜索的开销分摊不足

**解决**：一次调用处理多个 battle

```python
def parallel_mcts_batch(
    battles: list[Battle],
    num_workers=4,
    ...
):
    """一次并行处理多个 battle
    
    开销分摊：1.0s / 10 battles = 0.1s per battle
    """
    # 每个 worker 处理 len(battles) / num_workers 个
    ...
```

**预期收益**：开销分摊到多个搜索上

---

## 🎯 推荐实施顺序

### 短期（今天）

**1. 增加模拟次数** ⭐⭐⭐
- 最简单
- 立即验证是否有效
- 修改测试：`num_simulations = 800`

**2. 测试进程池复用** ⭐⭐⭐⭐
- 修改 API 支持传入 pool
- 测试性能改善

### 中期（明天）

**3. 实现轻量级状态传输** ⭐⭐
- 只在首次传完整数据
- 后续只传 diff

**4. 批处理接口** ⭐⭐
- 用于自我博弈（天然就是批处理）

---

## 📊 预期改进

### 当前（100 次模拟）

| 项目 | 耗时 |
|------|------|
| 串行 | 0.08s |
| 并行 | 1.90s |
| 加速比 | **0.04x** ❌ |

### 改进后（1600 次模拟 + 进程池复用）

| 项目 | 耗时 |
|------|------|
| 串行 | 1.30s |
| 并行（首次）| 1.50s |
| 并行（复用）| 0.50s |
| 加速比 | **2.6x** ✅ |

### 最终（复用 + 批处理）

| 项目 | 耗时 |
|------|------|
| 串行（10个）| 13.0s |
| 并行（批处理）| 5.5s |
| 加速比 | **2.4x** ✅ |

---

## 💬 结论

**当前问题**：
- ❌ 开销太大（96%）
- ❌ 模拟次数太少
- ❌ 每次都创建进程池

**解决方案**：
1. ✅ 进程池复用（消除 50% 开销）
2. ✅ 增加模拟次数（降低开销占比）
3. ✅ 轻量级传输（减少 IPC 开销）

**预期结果**：
- 从 0.04x → **2.5-3.0x 加速** 🚀
- 训练场景更有效（批处理 + 复用）

---

**下一步**：立即测试模拟次数增加的效果！
