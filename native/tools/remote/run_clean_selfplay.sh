#!/bin/bash
# 干净世界：重生成 BC 数据 → 预训练 → sims=400 自博弈验证（分阶段可续跑）
#
# 背景：2026-09-24 实测「搜索(100 sims) 打不过自己的策略头」（0.5175），
# 而 sims=400 时搜索强 24 个点（0.7425）→ 自博弈要出效果必须先把搜索预算提上去。
# 本流水线先在**当前世界**（解僵局修复 + 迸发/重放两处引擎修复 + 41 支 meta 队）重造
# BC 数据与初始化，再用 sims=400 跑自博弈，判据是"门控分是否离开 50%±"。
#
# 每段产物存在即跳过 → pod 重启后直接重跑本脚本即从断点续。
# 用法（远端）: bash run_clean_selfplay.sh
set -u
cd /mnt/workspace/roco_remote || exit 1
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
DATA=bc_20000_clean.npz
INIT=bc20000_clean.pt
RLDIR=checkpoints/rl_sims400
mkdir -p logs "$RLDIR"

if [ ! -f "$DATA" ]; then
  echo "[A] 生成 BC 数据 20000 局（干净世界，seed 2026）..."
  python -u -X utf8 native/tools/gen_bc_data.py --games 20000 --meta-frac 0.6 \
    --mirror-frac 0.15 --optimal-frac 0.95 --max-turns 60 --seed 2026 --workers 20 \
    --out "$DATA" --log-file logs/gen_20000_clean.log > logs/gen_20000_stdout.log 2>&1
  echo "[A] done rc=$? size=$(stat -c%s "$DATA" 2>/dev/null)"
else
  echo "[A] 已有 $DATA，跳过"
fi

if [ ! -f "$INIT" ]; then
  echo "[B] BC 预训练（holdout 4 / 6 epoch / seed 7）..."
  python -u -X utf8 -m backend.engine.ai.bc_pretrain --data "$DATA" --out "$INIT" \
    --holdout-teams 4 --epochs 6 --seed 7 > logs/bc_20000_clean.log 2>&1
  echo "[B] done rc=$?"
else
  echo "[B] 已有 $INIT，跳过"
fi

if [ ! -f "$RLDIR/DONE" ]; then
  echo "[C] 自博弈验证：4 轮 × 200 局 × sims=400，门控 400 局 @0.52 ..."
  # 产物目录由 --run-name 派生（checkpoints/<run-name>/）；原 `--output` 参数
  # 已于 2026-09-24 删除（RL 分支从不读它）。
  python -u -X utf8 -m backend.engine.ai.train --bc-init "$INIT" \
    --run-name rl_sims400 \
    --iterations 4 --battles 200 --sims 400 --workers 16 --eval-workers 16 \
    --eval-games 400 --eval-sims 100 --gate 0.52 --mirror-frac 0.2 \
    > logs/rl_sims400.log 2>&1
  rc=$?
  echo "[C] done rc=$rc"
  [ -f "$RLDIR/model_rl.pt" ] && touch "$RLDIR/DONE"
else
  echo "[C] 已有 $RLDIR/DONE，跳过"
fi

echo ALL_STAGES_DONE
