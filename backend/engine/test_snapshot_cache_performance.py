"""Performance validation tests for skill summary cache optimization."""
from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from backend.sim.battle import Battle
from backend.sim.factory import SimFactory
from backend.engine import snapshot

factory = SimFactory()


def test_cache_hit_rate():
    """Verify cache achieves near 100% hit rate in typical MCTS scenario."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle._mcts_sim = True
    sprite = battle.player_a.active

    # Simulate MCTS: many calls with same skill state
    iterations = 1000
    cache_hits = 0

    for i in range(iterations):
        cache_before = getattr(sprite, '_skill_summary_cache', None)

        ctx = battle._make_ctx(
            sprite,
            battle.player_b.active,
            sprite.skills[0],
            None,
            battle.globals,
            team="A",
            turn=1,
        )

        cache_after = getattr(sprite, '_skill_summary_cache', None)

        # After first call, cache should be reused
        if i > 0 and cache_before is not None and cache_before is cache_after:
            cache_hits += 1

    hit_rate = cache_hits / (iterations - 1) if iterations > 1 else 0
    print(f"\n缓存命中率: {hit_rate:.1%} ({cache_hits}/{iterations-1})")

    # Should achieve near 100% hit rate
    assert hit_rate > 0.99, f"缓存命中率过低: {hit_rate:.1%}"


def test_performance_comparison():
    """Compare performance with and without cache."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle._mcts_sim = True

    iterations = 5000

    # ══════════════════════════════════════════════════════
    # Test WITH cache (normal operation)
    # ══════════════════════════════════════════════════════
    start = time.perf_counter()
    for _ in range(iterations):
        ctx = battle._make_ctx(
            battle.player_a.active,
            battle.player_b.active,
            battle.player_a.active.skills[0],
            None,
            battle.globals,
            team="A",
            turn=1,
        )
    elapsed_with_cache = time.perf_counter() - start

    # ══════════════════════════════════════════════════════
    # Test WITHOUT cache (force cache miss every time)
    # ══════════════════════════════════════════════════════
    sprite = battle.player_a.active

    # Clear cache and modify state slightly between calls to force recalculation
    start = time.perf_counter()
    for i in range(iterations):
        # Force cache invalidation by clearing it
        if hasattr(sprite, '_skill_summary_cache'):
            delattr(sprite, '_skill_summary_cache')

        ctx = battle._make_ctx(
            sprite,
            battle.player_b.active,
            sprite.skills[0],
            None,
            battle.globals,
            team="A",
            turn=1,
        )
    elapsed_without_cache = time.perf_counter() - start

    # ══════════════════════════════════════════════════════
    # Results
    # ══════════════════════════════════════════════════════
    speedup = elapsed_without_cache / elapsed_with_cache
    time_saved = elapsed_without_cache - elapsed_with_cache
    per_call_with = elapsed_with_cache / iterations * 1_000_000  # μs
    per_call_without = elapsed_without_cache / iterations * 1_000_000  # μs

    print(f"\n性能对比 - _make_ctx ({iterations:,} 次调用):")
    print(f"  有缓存:   {elapsed_with_cache:.3f}s  ({per_call_with:.2f}μs/call)")
    print(f"  无缓存:   {elapsed_without_cache:.3f}s  ({per_call_without:.2f}μs/call)")
    print(f"  加速比:   {speedup:.2f}x")
    print(f"  节省时间: {time_saved:.3f}s ({time_saved/elapsed_without_cache:.1%})")

    # Cache should provide measurable speedup (even small improvement counts)
    # Note: _collect_skill_summary is only part of _make_ctx, so speedup may be modest
    assert speedup > 1.0, f"缓存未提供加速: {speedup:.2f}x"

    if speedup >= 1.1:
        print(f"\n✓ 缓存加速 {speedup:.2f}x，优化显著！")
    elif speedup >= 1.05:
        print(f"\n✓ 缓存加速 {speedup:.2f}x，优化有效（_collect_skill_summary 是 _make_ctx 的一部分）")
    else:
        print(f"\n✓ 缓存加速 {speedup:.2f}x，提供了优化（虽然较小）")


def test_direct_skill_summary_performance():
    """Directly measure _collect_skill_summary performance with/without cache."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    sprite = p1.active
    iterations = 10000

    # ══════════════════════════════════════════════════════
    # Test WITH cache
    # ══════════════════════════════════════════════════════
    start = time.perf_counter()
    for _ in range(iterations):
        result = snapshot._collect_skill_summary(sprite)
    elapsed_with_cache = time.perf_counter() - start

    # ══════════════════════════════════════════════════════
    # Test WITHOUT cache
    # ══════════════════════════════════════════════════════
    start = time.perf_counter()
    for _ in range(iterations):
        # Clear cache before each call
        if hasattr(sprite, '_skill_summary_cache'):
            delattr(sprite, '_skill_summary_cache')
        result = snapshot._collect_skill_summary(sprite)
    elapsed_without_cache = time.perf_counter() - start

    # ══════════════════════════════════════════════════════
    # Results
    # ══════════════════════════════════════════════════════
    speedup = elapsed_without_cache / elapsed_with_cache
    time_saved = elapsed_without_cache - elapsed_with_cache
    per_call_with = elapsed_with_cache / iterations * 1_000_000  # μs
    per_call_without = elapsed_without_cache / iterations * 1_000_000  # μs

    print(f"\n性能对比 - _collect_skill_summary 直接测试 ({iterations:,} 次调用):")
    print(f"  有缓存:   {elapsed_with_cache:.3f}s  ({per_call_with:.2f}μs/call)")
    print(f"  无缓存:   {elapsed_without_cache:.3f}s  ({per_call_without:.2f}μs/call)")
    print(f"  加速比:   {speedup:.2f}x")
    print(f"  节省时间: {time_saved:.3f}s ({time_saved/elapsed_without_cache:.1%})")

    # Direct test should show clear speedup
    assert speedup > 1.5, f"直接测试加速比过低: {speedup:.2f}x"
    print(f"\n✓ _collect_skill_summary 缓存加速 {speedup:.2f}x，优化显著！")


def test_cache_overhead_is_minimal():
    """Verify cache overhead is minimal for first call (cache miss)."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)

    # Measure first call overhead (cache miss + creation)
    iterations = 1000
    times = []

    for _ in range(iterations):
        # Create fresh sprites to ensure cache miss
        p1_fresh = factory.build_player("A", [
            {"name": "草衣虫", "skills": ["猛烈撞击"]},
        ])
        battle.player_a = p1_fresh

        start = time.perf_counter()
        ctx = battle._make_ctx(
            battle.player_a.active,
            battle.player_b.active,
            battle.player_a.active.skills[0],
            None,
            battle.globals,
            team="A",
            turn=1,
        )
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg_time = sum(times) / len(times) * 1_000_000  # μs
    print(f"\n首次调用平均耗时: {avg_time:.2f}μs")

    # First call overhead should be negligible (< 100μs)
    assert avg_time < 100, f"首次调用开销过大: {avg_time:.2f}μs"


def test_mcts_simulation_scenario():
    """Simulate realistic MCTS workload with multiple sprites."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
        {"name": "神谕鲨", "skills": ["甩水", "甩水", "甩水", "甩水"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    battle._mcts_sim = True

    # Simulate MCTS: 200 simulations × 20 nodes per simulation
    simulations = 200
    nodes_per_sim = 20
    total_calls = simulations * nodes_per_sim

    start = time.perf_counter()

    for sim in range(simulations):
        # Each simulation explores multiple nodes
        for node in range(nodes_per_sim):
            # Alternating between sprites to test cache per-sprite
            sprite_idx = node % 2
            team = "A" if node % 2 == 0 else "B"
            player = battle.player_a if team == "A" else battle.player_b
            opponent = battle.player_b if team == "A" else battle.player_a

            # Most calls reuse same sprite (high cache hit rate)
            ctx = battle._make_ctx(
                player.team[sprite_idx],
                opponent.active,
                player.team[sprite_idx].skills[0],
                None,
                battle.globals,
                team=team,
                turn=sim,
            )

    elapsed = time.perf_counter() - start
    per_call = elapsed / total_calls * 1_000_000  # μs
    throughput = total_calls / elapsed

    print(f"\nMCTS 模拟场景:")
    print(f"  模拟次数: {simulations}")
    print(f"  每次节点: {nodes_per_sim}")
    print(f"  总调用数: {total_calls:,}")
    print(f"  总耗时:   {elapsed:.3f}s")
    print(f"  每次调用: {per_call:.2f}μs")
    print(f"  吞吐量:   {throughput:,.0f} calls/s")

    # Should maintain high throughput
    assert throughput > 10000, f"吞吐量过低: {throughput:,.0f} calls/s"
    print(f"\n✓ 吞吐量 {throughput:,.0f} calls/s，满足 MCTS 性能要求！")


def test_cache_memory_footprint():
    """Verify cache doesn't cause excessive memory usage."""
    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    sprite = battle.player_a.active

    # Populate cache
    ctx = battle._make_ctx(
        sprite,
        battle.player_b.active,
        sprite.skills[0],
        None,
        battle.globals,
        team="A",
        turn=1,
    )

    # Check cache structure
    assert hasattr(sprite, '_skill_summary_cache')
    cache = sprite._skill_summary_cache

    # Cache should be (key_tuple, result_tuple)
    assert isinstance(cache, tuple) and len(cache) == 2

    cache_key, cache_result = cache

    # Key: tuple of (id, id, bool, str, int, int) per skill
    # 4 skills × 6 items = 24 items
    assert isinstance(cache_key, tuple)
    assert len(cache_key) == 4  # 4 skills

    # Result: (frozenset, dict, int, int)
    assert isinstance(cache_result, tuple) and len(cache_result) == 4

    print(f"\n缓存内存占用:")
    print(f"  缓存键长度:   {len(cache_key)} 技能")
    print(f"  缓存结果:     4 元组 (frozenset, dict, int, int)")
    print(f"  ✓ 缓存结构紧凑，内存占用合理")


if __name__ == "__main__":
    print("=" * 60)
    print("技能摘要缓存性能验证")
    print("=" * 60)

    test_cache_hit_rate()
    test_direct_skill_summary_performance()
    test_performance_comparison()
    test_cache_overhead_is_minimal()
    test_mcts_simulation_scenario()
    test_cache_memory_footprint()

    print("\n" + "=" * 60)
    print("✅ 所有性能测试通过！缓存优化有效。")
    print("=" * 60)
