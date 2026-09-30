#!/bin/bash
# C 段（修正版）：等 B 段**真正跑完**（sidecar json 出现，而不是 .pt——bc_pretrain 每个
# epoch 都会覆盖保存 --out，用 .pt 判断会提前开工）→ 2 轮 × 150 局 × sims=400 自博弈。
#
# 与上一版的关键修正：**必须带 `--batched-inference`**。
# train.py 里并行自博弈的开关是 `use_parallel = args.batched_inference and args.workers > 1`，
# 只给 `--workers 16` 会**静默退回单进程串行**（上一版就踩了这个坑：400 sims 串行 150 局
# 要十几小时，日志里那行 `串行` 是唯一线索）。
set -u
cd /mnt/workspace/roco_remote || exit 1
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
INIT=bc20000_clean.pt
SIDECAR=bc20000_clean.json
RLDIR=checkpoints/rl_short2
mkdir -p logs "$RLDIR"

while [ ! -f "$SIDECAR" ]; do
  echo "[wait] $(date '+%h-%d %H:%M') 等 $SIDECAR（=B 段跑完）..."
  sleep 60
done
echo "[start] $(date '+%h-%d %H:%M') B 段完成，开始 C 段"

if [ ! -f "$RLDIR/DONE" ]; then
  python -u -X utf8 -m backend.engine.ai.train --bc-init "$INIT" \
    --run-name rl_short2 \
    --batched-inference --workers 16 --eval-workers 16 \
    --iterations 2 --battles 150 --sims 400 \
    --eval-games 300 --eval-sims 100 --gate 0.52 --mirror-frac 0.2 \
    > logs/rl_short2.log 2>&1
  echo "[C''] done rc=$? $(date '+%h-%d %H:%M')"
  [ -f "$RLDIR/model_rl.pt" ] && touch "$RLDIR/DONE"
fi
echo RL_SHORT2_DONE
