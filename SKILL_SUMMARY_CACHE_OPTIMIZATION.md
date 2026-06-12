# 技能摘要缓存优化

## 背景

在性能分析中发现，`_collect_skill_summary` 方法在 MCTS 模拟期间被频繁调用（一次模拟数千次），但它每次都重新计算相同的技能摘要信息（元素集合、能量消耗等）。

## 优化方案

在 `backend/engine/snapshot.py` 中实现了技能摘要缓存：

### 1. 缓存键生成 (`_battle_skill_summary_key`)

为每个技能槽生成一个精确的缓存键，包含：
- `id(sk)` - 技能对象身份
- `id(base)` - 基础技能对象（处理 replaced_by）
- `nullified` - 技能是否失效
- `_element_override` - 元素覆写
- `_mech_energy_reduction` - 机械能耗减免
- `_modifiers.get("energy_cost", 0)` - 能耗修正

这些字段覆盖了所有会影响技能摘要的可变状态。

### 2. 缓存存储

缓存存储在 `sprite._skill_summary_cache` 属性上，格式为：
```python
(cache_key, result)
```

其中 `result` 是 `(frozenset(elements), element_counts, energy_sum, zero_cost_count)` 四元组。

### 3. 缓存策略

- **仅对 BattleSkill 列表启用**：测试/duck-typed 对象走原逻辑，避免兼容性风险
- **精确失效**：任何影响摘要的字段变化都会导致缓存键不匹配，自动失效
- **无需手动管理**：不需要显式清除缓存，键不匹配时自动重建

## 测试覆盖

创建了 `backend/engine/test_snapshot_cache.py`，包含：

1. **基本功能测试** (`test_skill_summary_cache_basic`)
   - 验证缓存正确创建和重用

2. **缓存失效测试** (`test_skill_summary_cache_invalidation`)
   - 验证技能状态变化时缓存正确失效

## 验证结果

所有核心测试通过：
- ✅ 167 个测试全部通过
- ✅ AI 测试套件（56 个）
- ✅ VM 测试套件（109 个）
- ✅ 新增缓存测试（2 个）

## 性能影响

预期收益：
- 减少 `_collect_skill_summary` 的重复计算
- 在 MCTS 模拟期间，相同技能槽的摘要只计算一次
- 对于 4 技能槽 × 两支队伍 × 数千次调用，缓存命中率接近 100%

## 注意事项

1. 缓存键使用 `id()` 而不是对象内容，因此：
   - 技能对象替换（换宠、传动）会自动失效
   - 同一对象的字段修改通过其他键字段检测

2. 缓存存储在 sprite 上，生命周期与 sprite 一致：
   - Battle 内正常工作
   - Rollback/restore 会自动处理（sprite 本身被替换）

3. 向后兼容：
   - 不包含 `_skill_summary_cache` 的 sprite 正常工作
   - 非 BattleSkill 技能列表走原逻辑

## 相关文件

- `backend/engine/snapshot.py` - 核心实现
- `backend/engine/test_snapshot_cache.py` - 测试覆盖
