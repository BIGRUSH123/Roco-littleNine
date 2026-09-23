#!/bin/bash
# BC 两臂对照：同协议各训一遍（holdout-teams 4 / epochs 6 / seed 7），再打印结论。
# 用法: bash /mnt/workspace/run_bc_arms.sh [旧臂数据] [新臂数据]
# 说明: 两臂**同时**训，各限 11 线程避免互相抢核（数值路径不变，只影响耗时）。
set -u
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
OLD=${1:-/mnt/workspace/roco_old/bc_old_10000.npz}
NEW=${2:-/mnt/workspace/roco_remote/bc_new_10000.npz}
mkdir -p /mnt/workspace/logs

train() {  # $1=工作目录 $2=数据 $3=产出  $4=日志
  cd "$1" || exit 1
  OMP_NUM_THREADS=11 MKL_NUM_THREADS=11 python -X utf8 -m backend.engine.ai.bc_pretrain \
    --data "$2" --out "$3" --holdout-teams 4 --epochs 6 --seed 7 > "$4" 2>&1
  echo "DONE $3 rc=$?"
}

train /mnt/workspace/roco_old "$OLD" old_10000.pt /mnt/workspace/logs/train_old_10000.log &
train /mnt/workspace/roco_remote "$NEW" new_10000.pt /mnt/workspace/logs/train_new_10000.log &
wait

echo "=== 世界与标签指标 ==="
cd /mnt/workspace && python -X utf8 bc_arm_stats.py "$OLD" "$NEW"
echo
echo "=== 训练结论对照 ==="
python -X utf8 /mnt/workspace/bc_arm_compare.py \
  /mnt/workspace/roco_old/old_10000.json /mnt/workspace/roco_remote/new_10000.json
echo "ALL_DONE"
