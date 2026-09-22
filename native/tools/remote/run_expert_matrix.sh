#!/bin/bash
# 逐规则矩阵：每条专精规则单独开、全开、全关（=纯通用基线），各跑 N 局
# 用法: bash run_expert_matrix.sh <队伍> <局数> [输出前缀]
cd /mnt/workspace/roco_remote || exit 1
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
TEAM=${1:-星陨队}; GAMES=${2:-120}; PREFIX=${3:-matrix}
mkdir -p expert logs

case "$TEAM" in
  星陨队) RULES="detonate_lethal attack_over_wait insight protect_dispel hold_generator" ;;
  魔偶雨天队) RULES="rain_first borrow_slot charge_combo rain_water" ;;
  首领毒) RULES="venom_cheap water_spreads spread_poison hold_converter" ;;
  新地武) RULES="counter_setup invest_cost off_status_counter off_trade" ;;
  【搬运】黑影平衡毒) RULES="punish_rotation spread_poison no_idle_rotate infect_finisher" ;;
  *) echo "未知队伍 $TEAM"; exit 1 ;;
esac

run() {  # $1=标签  $2=规则串（空=无规则）
  local tag="$1"; local r="$2"
  (
    if [ -z "$r" ]; then
      python -X utf8 native/tools/eval_team_expert.py --team "$TEAM" --games "$GAMES" \
        --max-turns 60 --json-out "expert/${PREFIX}_${tag}.json" > "logs/${PREFIX}_${tag}.log" 2>&1
    else
      python -X utf8 native/tools/eval_team_expert.py --team "$TEAM" --games "$GAMES" \
        --max-turns 60 --rules "$r" --json-out "expert/${PREFIX}_${tag}.json" \
        > "logs/${PREFIX}_${tag}.log" 2>&1
    fi
  ) &
}

run none "none"                  # 显式全关 = 与通用专家同规则（应≈0.5，作对照）
for r in $RULES; do run "$r" "$r"; done
run all "$(echo $RULES | tr ' ' ',')"
wait
echo "ALL_DONE $TEAM"
