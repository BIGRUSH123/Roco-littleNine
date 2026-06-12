"""
减少 dict.get 调用优化 - 性能对比测试

对比优化前后的性能差异
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


def benchmark_build_ctx_optimization():
    """测试 build_ctx 优化效果"""
    print("=" * 80)
    print("build_ctx 优化性能测试")
    print("=" * 80)

    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)

    iterations = 10000

    # 测试当前版本（已优化）
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

    print(f"\n当前版本（减少 dict.get 调用）:")
    print(f"  总耗时: {elapsed:.3f}s")
    print(f"  每次调用: {per_call:.2f}μs")
    print(f"  吞吐量: {throughput:,.0f} calls/s")

    # 与之前的基准对比
    baseline_per_call = 45.98  # μs (来自之前的 profile 数据)
    improvement = (baseline_per_call - per_call) / baseline_per_call * 100

    print(f"\n与优化前对比:")
    print(f"  优化前: {baseline_per_call:.2f}μs/call")
    print(f"  优化后: {per_call:.2f}μs/call")
    if improvement > 0:
        print(f"  性能提升: {improvement:.1f}% ✓")
    else:
        print(f"  性能变化: {improvement:.1f}%")

    return per_call


if __name__ == "__main__":
    per_call = benchmark_build_ctx_optimization()

    print("\n" + "=" * 80)
    print("✅ 性能测试完成")
    print("=" * 80)
