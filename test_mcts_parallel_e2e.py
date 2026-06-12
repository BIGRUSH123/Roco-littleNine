"""端到端 MCTS 并行化测试

使用随机对手（不需要模型）测试并行化的正确性和性能。
"""

import time
import numpy as np
import sys

# 简单的随机对手（不需要神经网络）
class RandomAgent:
    """随机选择动作的对手"""
    team = "B"

    def choose_action(self, battle):
        """返回 Action 对象"""
        from backend.engine.ai.core.mcts import get_valid_actions
        from backend.sim.action import Action

        player = battle.player_b
        valid, mask = get_valid_actions(player, battle)

        if not valid:
            # 回退到聚能
            return Action(kind='gather')

        # 随机选择一个有效动作
        action_idx = np.random.choice(valid)

        # 转换索引到 Action
        if action_idx < 10:
            return Action(kind='skill', skill_index=action_idx)
        elif 10 <= action_idx < 15:
            return Action(kind='switch', switch_index=action_idx - 10)
        elif action_idx == 15:
            return Action(kind='gather')
        else:
            return Action(kind='item')

    def _decide(self, battle) -> int | None:
        """MCTS 内部使用的接口"""
        from backend.engine.ai.core.mcts import get_valid_actions
        valid, mask = get_valid_actions(battle.player_b, battle)
        if not valid:
            return None
        return np.random.choice(valid)


# 简单的随机 evaluator（不需要神经网络）
class RandomEvaluator:
    """随机评估器（替代神经网络）"""

    def evaluate(self, state, mask):
        """返回随机 value 和均匀先验"""
        value = np.random.random() * 2 - 1  # [-1, 1]
        # 均匀先验（只在有效动作上）
        prior = mask / max(mask.sum(), 1.0)
        return value, prior

    def evaluate_batch(self, states, masks):
        """批量评估"""
        values = np.array([np.random.random() * 2 - 1 for _ in states], dtype=np.float32)
        priors = np.array([mask / max(mask.sum(), 1.0) for mask in masks], dtype=np.float32)
        return values, priors


def main():
    print("="*80)
    print("MCTS 并行化 - 端到端测试")
    print("="*80)
    print()

    # 导入
    try:
        from backend.sim.battle import Battle
        from backend.sim.player import Player
        from backend.sim.sprite import Sprite
        from backend.sim.factory import SimFactory
        from backend.common.models import SpeciesStats
        from backend.sim.skill import Skill
        from backend.sim.battleskill import BattleSkill
        from backend.engine.ai.core.mcts import mcts_search
        from backend.engine.ai.core.mcts_parallel import parallel_mcts_search_root

        print("✅ 所有导入成功")
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("【步骤 1: 创建测试环境】")
    print()

    # 创建测试数据
    species = SpeciesStats(
        name="测试精灵",
        hp=200,
        atk=120,
        def_=80,
        sp_atk=100,
        sp_def=90,
        speed=110
    )

    skill_data = {
        "name": "测试技能",
        "element": "火",
        "skill_type": "物攻",
        "power": 75,
        "energy_cost": 20,
        "combo": 1,
    }
    skill = Skill.load(skill_data)

    def create_sprite():
        sprite = Sprite(
            species=species,
            current_hp=170,
            max_hp=200,
            energy=50,
            initial_stats={"atk": 120, "def": 80, "sp_atk": 100, "sp_def": 90, "speed": 110}
        )
        sprite.skills = [BattleSkill(base=skill) for _ in range(4)]
        return sprite

    team_a = [create_sprite() for _ in range(3)]
    team_b = [create_sprite() for _ in range(3)]

    player_a = Player(team=team_a, name="玩家A")
    player_b = Player(team=team_b, name="玩家B")
    battle = Battle(player_a, player_b)
    factory = SimFactory()

    print(f"  Battle 创建完成: turn={battle.turn}")
    print(f"  玩家 A: {len(player_a.team)} 个精灵")
    print(f"  玩家 B: {len(player_b.team)} 个精灵")

    # 创建随机对手和评估器
    opponent = RandomAgent()
    evaluator = RandomEvaluator()

    print(f"  对手: {opponent.__class__.__name__}")
    print(f"  评估器: {evaluator.__class__.__name__}")

    # 测试参数
    num_simulations = 800  # 增加模拟次数以分摊开销
    num_workers = 4

    print()
    print("【步骤 2: 运行串行 MCTS】")
    print()

    np.random.seed(42)
    start = time.perf_counter()
    try:
        probs_serial = mcts_search(
            battle=battle,
            model=None,
            factory=factory,
            opponent_agent=opponent,
            num_simulations=num_simulations,
            evaluator=evaluator,
            device="cpu",
        )
        elapsed_serial = time.perf_counter() - start
        print(f"  ✅ 串行完成: {elapsed_serial:.3f}s")
        print(f"  动作概率 (前5个): {probs_serial[:5]}")
        print(f"  概率和: {probs_serial.sum():.6f}")
    except Exception as e:
        print(f"  ❌ 串行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("【步骤 3: 运行并行 MCTS】")
    print()

    # 重置随机种子
    np.random.seed(42)
    # 创建新的 battle（确保状态一致）
    team_a2 = [create_sprite() for _ in range(3)]
    team_b2 = [create_sprite() for _ in range(3)]
    player_a2 = Player(team=team_a2, name="玩家A")
    player_b2 = Player(team=team_b2, name="玩家B")
    battle2 = Battle(player_a2, player_b2)

    start = time.perf_counter()
    try:
        probs_parallel = parallel_mcts_search_root(
            battle=battle2,
            model=None,
            factory=factory,
            opponent_agent=opponent,
            num_simulations=num_simulations,
            num_workers=num_workers,
            evaluator=evaluator,
            device="cpu",
        )
        elapsed_parallel = time.perf_counter() - start
        print(f"  ✅ 并行完成 ({num_workers} workers): {elapsed_parallel:.3f}s")
        print(f"  动作概率 (前5个): {probs_parallel[:5]}")
        print(f"  概率和: {probs_parallel.sum():.6f}")
    except Exception as e:
        print(f"  ❌ 并行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("【步骤 4: 结果对比】")
    print()

    # 比较结果（由于随机性，不会完全相同）
    diff = np.abs(probs_serial - probs_parallel)
    max_diff = diff.max()
    mean_diff = diff.mean()

    print(f"  最大差异: {max_diff:.6f}")
    print(f"  平均差异: {mean_diff:.6f}")

    # 检查两者都是有效的概率分布
    serial_valid = abs(probs_serial.sum() - 1.0) < 0.01
    parallel_valid = abs(probs_parallel.sum() - 1.0) < 0.01

    print(f"  串行概率和有效: {'✅' if serial_valid else '❌'}")
    print(f"  并行概率和有效: {'✅' if parallel_valid else '❌'}")

    # 由于是随机评估，结果会有差异，但应该都是有效分布
    if serial_valid and parallel_valid:
        print("  ✅ 两者都产生了有效的概率分布")
    else:
        print("  ❌ 概率分布无效")

    print()
    print("【步骤 5: 性能对比】")
    print()

    speedup = elapsed_serial / elapsed_parallel if elapsed_parallel > 0 else 0
    efficiency = speedup / num_workers * 100

    print(f"  串行耗时: {elapsed_serial:.3f}s")
    print(f"  并行耗时: {elapsed_parallel:.3f}s")
    print(f"  加速比: {speedup:.2f}x")
    print(f"  并行效率: {efficiency:.1f}%")

    print()
    print("="*80)
    print("【总结】")
    print("="*80)
    print()

    if serial_valid and parallel_valid:
        print("✅ 功能测试通过")
    else:
        print("❌ 功能测试失败")

    if speedup >= 2.0:
        print(f"🎉 性能优秀！加速比 {speedup:.2f}x ≥ 2.0x")
    elif speedup >= 1.5:
        print(f"✅ 性能良好！加速比 {speedup:.2f}x ≥ 1.5x")
    elif speedup >= 1.2:
        print(f"⚠️  性能一般。加速比 {speedup:.2f}x ≥ 1.2x")
    else:
        print(f"❌ 性能不佳。加速比 {speedup:.2f}x < 1.2x")

    print()
    print("注意：由于使用随机评估器，串行和并行的结果会有差异。")
    print("真实场景下（使用相同模型），结果应该更接近。")
    print()

    if speedup >= 1.5:
        print("🚀 并行化实现成功！可以集成到训练流程。")
    else:
        print("⚠️  需要进一步优化或调试。")

    print()


if __name__ == '__main__':
    main()
