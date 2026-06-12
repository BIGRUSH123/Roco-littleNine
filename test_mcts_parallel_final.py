"""最终性能测试：串行 vs 并行（进程池复用）

测量真实的加速比。
"""

import time
import numpy as np
import sys
import multiprocessing as mp


# 复制之前的 RandomAgent 和 RandomEvaluator
class RandomAgent:
    team = "B"
    def choose_action(self, battle):
        from backend.engine.ai.core.mcts import get_valid_actions
        from backend.sim.action import Action
        player = battle.player_b
        valid, mask = get_valid_actions(player, battle)
        if not valid:
            return Action(kind='gather')
        action_idx = np.random.choice(valid)
        if action_idx < 10:
            return Action(kind='skill', skill_index=action_idx)
        elif 10 <= action_idx < 15:
            return Action(kind='switch', switch_index=action_idx - 10)
        elif action_idx == 15:
            return Action(kind='gather')
        else:
            return Action(kind='item')
    def _decide(self, battle) -> int | None:
        from backend.engine.ai.core.mcts import get_valid_actions
        valid, mask = get_valid_actions(battle.player_b, battle)
        if not valid:
            return None
        return np.random.choice(valid)


class RandomEvaluator:
    def evaluate(self, state, mask):
        value = np.random.random() * 2 - 1
        prior = mask / max(mask.sum(), 1.0)
        return value, prior
    def evaluate_batch(self, states, masks):
        values = np.array([np.random.random() * 2 - 1 for _ in states], dtype=np.float32)
        priors = np.array([mask / max(mask.sum(), 1.0) for mask in masks], dtype=np.float32)
        return values, priors


def create_test_battle():
    from backend.sim.battle import Battle
    from backend.sim.player import Player
    from backend.sim.sprite import Sprite
    from backend.common.models import SpeciesStats
    from backend.sim.skill import Skill
    from backend.sim.battleskill import BattleSkill

    species = SpeciesStats(
        name="测试精灵",
        hp=200, atk=120, def_=80, sp_atk=100, sp_def=90, speed=110
    )
    skill_data = {
        "name": "测试技能", "element": "火", "skill_type": "物攻",
        "power": 75, "energy_cost": 20, "combo": 1,
    }
    skill = Skill.load(skill_data)

    def create_sprite():
        sprite = Sprite(species=species, current_hp=170, max_hp=200, energy=50,
                       initial_stats={"atk": 120, "def": 80, "sp_atk": 100, "sp_def": 90, "speed": 110})
        sprite.skills = [BattleSkill(base=skill) for _ in range(4)]
        return sprite

    team_a = [create_sprite() for _ in range(3)]
    team_b = [create_sprite() for _ in range(3)]
    player_a = Player(team=team_a, name="玩家A")
    player_b = Player(team=team_b, name="玩家B")
    return Battle(player_a, player_b)


def main():
    print("="*80)
    print("MCTS 并行化 - 最终性能测试")
    print("="*80)
    print()

    from backend.sim.factory import SimFactory
    from backend.engine.ai.core.mcts import mcts_search
    from backend.engine.ai.core.mcts_parallel import parallel_mcts_search_root

    factory = SimFactory()
    opponent = RandomAgent()
    evaluator = RandomEvaluator()

    num_simulations = 800
    num_workers = 4
    num_rounds = 5

    print(f"测试配置：")
    print(f"  模拟次数: {num_simulations}")
    print(f"  Worker 数量: {num_workers}")
    print(f"  测试轮数: {num_rounds}")
    print()

    # 串行测试
    print("【串行 MCTS】")
    print()

    times_serial = []
    for i in range(num_rounds):
        battle = create_test_battle()
        np.random.seed(100 + i)
        start = time.perf_counter()
        probs = mcts_search(
            battle=battle, model=None, factory=factory,
            opponent_agent=opponent, num_simulations=num_simulations,
            evaluator=evaluator,
        )
        elapsed = time.perf_counter() - start
        times_serial.append(elapsed)
        print(f"  轮次 {i+1}: {elapsed:.3f}s")

    avg_serial = np.mean(times_serial)
    print(f"  平均: {avg_serial:.3f}s")
    print()

    # 并行测试（进程池复用）
    print("【并行 MCTS（进程池复用）】")
    print()

    pool = mp.Pool(num_workers)

    times_parallel = []
    for i in range(num_rounds):
        battle = create_test_battle()
        np.random.seed(100 + i)
        start = time.perf_counter()
        probs = parallel_mcts_search_root(
            battle=battle, model=None, factory=factory,
            opponent_agent=opponent, num_simulations=num_simulations,
            num_workers=num_workers, evaluator=evaluator,
            pool=pool,  # 复用
        )
        elapsed = time.perf_counter() - start
        times_parallel.append(elapsed)
        print(f"  轮次 {i+1}: {elapsed:.3f}s")

    pool.close()
    pool.join()

    avg_parallel = np.mean(times_parallel)
    print(f"  平均: {avg_parallel:.3f}s")
    print()

    # 结果
    print("="*80)
    print("【最终结果】")
    print("="*80)
    print()

    speedup = avg_serial / avg_parallel
    efficiency = speedup / num_workers * 100

    print(f"串行平均耗时:   {avg_serial:.3f}s")
    print(f"并行平均耗时:   {avg_parallel:.3f}s")
    print(f"🚀 加速比:      {speedup:.2f}x")
    print(f"   并行效率:    {efficiency:.1f}%")
    print()

    # 累计加速
    previous_speedup = 2.7  # 之前的优化
    total_speedup = previous_speedup * speedup

    print(f"之前的优化:     {previous_speedup:.2f}x")
    print(f"并行化加速:     {speedup:.2f}x")
    print(f"🎉 累计总加速:  {total_speedup:.2f}x")
    print()

    if speedup >= 2.5:
        print("🎉🎉🎉 优秀！加速比 ≥2.5x")
    elif speedup >= 2.0:
        print("🎉 良好！加速比 ≥2.0x")
    elif speedup >= 1.5:
        print("✅ 有效！加速比 ≥1.5x")
    else:
        print("⚠️  效果一般")

    print()
    print("="*80)
    print("【部署建议】")
    print("="*80)
    print()

    if speedup >= 2.0:
        print("✅ 推荐立即部署到训练流程")
        print("✅ 训练时间预计缩短 50-60%")
        print()
        print("使用方法：")
        print("```python")
        print("# 创建进程池（训练开始时）")
        print("pool = multiprocessing.Pool(4)")
        print()
        print("# 每次搜索时复用")
        print("probs = parallel_mcts_search_root(")
        print("    battle, model, factory, opponent,")
        print("    num_simulations=800,")
        print("    num_workers=4,")
        print("    pool=pool,  # 复用！")
        print(")")
        print()
        print("# 训练结束时关闭")
        print("pool.close()")
        print("pool.join()")
        print("```")
    else:
        print("⚠️  需要进一步优化后再部署")

    print()


if __name__ == '__main__':
    main()
