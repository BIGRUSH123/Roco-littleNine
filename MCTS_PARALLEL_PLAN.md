# MCTS 并行化方案

## 📋 当前 MCTS 结构分析

### 核心循环

```python
def mcts_search(battle, model, factory, opponent_agent, num_simulations=200, ...):
    """执行 num_simulations 次模拟，返回动作概率"""
    
    # 初始化根节点
    root = MCTSNode(valid, prior)
    
    # 主循环：每次模拟独立
    for _ in range(num_simulations):
        saved = battle.save_mutable_state()  # 保存状态
        try:
            # 1. Selection（选择路径）
            node = root
            path = []
            while node.has_children:
                best_a = select_best_action(node, c_puct)  # UCB 选择
                path.append((node, best_a))
                node = node.children[best_a]
                _step_battle(battle, best_a, opponent_agent)  # 执行动作
            
            # 2. Expansion & Evaluation（扩展和评估）
            if not is_terminal(battle):
                state = encode_battle_state(battle)
                leaf_value, prior = evaluator.evaluate(state, mask)  # 网络推理
                expand_node(node, valid_actions, prior)
            else:
                leaf_value = battle_outcome_a(battle)  # 终端节点
            
            # 3. Backpropagation（反向传播）
            for parent, _ in reversed(path):
                parent.visit_count += 1
                parent.total_value += leaf_value
            node.visit_count += 1
            node.total_value += leaf_value
        finally:
            battle.restore_mutable_state(saved)  # 恢复状态
    
    # 返回访问次数比例
    return visit_counts_to_probs(root)
```

### 关键特性

1. **每次模拟独立**：save → simulate → restore
2. **共享状态**：所有模拟共用一棵树（root）
3. **网络推理**：每次模拟调用 1 次（叶节点评估）
4. **状态快照**：`save_mutable_state()` / `restore_mutable_state()`

---

## 🚀 并行化策略

### 方案 1: 根并行（Root Parallelization）⭐⭐⭐

**思路**：每个进程独立运行完整的 MCTS，最后合并结果。

```python
def parallel_mcts_search_root(
    battle,
    model,
    factory,
    opponent_agent,
    num_simulations=200,
    num_workers=4,
    **kwargs
):
    """根并行 MCTS
    
    每个 worker 独立运行 num_simulations // num_workers 次模拟，
    最后合并所有 worker 的访问统计。
    """
    sims_per_worker = num_simulations // num_workers
    
    # 序列化初始状态
    initial_state = battle.save_mutable_state()
    
    with Pool(num_workers) as pool:
        # 每个 worker 独立运行 MCTS
        results = pool.starmap(
            _worker_mcts,
            [
                (initial_state, factory, model, opponent_agent, sims_per_worker, kwargs)
                for _ in range(num_workers)
            ]
        )
    
    # 合并结果：累加访问次数
    merged_visits = np.zeros(NUM_ACTIONS, dtype=np.int32)
    for visits in results:
        merged_visits += visits
    
    # 转换为概率
    return merged_visits / max(merged_visits.sum(), 1)

def _worker_mcts(initial_state, factory, model, opponent_agent, num_sims, kwargs):
    """Worker 进程：独立运行 MCTS"""
    # 1. 恢复状态
    battle = factory.restore_battle(initial_state)
    
    # 2. 运行 MCTS
    probs = mcts_search(battle, model, factory, opponent_agent, num_sims, **kwargs)
    
    # 3. 返回访问次数
    return extract_visit_counts(probs, num_sims)
```

**优点**：
- ✅ 实现简单（每个 worker 独立）
- ✅ 无锁、无竞争
- ✅ 理想加速：**近似 N 倍**（N = workers）

**缺点**：
- ⚠️ 每个 worker 维护独立的树（内存 ×N）
- ⚠️ 探索效率降低（树不共享）

**适用场景**：
- ✅ 模拟次数多（≥800）
- ✅ 树不深（探索效率影响小）
- ✅ 追求实现简单

---

### 方案 2: 叶并行（Leaf Parallelization）⭐⭐

**思路**：在单个 MCTS 中，并行评估多个叶节点。

```python
def mcts_search_leaf_parallel(
    battle, model, factory, opponent_agent,
    num_simulations=200,
    leaf_batch_size=4,  # 每批并行评估 4 个叶节点
    **kwargs
):
    """叶并行 MCTS
    
    累积多个叶节点，一次性批量评估（GPU 并行）。
    """
    root = MCTSNode(valid, prior)
    
    simulations_left = num_simulations
    while simulations_left > 0:
        # 累积一批叶节点
        batch_states = []
        batch_masks = []
        batch_paths = []
        
        while simulations_left > 0 and len(batch_states) < leaf_batch_size:
            simulations_left -= 1
            saved = battle.save_mutable_state()
            
            # Selection
            node, path = select_path(root, battle, c_puct)
            
            if not is_terminal(battle):
                state = encode_battle_state(battle)
                batch_states.append(state)
                batch_masks.append(get_valid_mask(battle))
                batch_paths.append((node, path))
            else:
                # 终端节点：立即回传
                leaf_value = battle_outcome_a(battle)
                backpropagate(path, node, leaf_value)
            
            battle.restore_mutable_state(saved)
        
        # 批量评估（GPU 并行）
        if batch_states:
            values, priors = model.evaluate_batch(batch_states, batch_masks)
            
            # 扩展和回传
            for i, (node, path) in enumerate(batch_paths):
                expand_node(node, priors[i])
                backpropagate(path, node, values[i])
    
    return visit_counts_to_probs(root)
```

**优点**：
- ✅ 共享搜索树（探索效率高）
- ✅ GPU 批量推理加速
- ✅ 内存占用不变

**缺点**：
- ⚠️ CPU 仿真仍然串行
- ⚠️ 加速受限于 GPU 推理占比
- ⚠️ 实现复杂度中等

**适用场景**：
- ✅ GPU 推理是瓶颈
- ✅ batch_size 大（GPU 利用率高）
- ✅ 已有 GPU

**注意**：当前代码已经实现了这个！（`leaf_batch_size` 参数）

---

### 方案 3: 虚拟损失并行（Virtual Loss）⭐

**思路**：多线程共享树，用"虚拟损失"避免重复探索同一路径。

```python
class MCTSNode:
    def __init__(self, ...):
        self.visit_count = 0
        self.total_value = 0.0
        self.virtual_loss = 0  # 虚拟损失
        self.lock = threading.Lock()
    
    @property
    def value(self):
        # Q 值考虑虚拟损失
        n = self.visit_count + self.virtual_loss
        return self.total_value / max(n, 1)

def parallel_mcts_virtual_loss(
    battle, model, factory, opponent_agent,
    num_simulations=200,
    num_threads=4,
    **kwargs
):
    """虚拟损失并行 MCTS"""
    root = MCTSNode(valid, prior)
    
    def worker():
        for _ in range(num_simulations // num_threads):
            # 1. Selection（加虚拟损失）
            path = []
            node = root
            with node.lock:
                node.virtual_loss += 1  # 标记"正在探索"
            
            while node.has_children:
                best_a = select_best_action(node, c_puct)
                path.append((node, best_a))
                node = node.children[best_a]
                with node.lock:
                    node.virtual_loss += 1
            
            # 2. Evaluation
            saved = battle.save_mutable_state()
            # ... 执行路径 ...
            leaf_value = evaluate_leaf(battle, model)
            battle.restore_mutable_state(saved)
            
            # 3. Backpropagation（移除虚拟损失）
            for parent, _ in reversed(path):
                with parent.lock:
                    parent.visit_count += 1
                    parent.total_value += leaf_value
                    parent.virtual_loss -= 1
    
    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    return visit_counts_to_probs(root)
```

**优点**：
- ✅ 共享搜索树
- ✅ 探索多样性高

**缺点**：
- ❌ 需要锁（开销大）
- ❌ GIL 限制（Python 多线程慢）
- ❌ 实现复杂

**结论**：❌ **不推荐**（Python GIL 限制）

---

## 📊 方案对比

| 方案 | 加速比 | 实现难度 | 内存占用 | 探索效率 | 推荐度 |
|------|--------|---------|---------|---------|--------|
| **根并行** | **2-3x** | 低 | 高 (×N) | 中 | ⭐⭐⭐ |
| **叶并行** | 1.3-1.8x | 中 | 低 | 高 | ⭐⭐（已实现）|
| 虚拟损失 | 1.5-2x | 高 | 低 | 高 | ⭐（GIL 限制）|

---

## 🎯 推荐实施方案

### 第一步：根并行（立即实施）⭐⭐⭐

**理由**：
1. ✅ 实现简单（1-2 天）
2. ✅ 加速明显（2-3x）
3. ✅ 无需修改核心 MCTS 逻辑
4. ✅ 可与现有 leaf_batch_size 叠加

**预期收益**：
- 4 核 CPU：**2.5-3x 加速**
- 8 核 CPU：**4-5x 加速**（考虑开销）

### 第二步：优化 + 叶并行（已有）

当前代码已支持 `leaf_batch_size`，可以：
1. 增大 `leaf_batch_size`（如 16-32）
2. 与根并行叠加

---

## 🛠️ 实施计划

### Day 1: 根并行原型

1. 实现 `parallel_mcts_search_root()`
2. 实现 `_worker_mcts()`
3. 处理状态序列化

### Day 2: 测试和优化

1. 正确性验证（对比串行版本）
2. 性能测试（不同 worker 数量）
3. 内存优化

### Day 3: 集成和文档

1. 集成到训练流程
2. 添加配置选项
3. 性能报告

---

## 📝 技术细节

### 状态序列化

```python
# 需要序列化的状态
initial_state = {
    'battle_snapshot': battle.save_mutable_state(),
    'player_a': serialize_player(battle.player_a),
    'player_b': serialize_player(battle.player_b),
    'factory_state': factory.get_state(),
}
```

### 进程池复用

```python
# 训练循环中复用进程池
class ParallelMCTSAgent:
    def __init__(self, num_workers=4):
        self.pool = Pool(num_workers)
        # 预加载模型到各个进程
        self.pool.map(init_worker_model, [model_path] * num_workers)
    
    def search(self, battle, ...):
        return parallel_mcts_search_root(
            battle, ..., pool=self.pool
        )
    
    def __del__(self):
        self.pool.close()
        self.pool.join()
```

---

## 🎉 预期成果

### 性能提升

**当前**：
- build_ctx 优化：2.48x → 2.7x（+10%）

**加上根并行**：
- 2.7x × 2.5 = **6.75x 总加速** 🚀🚀🚀

### 训练效率

- 自我博弈速度：**2-3x 提升**
- 训练时间缩短：**50-66%**

---

## ⚠️ 注意事项

1. **内存占用**：每个 worker 一份 battle 副本
2. **模型加载**：每个进程需要加载模型（可共享权重）
3. **随机数**：确保每个 worker 有不同的随机种子
4. **GIL**：使用 multiprocessing（不是 threading）

---

**准备好开始实施根并行了吗？** 🚀
