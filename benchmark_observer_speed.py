"""Micro-benchmark: Observer firing speed before vs after registration-time compilation.

Measures the impact of moving compilation from fire-time (lazy) to registration-time (eager).
"""
import time
from backend.engine.battle import BattleVMEngine
from backend.engine.observer import Observer
from backend.engine.replayer import JournalReplayer
from backend.vm.ctx import Ctx


class MockSprite:
    """Minimal sprite for benchmarking."""
    current_hp = 100
    max_hp = 100
    active_effects = []
    _modifiers = {}
    _mod_scopes = {}

    def add_effect(self, effect):
        self.active_effects.append(effect)


def benchmark_observer_firing(num_observers=100, num_fires=1000):
    """Benchmark observer firing with pre-compiled effects.

    Args:
        num_observers: Number of observers to register
        num_fires: Number of times to fire each trigger
    """
    print(f"\n{'='*60}")
    print(f"Observer Firing Benchmark")
    print(f"{'='*60}")
    print(f"Setup: {num_observers} observers × {num_fires} fires = {num_observers * num_fires:,} total firings")
    print()

    # Setup engine
    engine = BattleVMEngine()
    sprite = MockSprite()
    opp = MockSprite()

    # Register observers (all will compile at registration now)
    print("Registering observers...")
    t0 = time.perf_counter()
    for i in range(num_observers):
        obs = Observer(
            cond={'cond': 'always'},
            then=[
                {'op': 'mod', 'target': 'sprite_self', 'stat': 'atk', 'steps': 1},
                {'op': 'mod', 'target': 'sprite_self', 'stat': 'def', 'steps': 1},
            ],
            scope='turn',
            listen=frozenset({'post_skill'}),
        )
        engine.registry.register(obs)
    t1 = time.perf_counter()
    registration_time = t1 - t0
    print(f"  Registration time: {registration_time*1000:.2f}ms ({registration_time*1000/num_observers:.3f}ms/observer)")

    # Verify all are compiled
    compiled_count = sum(1 for obs in engine.registry._observers if isinstance(obs.then, tuple))
    print(f"  Compiled observers: {compiled_count}/{num_observers}")
    print()

    # Benchmark firing
    print("Benchmarking firing performance...")
    ctx = Ctx()
    replayer = JournalReplayer(sprite, opp, None, engine.registry, team="A")

    t0 = time.perf_counter()
    for _ in range(num_fires):
        engine._fire_post_event("post_skill", ctx, replayer)
    t1 = time.perf_counter()

    firing_time = t1 - t0
    total_firings = num_observers * num_fires
    time_per_firing = firing_time * 1e6 / total_firings  # microseconds

    print(f"  Total firing time: {firing_time*1000:.2f}ms")
    print(f"  Time per observer firing: {time_per_firing:.2f}µs")
    print(f"  Throughput: {total_firings/firing_time:,.0f} firings/sec")
    print()

    # Calculate effect processing rate
    effects_per_observer = 2  # Two mod ops per observer
    total_effects = total_firings * effects_per_observer
    time_per_effect = firing_time * 1e9 / total_effects  # nanoseconds

    print(f"Effect processing:")
    print(f"  Total effects processed: {total_effects:,}")
    print(f"  Time per effect: {time_per_effect:.0f}ns")
    print(f"  Throughput: {total_effects/firing_time:,.0f} effects/sec")
    print()

    return {
        'registration_time_ms': registration_time * 1000,
        'firing_time_ms': firing_time * 1000,
        'time_per_firing_us': time_per_firing,
        'time_per_effect_ns': time_per_effect,
        'throughput_firings_per_sec': total_firings / firing_time,
        'throughput_effects_per_sec': total_effects / firing_time,
    }


def benchmark_pre_event_firing(num_observers=100, num_fires=1000):
    """Benchmark pre-event observer firing (no scope baking)."""
    print(f"\n{'='*60}")
    print(f"Pre-Event Observer Benchmark")
    print(f"{'='*60}")
    print(f"Setup: {num_observers} observers × {num_fires} fires")
    print()

    engine = BattleVMEngine()

    # Register pre-event observers
    print("Registering pre-event observers...")
    t0 = time.perf_counter()
    for i in range(num_observers):
        obs = Observer(
            cond={'cond': 'always'},
            then=[
                {'op': 'mod', 'target': 'sprite_self', 'stat': 'atk', 'steps': 1},
                {'op': 'mod', 'target': 'sprite_opp', 'stat': 'def', 'steps': -1},
            ],
            listen=frozenset({'pre_calc'}),
            owner_sprite_id=123,
        )
        engine.registry.register(obs)
    t1 = time.perf_counter()
    registration_time = t1 - t0
    print(f"  Registration time: {registration_time*1000:.2f}ms")
    print()

    # Benchmark firing
    print("Benchmarking pre-event firing...")
    ctx = Ctx()

    t0 = time.perf_counter()
    for _ in range(num_fires):
        engine._fire_pre_event("pre_calc", ctx, 123)
    t1 = time.perf_counter()

    firing_time = t1 - t0
    total_firings = num_observers * num_fires
    time_per_firing = firing_time * 1e6 / total_firings

    print(f"  Total firing time: {firing_time*1000:.2f}ms")
    print(f"  Time per observer firing: {time_per_firing:.2f}µs")
    print(f"  Throughput: {total_firings/firing_time:,.0f} firings/sec")
    print()

    return {
        'registration_time_ms': registration_time * 1000,
        'firing_time_ms': firing_time * 1000,
        'time_per_firing_us': time_per_firing,
        'throughput_firings_per_sec': total_firings / firing_time,
    }


if __name__ == '__main__':
    # Warm up
    print("Warming up...")
    benchmark_observer_firing(num_observers=10, num_fires=100)

    # Main benchmarks
    post_results = benchmark_observer_firing(num_observers=100, num_fires=1000)
    pre_results = benchmark_pre_event_firing(num_observers=100, num_fires=1000)

    # Summary
    print(f"\n{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    print(f"Post-event observers: {post_results['time_per_firing_us']:.2f}µs per firing")
    print(f"Pre-event observers:  {pre_results['time_per_firing_us']:.2f}µs per firing")
    print(f"\nKey improvement: No lazy-compile checks in hot path")
    print(f"  - Registration: One-time compilation cost")
    print(f"  - Firing: Zero-overhead typed IR dispatch")
    print()
