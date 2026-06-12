"""Cython 原型性能测试

测试 Cython 优化的函数与 Python 原始版本的性能对比
"""

import time
from backend.engine.snapshot import (
    _compute_speed_self,
    _compute_speed,
    _get_element_advantage,
)
from backend.sim.resolver import _TYPE_CHART

# 测试数据
initial_stats = {"speed": 100, "atk": 120, "def": 80}
modifiers = {"speed": 0.2, "atk": 0.5}
stat_stages = {"speed": 2, "atk": 1}

print("="*80)
print("Cython 原型性能测试")
print("="*80)
print()

# 检查 Cython 模块是否编译成功
try:
    from backend.engine.snapshot_cy import (
        compute_speed_self_cy,
        compute_speed_cy,
        get_element_advantage_cy,
    )
    cython_available = True
    print("✅ Cython 模块编译成功")
except ImportError as e:
    cython_available = False
    print(f"❌ Cython 模块未编译: {e}")
    print()
    print("请先编译 Cython 扩展：")
    print("  python setup_cython.py build_ext --inplace")
    print()
    exit(1)

print()

# === 测试 1: compute_speed_self ===
print("【测试 1: compute_speed_self】")
print()

# Python 版本
start = time.perf_counter()
for _ in range(100000):
    # 模拟 Sprite 对象
    class MockSprite:
        initial_stats = initial_stats
        _modifiers = modifiers

    result = _compute_speed_self(MockSprite(), stat_stages)
elapsed_py = time.perf_counter() - start

print(f"  Python 版本 (100k 次): {elapsed_py:.3f}s ({elapsed_py/100000*1e6:.2f}μs/call)")

# Cython 版本
start = time.perf_counter()
for _ in range(100000):
    result = compute_speed_self_cy(initial_stats, modifiers, stat_stages)
elapsed_cy = time.perf_counter() - start

print(f"  Cython 版本 (100k 次): {elapsed_cy:.3f}s ({elapsed_cy/100000*1e6:.2f}μs/call)")
print(f"  加速比: {elapsed_py/elapsed_cy:.2f}x")
print()

# === 测试 2: compute_speed ===
print("【测试 2: compute_speed】")
print()

stats = {"speed": 100}

# Python 版本
start = time.perf_counter()
for _ in range(100000):
    result = _compute_speed(stats, stat_stages)
elapsed_py = time.perf_counter() - start

print(f"  Python 版本 (100k 次): {elapsed_py:.3f}s ({elapsed_py/100000*1e6:.2f}μs/call)")

# Cython 版本
start = time.perf_counter()
for _ in range(100000):
    result = compute_speed_cy(stats, stat_stages)
elapsed_cy = time.perf_counter() - start

print(f"  Cython 版本 (100k 次): {elapsed_cy:.3f}s ({elapsed_cy/100000*1e6:.2f}μs/call)")
print(f"  加速比: {elapsed_py/elapsed_cy:.2f}x")
print()

# === 测试 3: get_element_advantage ===
print("【测试 3: get_element_advantage】")
print()

# Python 版本
start = time.perf_counter()
for _ in range(100000):
    result = _get_element_advantage("火", ["草", "虫"])
elapsed_py = time.perf_counter() - start

print(f"  Python 版本 (100k 次): {elapsed_py:.3f}s ({elapsed_py/100000*1e6:.2f}μs/call)")

# Cython 版本
start = time.perf_counter()
for _ in range(100000):
    result = get_element_advantage_cy("火", ["草", "虫"], _TYPE_CHART)
elapsed_cy = time.perf_counter() - start

print(f"  Cython 版本 (100k 次): {elapsed_cy:.3f}s ({elapsed_cy/100000*1e6:.2f}μs/call)")
print(f"  加速比: {elapsed_py/elapsed_cy:.2f}x")
print()

# === 验证正确性 ===
print("="*80)
print("【正确性验证】")
print("="*80)
print()

class MockSprite:
    initial_stats = {"speed": 100}
    _modifiers = {"speed": 0.5}

py_result_1 = _compute_speed_self(MockSprite(), {"speed": 2})
cy_result_1 = compute_speed_self_cy({"speed": 100}, {"speed": 0.5}, {"speed": 2})
print(f"compute_speed_self: Python={py_result_1}, Cython={cy_result_1}, {'✅' if py_result_1 == cy_result_1 else '❌'}")

py_result_2 = _compute_speed({"speed": 100}, {"speed": 2})
cy_result_2 = compute_speed_cy({"speed": 100}, {"speed": 2})
print(f"compute_speed:      Python={py_result_2}, Cython={cy_result_2}, {'✅' if py_result_2 == cy_result_2 else '❌'}")

py_result_3 = _get_element_advantage("火", ["草"])
cy_result_3 = get_element_advantage_cy("火", ["草"], _TYPE_CHART)
print(f"element_advantage:  Python={py_result_3}, Cython={cy_result_3}, {'✅' if abs(py_result_3 - cy_result_3) < 0.01 else '❌'}")

print()
print("="*80)
print("【总结】")
print("="*80)
print()
print("Cython 原型测试完成！")
print()
print("如果加速比 >2x，值得全面实施。")
print("如果加速比 >3x，强烈推荐全面实施。")
print()
print("下一步：")
print("  1. 如果效果好，重写整个 build_ctx")
print("  2. 添加更多 Cython 函数")
print("  3. 运行完整测试验证")
