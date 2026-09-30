#!/bin/bash
# 自博弈验证 v2 —— 修掉"单局 wall-clock 上限吃样本"之后再跑
#
# 与上一版的差异（每条都对应一次实测）：
#   1. `--game-budget-s 900`：450s 时代 sims=400 有 45% 的局超时被丢弃（每轮可用样本塌到 ~5800，
#      门控分掉到 44.5%）；放宽后这些局能打完、进入训练样本。
#   2. `--battles 300`（上版 150）：把每轮样本量补回来。
#   3. `--sims` 由一次"搜索 vs 策略"扫描选定 —— sims 太低（100 → 0.5175）搜索打不过策略、
#      目标没有可学增量；太高（400）每局太慢。用法：bash run_rl_v2.sh <sims> [rounds] [battles]
set -u
cd /mnt/workspace/roco_remote || exit 1
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
SIMS=${1:-200}
ROUNDS=${2:-2}
BATTLES=${3:-300}
INIT=bc20000_clean.pt
RUN=rl_v2_s${SIMS}
LOGDIR_RL=checkpoints/${RUN}
mkdir -p logs "$LOGDIR_RL"

if [ ! -f "$INIT" ]; then echo "!! 缺 $INIT"; exit 1; fi

if [ ! -f "$LOGDIR_RL/DONE" ]; then
  echo "[v2] $(date '+%h-%d %H:%M') sims=$SIMS rounds=$ROUNDS battles=$BATTLES budget=900s"
  python -u -X utf8 -m backend.engine.ai.train --bc-init "$INIT" \
    --run-name "$RUN" \
    --batched-inference --workers 16 --eval-workers 16 \
    --iterations "$ROUNDS" --battles "$BATTLES" --sims "$SIMS" \
    --game-budget-s 900 \
    --eval-games 300 --eval-sims 100 --gate 0.52 --mirror-frac 0.2 \
    > "logs/${RUN}.log" 2>&1
  echo "[v2] done rc=$? $(date '+%h-%d %H:%M')"
  [ -f "$LOGDIR_RL/model_rl.pt" ] && touch "$LOGDIR_RL/DONE"
fi
echo RL_V2_DONE
