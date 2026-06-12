"""快速性能验证：优化后 Observer 编译的实战测试

测试配置：小规模快速迭代，重点验证回合执行速度
预计完成时间: 2-5 分钟
"""
import subprocess
import sys
import time
from datetime import datetime

# 测试参数（小规模，快速完成）
BATTLES = 50          # 自我博弈局数（足够统计，又不会太久）
SIMS = 200            # MCTS 模拟次数（标准配置，体现 observer 触发密集度）
ITERATIONS = 1        # 只跑 1 轮迭代（我们关心单轮速度，不是训练收敛）
EPOCHS = 3            # 训练 epoch 少一点（重点测自我博弈速度）
BATCH_SIZE = 128      # 标准 batch
WORKERS = 1           # 单线程（避免并发干扰，纯测引擎速度）
LEAF_BATCH = 16       # 叶节点批量大小（GPU 批推理）

RUN_NAME = f"speed_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

print("=" * 60)
print("Observer 编译优化性能验证")
print("=" * 60)
print()
print("配置:")
print(f"  - 对局数: {BATTLES}")
print(f"  - MCTS 模拟: {SIMS} 次/步")
print(f"  - 迭代轮数: {ITERATIONS}")
print(f"  - Worker: {WORKERS} (单线程)")
print(f"  - Leaf batch: {LEAF_BATCH}")
print()
print("预计完成时间: 2-5 分钟")
print()

# 运行训练
cmd = [
    sys.executable,
    "backend/engine/ai/train.py",
    "--battles", str(BATTLES),
    "--sims", str(SIMS),
    "--iterations", str(ITERATIONS),
    "--epochs", str(EPOCHS),
    "--batch-size", str(BATCH_SIZE),
    "--workers", str(WORKERS),
    "--leaf-batch-size", str(LEAF_BATCH),
    "--eval-games", "0",
    "--progress-every", "10",
    "--run-name", RUN_NAME,
    "--output", f"checkpoints/{RUN_NAME}/model.pt",
]

print("=" * 60)
print("开始训练...")
print("=" * 60)
print()

start_time = time.time()
result = subprocess.run(cmd, cwd=".")
elapsed = time.time() - start_time

print()
print("=" * 60)
print("测试完成！")
print("=" * 60)
print()
print(f"总耗时: {elapsed:.1f}秒")
print()
print("查看日志:")
print(f"  - 全量日志: backend/engine/ai/log/{RUN_NAME}/run.log")
print(f"  - 结构化指标: backend/engine/ai/log/{RUN_NAME}/summary.json")
print()
print("关键指标:")
print("  - selfplay_time: 自我博弈总耗时（体现 observer 触发性能）")
print("  - avg_game_time: 单局平均耗时")
print("  - avg_turns: 平均回合数")
print()

# 尝试读取 summary.json 显示关键指标
try:
    import json
    with open(f"backend/engine/ai/log/{RUN_NAME}/summary.json", encoding="utf-8") as f:
        summary = json.load(f)

    print("实测指标:")
    if "iterations" in summary and len(summary["iterations"]) > 0:
        iter_data = summary["iterations"][0]
        print(f"  - 自我博弈耗时: {iter_data.get('selfplay_time_s', 'N/A'):.2f}秒")
        print(f"  - 单局平均耗时: {iter_data.get('avg_game_time_s', 'N/A'):.3f}秒")
        print(f"  - 平均回合数: {iter_data.get('avg_turns', 'N/A'):.1f}")
        print(f"  - 总样本数: {iter_data.get('samples', 'N/A')}")
except Exception as e:
    print(f"(无法读取 summary.json: {e})")
    print()

sys.exit(result.returncode)
