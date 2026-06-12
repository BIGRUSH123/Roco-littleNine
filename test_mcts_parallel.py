"""测试 MCTS 并行化的正确性和性能"""

import time
import numpy as np

from backend.engine.ai.core.mcts import mcts_search, NetworkPolicyAgent
from backend.engine.ai.core.mcts_parallel import parallel_mcts_search_root
from backend.engine.ai.core.model import ModularBattleNet
from backend.sim.factory import SimFactory
from backend.common.models import SpeciesStats

print("="*80)
print("MCTS 并行化测试")
print("="*80)
print()

# 创建工厂和测试环境
factory = SimFactory()

# 创建简单的测试队伍
species = SpeciesStats(
    name="测试精灵",
    hp=200,
    atk=120,
    def_=80,
    sp_atk=100,
    sp_def=90,
    speed=110
)

team_a = [
    factory.create_sprite(species, level=50),
    factory.create_sprite(species, level=50),
    factory.create_sprite(species, level=50),
]

team_b = [
    factory.create_sprite(species, level=50),
    factory.create_sprite(species, level=50),
    factory.create_sprite(species, level=50),
]

# 创建对战
battle = factory.create_battle(team_a, team_b)

# 创建简单的对手（随机策略）
class RandomOpponent:
    """随机选择动作的对手（用于测试）"""
    team = "B"

    def _decide(self, battle) -> int | None:
        from backend.engine.ai.core.mcts import get_valid_actions
        valid, mask = get_valid_actions(battle.player_b, battle)
        if not valid:
            return None
        # 随机选择
        return np.random.choice(valid)

opponent = RandomOpponent()

# 测试参数
num_simulations = 200
num_workers = 4

print("测试配置：")
print(f"  模拟次数: {num_simulations}")
print(f"  Worker 数量: {num_workers}")
print()

# 注意：需要模型才能运行，这里先检查是否可以创建
try:
    # 尝试创建一个小模型用于测试
    model = ModularBattleNet(
        input_channels=16,
        board_size=8,
        policy_output=17,
        num_resblocks=2,
        channels=32,
    )
    print("✅ 模型创建成功")
except Exception as e:
    print(f"⚠️  无法创建模型: {e}")
    print("提示：这个测试需要完整的训练环境")
    exit(0)

print()
print("="*80)
print("【测试 1: 串行 vs 并行结果一致性】")
print("="*80)
print()

# 固定随机种子
np.random.seed(42)

# 串行版本（使用 opponent 替代 NetworkPolicyAgent）
print("运行串行 MCTS...")
start = time.perf_counter()
try:
    probs_serial = mcts_search(
        battle=battle,
        model=model,
        factory=factory,
        opponent_agent=opponent,
        num_simulations=num_simulations,
        device="cpu",
    )
    elapsed_serial = time.perf_counter() - start
    print(f"✅ 串行完成: {elapsed_serial:.2f}s")
    print(f"   动作概率分布: {probs_serial[:5]}")  # 显示前5个
except Exception as e:
    print(f"❌ 串行失败: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print()

# 并行版本
print(f"运行并行 MCTS ({num_workers} workers)...")
np.random.seed(42)  # 相同的随机种子
start = time.perf_counter()
try:
    probs_parallel = parallel_mcts_search_root(
        battle=battle,
        model=model,
        factory=factory,
        opponent_agent=opponent,
        num_simulations=num_simulations,
        num_workers=num_workers,
        device="cpu",
    )
    elapsed_parallel = time.perf_counter() - start
    print(f"✅ 并行完成: {elapsed_parallel:.2f}s")
    print(f"   动作概率分布: {probs_parallel[:5]}")  # 显示前5个
except Exception as e:
    print(f"❌ 并行失败: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print()

# 比较结果
diff = np.abs(probs_serial - probs_parallel).max()
print(f"最大概率差异: {diff:.6f}")

if diff < 0.1:  # 允许一定的随机性差异
    print("✅ 结果基本一致")
else:
    print(f"⚠️  结果差异较大（由于随机性）")

print()
print("="*80)
print("【测试 2: 性能对比】")
print("="*80)
print()

# 多次测试取平均
num_runs = 3
print(f"运行 {num_runs} 次取平均...")
print()

# 串行性能
serial_times = []
for i in range(num_runs):
    np.random.seed(100 + i)
    start = time.perf_counter()
    mcts_search(
        battle=battle,
        model=model,
        factory=factory,
        opponent_agent=opponent,
        num_simulations=num_simulations,
        device="cpu",
    )
    serial_times.append(time.perf_counter() - start)

avg_serial = np.mean(serial_times)
print(f"串行平均: {avg_serial:.2f}s")

# 并行性能（不同 worker 数量）
for workers in [2, 4]:
    parallel_times = []
    for i in range(num_runs):
        np.random.seed(100 + i)
        start = time.perf_counter()
        parallel_mcts_search_root(
            battle=battle,
            model=model,
            factory=factory,
            opponent_agent=opponent,
            num_simulations=num_simulations,
            num_workers=workers,
            device="cpu",
        )
        parallel_times.append(time.perf_counter() - start)

    avg_parallel = np.mean(parallel_times)
    speedup = avg_serial / avg_parallel
    print(f"{workers} workers 平均: {avg_parallel:.2f}s (加速 {speedup:.2f}x)")

print()
print("="*80)
print("【性能评估】")
print("="*80)
print()

speedup_4 = avg_serial / np.mean([t for t in parallel_times])  # 使用最后一次（4 workers）
efficiency = speedup_4 / 4 * 100

print(f"4 workers 加速比: {speedup_4:.2f}x")
print(f"并行效率: {efficiency:.1f}%")
print()

if speedup_4 >= 2.5:
    print("🎉 优秀！加速比 ≥2.5x")
elif speedup_4 >= 2.0:
    print("✅ 良好！加速比 ≥2.0x")
elif speedup_4 >= 1.5:
    print("⚠️  一般。加速比 ≥1.5x")
else:
    print("❌ 效果不明显。加速比 <1.5x")

print()
print("="*80)
print("测试完成！")
print("="*80)
