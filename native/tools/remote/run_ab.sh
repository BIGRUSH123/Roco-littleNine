#!/bin/bash
# 逐阵容 A/B 批量驱动：MODE GAMES TURNS SHARDS 四参数
cd /mnt/workspace/roco_remote || exit 1
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
MODE=${1:-shipped}; GAMES=${2:-120}; TURNS=${3:-60}; SHARDS=${4:-8}
mkdir -p ab logs
i=0
while [ "$i" -lt "$SHARDS" ]; do
  (
    python -X utf8 ab_per_team.py --ab "$MODE" --games "$GAMES" \
      --max-turns "$TURNS" --shards "$SHARDS" --shard "$i" \
      > "logs/ab_${MODE}_s${i}.log" 2>&1
  ) &
  i=$((i + 1))
done
wait
echo "ALL_DONE $MODE"
