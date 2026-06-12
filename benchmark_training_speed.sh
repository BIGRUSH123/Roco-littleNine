#!/bin/bash
# 快速性能验证：优化后 Observer 编译的实战测试
# 测试配置：小规模快速迭代，重点验证回合执行速度

cd "$(dirname "$0")"

echo "========================================"
echo "Observer 编译优化性能验证"
echo "========================================"
echo ""

# 测试参数（小规模，快速完成）
BATTLES=50          # 自我博弈局数（足够统计，又不会太久）
SIMS=200            # MCTS 模拟次数（标准配置，体现 observer 触发密集度）
ITERATIONS=1        # 只跑 1 轮迭代（我们关心单轮速度，不是训练收敛）
EPOCHS=3            # 训练 epoch 少一点（重点测自我博弈速度）
BATCH_SIZE=128      # 标准 batch
WORKERS=1           # 单线程（避免并发干扰，纯测引擎速度）
LEAF_BATCH=16       # 叶节点批量大小（GPU 批推理）

RUN_NAME="speed_test_$(date +%Y%m%d_%H%M%S)"

echo "配置:"
echo "  - 对局数: $BATTLES"
echo "  - MCTS 模拟: $SIMS 次/步"
echo "  - 迭代轮数: $ITERATIONS"
echo "  - Worker: $WORKERS (单线程)"
echo "  - Leaf batch: $LEAF_BATCH"
echo ""
echo "预计完成时间: 2-5 分钟"
echo ""

# 运行训练
python backend/engine/ai/train.py \
    --battles $BATTLES \
    --sims $SIMS \
    --iterations $ITERATIONS \
    --epochs $EPOCHS \
    --batch-size $BATCH_SIZE \
    --workers $WORKERS \
    --leaf-batch-size $LEAF_BATCH \
    --eval-games 0 \
    --progress-every 10 \
    --run-name "$RUN_NAME" \
    --output "checkpoints/$RUN_NAME/model.pt"

echo ""
echo "========================================"
echo "测试完成！"
echo "========================================"
echo ""
echo "查看日志:"
echo "  cat backend/engine/ai/log/$RUN_NAME/summary.json"
echo ""
echo "关键指标:"
echo "  - selfplay_time: 自我博弈总耗时（体现 observer 触发性能）"
echo "  - avg_game_time: 单局平均耗时"
echo "  - avg_turns: 平均回合数"
echo ""
