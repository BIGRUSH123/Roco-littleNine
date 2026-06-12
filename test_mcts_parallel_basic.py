"""简单测试 MCTS 并行化的基本功能

不依赖完整的训练环境，只测试并行框架是否工作。
"""

import time
import numpy as np
import sys


def worker_test(worker_id, value):
    """简单的 worker 函数（必须在模块级别定义）"""
    return worker_id * value


def main():
    print("="*80)
    print("MCTS 并行化 - 基础功能测试")
    print("="*80)
    print()

    # 检查导入
    try:
        from backend.engine.ai.core.mcts_parallel import parallel_mcts_search_root
        print("✅ 导入 mcts_parallel 成功")
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        sys.exit(1)

    try:
        from backend.engine.ai.core.mcts import mcts_search, get_valid_actions
        print("✅ 导入 mcts 成功")
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        sys.exit(1)

    try:
        from backend.sim.factory import SimFactory
        print("✅ 导入 factory 成功")
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        sys.exit(1)

    print()
    print("="*80)
    print("【测试 1: Pickle 序列化测试】")
    print("="*80)
    print()

    # 创建一个简单的测试对象
    try:
        import pickle
        from backend.sim.battle import Battle
        from backend.sim.player import Player
        from backend.sim.sprite import Sprite
        from backend.common.models import SpeciesStats

        # 创建测试精灵
        species = SpeciesStats(
            name="测试精灵",
            hp=200,
            atk=120,
            def_=80,
            sp_atk=100,
            sp_def=90,
            speed=110
        )

        sprite = Sprite(
            species=species,
            current_hp=170,
            max_hp=200,
            energy=50,
            initial_stats={"atk": 120, "def": 80, "sp_atk": 100, "sp_def": 90, "speed": 110}
        )

        print(f"创建精灵: {sprite.species.name}, HP={sprite.current_hp}")

        # 测试 pickle
        pickled = pickle.dumps(sprite)
        restored = pickle.loads(pickled)
        print(f"恢复精灵: {restored.species.name}, HP={restored.current_hp}")

        if sprite.current_hp == restored.current_hp:
            print("✅ Pickle 序列化成功")
        else:
            print("❌ Pickle 序列化失败")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("="*80)
    print("【测试 2: 多进程基础测试】")
    print("="*80)
    print()

    try:
        import multiprocessing as mp

        with mp.Pool(2) as pool:
            results = pool.starmap(worker_test, [(0, 10), (1, 10)])

        print(f"Worker 结果: {results}")
        if results == [0, 10]:
            print("✅ 多进程基础功能正常")
        else:
            print("❌ 多进程结果不符合预期")

    except Exception as e:
        print(f"❌ 多进程测试失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("="*80)
    print("【总结】")
    print("="*80)
    print()
    print("基础功能测试完成。")
    print()
    print("下一步：")
    print("1. 实现完整的 Battle pickle 支持")
    print("2. 创建端到端的并行 MCTS 测试")
    print("3. 性能基准测试")


if __name__ == '__main__':
    main()
