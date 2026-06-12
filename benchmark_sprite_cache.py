"""
Sprite 属性缓存优化 - 性能验证

对比：
1. 原始版本（无优化）
2. 减少 dict.get 优化
3. Sprite 属性缓存优化（当前）
"""
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

import time
from backend.sim.factory import SimFactory
from backend.sim.battle import Battle

factory = SimFactory()


def benchmark_sprite_cache():
    """测试 Sprite 属性缓存优化效果"""
    print("=" * 80)
    print("Sprite 属性缓存优化性能测试")
    print("=" * 80)

    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)

    iterations = 10000

    # 测试当前版本
    start = time.perf_counter()
    for _ in range(iterations):
        ctx = battle._make_ctx(
            battle.player_a.active,
            battle.player_b.active,
            battle.player_a.active.skills[0],
            None,
            team="A",
            turn=1
        )
    elapsed = time.perf_counter() - start

    per_call = elapsed / iterations * 1_000_000  # μs
    throughput = iterations / elapsed

    print(f"\n当前版本（Sprite 属性缓存）:")
    print(f"  总耗时: {elapsed:.3f}s")
    print(f"  每次调用: {per_call:.2f}μs")
    print(f"  吞吐量: {throughput:,.0f} calls/s")

    # 与历史数据对比
    original = 45.98  # 原始版本（无优化）
    after_dict_get = 19.50  # 减少 dict.get 后
    current = per_call

    print(f"\n历史对比:")
    print(f"  原始版本: {original:.2f}μs/call")
    print(f"  减少 dict.get: {after_dict_get:.2f}μs/call (提升 {(original-after_dict_get)/original*100:.1f}%)")
    print(f"  Sprite 缓存: {current:.2f}μs/call")

    improvement_from_dict_get = (after_dict_get - current) / after_dict_get * 100
    total_improvement = (original - current) / original * 100

    print(f"\n增量收益:")
    print(f"  相比 dict.get 优化: {improvement_from_dict_get:.1f}%")
    print(f"  相比原始版本: {total_improvement:.1f}%")
    print(f"  累计加速: {original/current:.2f}x")

    return per_call


if __name__ == "__main__":
    per_call = benchmark_sprite_cache()

    print("\n" + "=" * 80)
    print("✅ 性能测试完成")
    print("=" * 80)
