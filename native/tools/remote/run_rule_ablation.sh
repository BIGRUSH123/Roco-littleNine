#!/bin/bash
# 逐队"全局规则消融"：对每支队跑 status / trade / antiloop 三个模式的成对 A/B
#   · 每个模式 = "该规则按出厂值 ON" vs "OFF"（其余规则两侧一致）
#   · WR < 0.5 表示**这条全场规则对这支队有害** → 新地武那类"队级例外"的候选
# 用法: bash run_rule_ablation.sh [局数(默认120)] [队伍...]
cd /mnt/workspace/roco_remote || exit 1
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
GAMES=${1:-120}; shift || true
TEAMS=("$@")
if [ ${#TEAMS[@]} -eq 0 ]; then
  TEAMS=("新愿力火" "铁头海豹平衡队" "羽刃翼王铁头队" "沙包五" "队伍5" "火队" "恶魔狼队" "新版本龙火队")
fi
mkdir -p ablate logs
for t in "${TEAMS[@]}"; do
  (
    for mode in status trade antiloop; do
      python -X utf8 native/tools/eval_expert_change.py --ab "$mode" --games "$GAMES" \
        --max-turns 60 --only-team "$t" --per-team \
        --json-out "ablate/${t}_${mode}.json" > "logs/ablate_${t}_${mode}.log" 2>&1
    done
  ) &
done
wait
echo "ALL_DONE 消融扫描"
