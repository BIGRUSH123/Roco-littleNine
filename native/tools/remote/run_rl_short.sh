#!/bin/bash
# 缩水版 C 段（远程只剩 6 小时窗口）：等 B 段权重出现 → 2 轮 × 150 局 × sims=400 自博弈
#                                  → 门控 200 局 @0.52，产物落 checkpoints/rl_short/
#
# 为什么砍：自博弈成本 ∝ 轮数 × 局数 × sims。sims=400 是**实测**能让搜索强于策略的
# 唯一档位（0.7425 vs 0.5175），必须保留；轮数/局数/门控局数是唯一可砍的维度。
# 判据：门控分是否离开 50%±（≥0.52 至少晋升一次）。逐轮检查点都会留存
# （checkpoints/rl_short/model_rl_iter{1,2}.pt），即使窗口用尽也能在新实例上用
# 高分辨率配对评估补测。
set -u
cd /mnt/workspace/roco_remote || exit 1
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
INIT=bc20000_clean.pt
RLDIR=checkpoints/rl_short
mkdir -p logs "$RLDIR"

while [ ! -f "$INIT" ]; do
  echo "[wait] $(date '+%h-%d %H:%M') 等 $INIT ..."
  sleep 60
done

if [ ! -f "$RLDIR/DONE" ]; then
  echo "[C'] $(date '+%h-%d %H:%M') 自博弈 2 轮 × 150 局 × sims=400，门控 200 局 @0.52"
  python -u -X utf8 -m backend.engine.ai.train --bc-init "$INIT" \
    --run-name rl_short \
    --iterations 2 --battles 150 --sims 400 --workers 16 --eval-workers 16 \
    --eval-games 200 --eval-sims 100 --gate 0.52 --mirror-frac 0.2 \
    > logs/rl_short.log 2>&1
  echo "[C'] done rc=$? $(date '+%h-%d %H:%M')"
  [ -f "$RLDIR/model_rl.pt" ] && touch "$RLDIR/DONE"
fi
echo RL_SHORT_DONE
