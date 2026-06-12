"""
全管线性能分析工具 - AI训练 → 战斗引擎 → IR层

分析目标：
1. MCTS 自博弈（占训练 96.6% 时间）
2. 战斗引擎热路径
3. IR 编译和执行
4. 网络推理
"""
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

import cProfile
import pstats
import time
from io import StringIO

from backend.sim.factory import SimFactory
from backend.sim.battle import Battle
from backend.engine.ai.core.mcts import MCTS, MCTSConfig
from backend.engine.ai.core.neural_evaluator import DummyEvaluator

factory = SimFactory()


def profile_mcts_simulation():
    """分析 MCTS 模拟的性能瓶颈"""
    print("\n" + "=" * 80)
    print("分析 1: MCTS 模拟性能 (200 sims)")
    print("=" * 80)

    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
        {"name": "神谕鲨", "skills": ["甩水", "甩水", "甩水", "甩水"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    battle = Battle(p1, p2, verbose=False)
    evaluator = DummyEvaluator()
    config = MCTSConfig(num_simulations=200, cpuct=1.0)
    mcts = MCTS(config, evaluator)

    # Profile
    profiler = cProfile.Profile()
    profiler.enable()

    start = time.perf_counter()
    action, _ = mcts.search(battle, "A")
    elapsed = time.perf_counter() - start

    profiler.disable()

    # 分析
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s)
    ps.strip_dirs()
    ps.sort_stats('cumulative')
    ps.print_stats(30)

    print(f"\n总耗时: {elapsed:.3f}s")
    print(f"每次模拟: {elapsed/200*1000:.2f}ms")
    print(f"\nTop 30 热点函数 (按累计时间):")
    print(s.getvalue())

    # 按自身时间排序
    s2 = StringIO()
    ps2 = pstats.Stats(profiler, stream=s2)
    ps2.strip_dirs()
    ps2.sort_stats('tottime')
    ps2.print_stats(15)

    print(f"\nTop 15 热点函数 (按自身时间):")
    print(s2.getvalue())


def profile_battle_turn():
    """分析单回合战斗执行的性能"""
    print("\n" + "=" * 80)
    print("分析 2: 战斗回合执行性能 (1000 回合)")
    print("=" * 80)

    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    iterations = 1000

    profiler = cProfile.Profile()
    profiler.enable()

    start = time.perf_counter()
    for _ in range(iterations):
        battle = Battle(p1, p2, verbose=False)
        battle._mcts_sim = True

        from backend.vm.actions import Action
        action_a = Action(kind="skill", skill_index=0)
        action_b = Action(kind="skill", skill_index=0)

        battle._execute_turn_headless(action_a, action_b)
    elapsed = time.perf_counter() - start

    profiler.disable()

    # 分析
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s)
    ps.strip_dirs()
    ps.sort_stats('cumulative')
    ps.print_stats(30)

    print(f"\n总耗时: {elapsed:.3f}s")
    print(f"每回合: {elapsed/iterations*1000:.2f}ms")
    print(f"吞吐量: {iterations/elapsed:.0f} turns/s")
    print(f"\nTop 30 热点函数 (按累计时间):")
    print(s.getvalue())

    # 按自身时间
    s2 = StringIO()
    ps2 = pstats.Stats(profiler, stream=s2)
    ps2.strip_dirs()
    ps2.sort_stats('tottime')
    ps2.print_stats(15)

    print(f"\nTop 15 热点函数 (按自身时间):")
    print(s2.getvalue())


def profile_ir_execution():
    """分析 IR 执行性能"""
    print("\n" + "=" * 80)
    print("分析 3: IR 技能执行性能 (1000 次技能执行)")
    print("=" * 80)

    p1 = factory.build_player("A", [
        {"name": "草衣虫", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])
    p2 = factory.build_player("B", [
        {"name": "花衣蝶", "skills": ["猛烈撞击", "猛烈撞击", "猛烈撞击", "猛烈撞击"]},
    ])

    iterations = 1000

    profiler = cProfile.Profile()
    profiler.enable()

    start = time.perf_counter()
    for _ in range(iterations):
        battle = Battle(p1, p2, verbose=False)
        battle._mcts_sim = True

        from backend.vm.actions import Action
        action = Action(kind="skill", skill_index=0)

        # 直接执行技能（跳过回合管理）
        battle._execute_skill_vm("A", action)
    elapsed = time.perf_counter() - start

    profiler.disable()

    # 分析
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s)
    ps.strip_dirs()
    ps.sort_stats('cumulative')
    ps.print_stats(30)

    print(f"\n总耗时: {elapsed:.3f}s")
    print(f"每次技能: {elapsed/iterations*1000:.2f}ms")
    print(f"吞吐量: {iterations/elapsed:.0f} skills/s")
    print(f"\nTop 30 热点函数 (按累计时间):")
    print(s.getvalue())

    # 按自身时间
    s2 = StringIO()
    ps2 = pstats.Stats(profiler, stream=s2)
    ps2.strip_dirs()
    ps2.sort_stats('tottime')
    ps2.print_stats(15)

    print(f"\nTop 15 热点函数 (按自身时间):")
    print(s2.getvalue())


def profile_snapshot_operations():
    """分析快照和恢复性能"""
    print("\n" + "=" * 80)
    print("分析 4: 快照/恢复性能 (1000 次 save+restore)")
    print("=" * 80)

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

    iterations = 1000

    profiler = cProfile.Profile()
    profiler.enable()

    start = time.perf_counter()
    for _ in range(iterations):
        snapshot = battle.save_state()
        battle.restore_state(snapshot)
    elapsed = time.perf_counter() - start

    profiler.disable()

    # 分析
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s)
    ps.strip_dirs()
    ps.sort_stats('cumulative')
    ps.print_stats(30)

    print(f"\n总耗时: {elapsed:.3f}s")
    print(f"每次操作: {elapsed/iterations*1000:.2f}ms")
    print(f"吞吐量: {iterations/elapsed:.0f} ops/s")
    print(f"\nTop 30 热点函数 (按累计时间):")
    print(s.getvalue())


def analyze_training_bottlenecks():
    """综合分析训练瓶颈"""
    print("\n" + "=" * 80)
    print("分析 5: 训练管线瓶颈总结")
    print("=" * 80)

    print("\n根据之前的分析:")
    print("- 自博弈占训练时间: 96.6%")
    print("- 训练占时间: 3.4%")
    print("\n因此优化重点应在自博弈（MCTS + Battle）\n")

    print("关键瓶颈（按优先级）:")
    print("\n1. MCTS 模拟 (最高优先级)")
    print("   - 每次训练迭代: 200 模拟 × 200 游戏 = 40,000 次模拟")
    print("   - 每次模拟包含: 快照、回合执行、恢复")
    print("   - 优化方向: 减少不必要的深拷贝、优化快照格式")

    print("\n2. 战斗回合执行")
    print("   - 每次模拟: 10-50 回合")
    print("   - 每回合: 技能执行、状态更新、observer 触发")
    print("   - 优化方向: IR 执行热路径、observer 缓存")

    print("\n3. 快照/恢复")
    print("   - 每次模拟: 1 次保存 + N 次恢复（N = 树深度）")
    print("   - 优化方向: 增量快照、copy-on-write")

    print("\n4. IR 编译和执行")
    print("   - 已优化: 预编译技能/observer effects")
    print("   - 优化方向: 热点 IR 操作内联、条件分支优化")

    print("\n5. 网络推理 (如果使用)")
    print("   - 当前测试用 DummyEvaluator")
    print("   - 优化方向: 批量推理、GPU 加速")


if __name__ == "__main__":
    print("=" * 80)
    print("AI 训练全管线性能分析")
    print("=" * 80)

    # 1. MCTS 是主瓶颈
    try:
        profile_mcts_simulation()
    except Exception as e:
        print(f"MCTS 分析失败: {e}")

    # 2. 战斗回合执行
    try:
        profile_battle_turn()
    except Exception as e:
        print(f"战斗回合分析失败: {e}")

    # 3. IR 执行
    try:
        profile_ir_execution()
    except Exception as e:
        print(f"IR 执行分析失败: {e}")

    # 4. 快照/恢复
    try:
        profile_snapshot_operations()
    except Exception as e:
        print(f"快照分析失败: {e}")

    # 5. 总结
    analyze_training_bottlenecks()

    print("\n" + "=" * 80)
    print("分析完成！查看上方输出找到最值得优化的热点。")
    print("=" * 80)
