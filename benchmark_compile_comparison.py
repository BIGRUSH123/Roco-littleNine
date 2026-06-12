"""Benchmark: Registration-time compilation vs lazy compilation (simulated old behavior).

Measures the performance difference between:
- OLD: Lazy compile on first fire (dict check + compile_effects_batch on first hit)
- NEW: Eager compile at registration (current implementation)
"""
import time
from backend.engine.battle import BattleVMEngine
from backend.engine.observer import Observer
from backend.engine.replayer import JournalReplayer
from backend.vm.ctx import Ctx
from backend.vm.executor import compile_effects_batch, process_effects


class MockSprite:
    current_hp = 100
    max_hp = 100
    active_effects = []
    _modifiers = {}
    _mod_scopes = {}

    def add_effect(self, effect):
        self.active_effects.append(effect)


def benchmark_new_eager_compile(num_observers=100, num_fires=100):
    """Current implementation: compile at registration."""
    engine = BattleVMEngine()
    sprite = MockSprite()
    opp = MockSprite()

    effects_template = [
        {'op': 'mod', 'target': 'sprite_self', 'stat': 'atk', 'steps': 1},
        {'op': 'mod', 'target': 'sprite_self', 'stat': 'def', 'steps': 1},
    ]

    # Registration (includes compilation)
    t0 = time.perf_counter()
    for i in range(num_observers):
        obs = Observer(
            cond={'cond': 'always'},
            then=effects_template.copy(),
            scope='turn',
            listen=frozenset({'post_skill'}),
        )
        engine.registry.register(obs)
    t1 = time.perf_counter()
    registration_time = t1 - t0

    # Firing (already compiled)
    ctx = Ctx()
    replayer = JournalReplayer(sprite, opp, None, engine.registry, team="A")

    t0 = time.perf_counter()
    for _ in range(num_fires):
        engine._fire_post_event("post_skill", ctx, replayer)
    t1 = time.perf_counter()
    firing_time = t1 - t0

    return registration_time, firing_time


def benchmark_old_lazy_compile_simulated(num_observers=100, num_fires=100):
    """Simulate old behavior: compile on first fire."""
    engine = BattleVMEngine()
    sprite = MockSprite()
    opp = MockSprite()

    effects_template = [
        {'op': 'mod', 'target': 'sprite_self', 'stat': 'atk', 'steps': 1},
        {'op': 'mod', 'target': 'sprite_self', 'stat': 'def', 'steps': 1},
    ]

    # Registration (no compilation in old version)
    # We need to bypass the new _index() and manually set up observers
    t0 = time.perf_counter()
    observers = []
    for i in range(num_observers):
        obs = Observer(
            cond={'cond': 'always'},
            then=effects_template.copy(),  # Keep as dict list
            scope='turn',
            listen=frozenset({'post_skill'}),
        )
        # Manually append without calling register (to skip compilation)
        obs._reg_seq = len(observers)
        observers.append(obs)
        engine.registry._by_trigger.setdefault('post_skill', []).append(obs)
        engine.registry._ownerless_by_trigger.setdefault('post_skill', []).append(obs)
        engine.registry._observers.append(obs)
    t1 = time.perf_counter()
    registration_time = t1 - t0

    # Firing (with lazy compile check)
    ctx = Ctx()
    replayer = JournalReplayer(sprite, opp, None, engine.registry, team="A")

    t0 = time.perf_counter()
    for _ in range(num_fires):
        # Simulate old _fire_post_event with lazy compile
        for obs in engine.registry.candidates_for('post_skill'):
            try:
                if obs.eval_cond(ctx):
                    # OLD CODE: Check and compile on first fire
                    if obs.then and type(obs.then[0]) is dict:
                        obs.then = compile_effects_batch(obs.then)
                    journal = process_effects(ctx, obs.then)
                    replayer._trait_sourcing = True
                    replayer.replay(journal)
                    replayer._trait_sourcing = False
            except Exception:
                continue
    t1 = time.perf_counter()
    firing_time = t1 - t0

    return registration_time, firing_time


def run_comparison(num_observers=100, num_fires=100, num_trials=5):
    """Run comparison across multiple trials."""
    print(f"\n{'='*70}")
    print(f"Registration-time vs Lazy Compilation Benchmark")
    print(f"{'='*70}")
    print(f"Configuration: {num_observers} observers × {num_fires} fires/trial × {num_trials} trials")
    print()

    # Warm up
    print("Warming up...")
    benchmark_new_eager_compile(10, 10)
    benchmark_old_lazy_compile_simulated(10, 10)
    print()

    # Run trials
    new_reg_times = []
    new_fire_times = []
    old_reg_times = []
    old_fire_times = []

    for trial in range(num_trials):
        print(f"Trial {trial + 1}/{num_trials}...", end='\r')

        # New approach
        reg_t, fire_t = benchmark_new_eager_compile(num_observers, num_fires)
        new_reg_times.append(reg_t)
        new_fire_times.append(fire_t)

        # Old approach
        reg_t, fire_t = benchmark_old_lazy_compile_simulated(num_observers, num_fires)
        old_reg_times.append(reg_t)
        old_fire_times.append(fire_t)

    print(" " * 50, end='\r')  # Clear progress line

    # Calculate averages
    new_reg_avg = sum(new_reg_times) / num_trials
    new_fire_avg = sum(new_fire_times) / num_trials
    old_reg_avg = sum(old_reg_times) / num_trials
    old_fire_avg = sum(old_fire_times) / num_trials

    new_total = new_reg_avg + new_fire_avg
    old_total = old_reg_avg + old_fire_avg

    # Results
    print(f"{'Approach':<30} {'Registration':<15} {'Firing':<15} {'Total':<15}")
    print(f"{'-'*70}")
    print(f"{'NEW (eager compile)':<30} {new_reg_avg*1000:>12.2f}ms {new_fire_avg*1000:>12.2f}ms {new_total*1000:>12.2f}ms")
    print(f"{'OLD (lazy compile)':<30} {old_reg_avg*1000:>12.2f}ms {old_fire_avg*1000:>12.2f}ms {old_total*1000:>12.2f}ms")
    print(f"{'-'*70}")

    # Calculate speedups
    reg_ratio = old_reg_avg / new_reg_avg if new_reg_avg > 0 else 0
    fire_speedup = old_fire_avg / new_fire_avg if new_fire_avg > 0 else 0
    total_speedup = old_total / new_total if new_total > 0 else 0

    print()
    print(f"Performance Impact:")
    print(f"  Registration: {(new_reg_avg/old_reg_avg - 1)*100:+.1f}% time (compilation cost)")
    print(f"  Firing:       {(1 - new_fire_avg/old_fire_avg)*100:+.1f}% improvement ({fire_speedup:.2f}× faster)")
    print(f"  Total:        {(1 - new_total/old_total)*100:+.1f}% improvement ({total_speedup:.2f}× faster)")
    print()

    # Per-operation breakdown
    total_firings = num_observers * num_fires
    new_fire_per_op = new_fire_avg * 1e6 / total_firings
    old_fire_per_op = old_fire_avg * 1e6 / total_firings

    print(f"Per-observer-firing cost:")
    print(f"  NEW: {new_fire_per_op:.2f}µs")
    print(f"  OLD: {old_fire_per_op:.2f}µs (includes dict type check on every fire)")
    print(f"  Saved: {old_fire_per_op - new_fire_per_op:.2f}µs per firing")
    print()

    # First-fire penalty analysis (only on old approach)
    first_fire_compile_overhead = (old_fire_times[0] - min(old_fire_times[1:])) / num_observers if num_trials > 1 else 0
    print(f"Old approach first-fire penalty: ~{first_fire_compile_overhead*1e6:.2f}µs per observer")
    print(f"  (compilation on first fire, then cached)")
    print()


if __name__ == '__main__':
    run_comparison(num_observers=100, num_fires=100, num_trials=5)

    # Heavy load test
    print()
    print("="*70)
    print("Heavy Load Test (200 observers × 500 fires)")
    print("="*70)
    run_comparison(num_observers=200, num_fires=500, num_trials=3)
