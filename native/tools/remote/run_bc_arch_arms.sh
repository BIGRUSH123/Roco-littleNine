#!/usr/bin/env bash
# run_bc_arch_arms.sh — 远端 BC **架构**对照：v4 基线 / v5(slot+aux) / v6(slot+aux+history)
#
# 与 run_bc_arms.sh（旧/新世界数据对照）的区别：本脚本三臂共用**同一份数据**、
# 同一 seed、同一超参，唯一变量是模型架构开关，所以 val top-1 直接可比。
#
# 设计要点
#   1) **可恢复**：某臂只要 sidecar json 已存在就跳过 —— 实例被停/重开后重跑不会
#      重做已完成的臂（2026-09-24 实例两次被回收，吃过这个亏）。
#   2) 顺序执行：单卡并行会互相拖慢，并行时 wall-time 也无法对比。
#   3) 末尾自动汇总四行（含历史基线 bc20000_clean.json）。
#
# 用法（远端）:
#   cd /mnt/workspace/roco_remote && bash run_bc_arch_arms.sh [数据 npz] [轮数]
set -u
export PYTHONHASHSEED=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

DATA="${1:-bc_20000_clean.npz}"
EPOCHS="${2:-6}"
COMMON="--data $DATA --epochs $EPOCHS --batch-size 256 --seed 7"

run_arm() {           # $1=输出名  $2..=架构开关
  local out="$1"; shift
  if [ -f "${out%.pt}.json" ]; then
    echo "== 跳过 $out（已有 sidecar）=="
    return 0
  fi
  echo "== 开始 $out  [$*] =="
  python -u -X utf8 -m backend.engine.ai.bc_pretrain $COMMON --out "$out" "$@" \
    || echo "!! $out 失败（rc=$?），继续下一臂"
}

run_arm bc20k_v4_rebase.pt
run_arm bc20k_v5_slotaux.pt --slot-pool --aux-heads --aux-weight 0.3
run_arm bc20k_v6_slotaux_hist.pt --slot-pool --aux-heads --aux-weight 0.3 --history 4

echo
echo "===== 汇总 ====="
python - <<'PY'
import json
from pathlib import Path
rows = []
for name in ("bc20000_clean.json", "bc20k_v4_rebase.json",
             "bc20k_v5_slotaux.json", "bc20k_v6_slotaux_hist.json"):
    p = Path(name)
    if not p.exists():
        rows.append((name, None))
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    c, h = d.get("config", {}), d.get("final_history", {})
    rows.append((name, (d["best_epoch"], d["best_val_policy_top1"],
                        h.get("val_policy_top3", 0.0), h.get("val_v_loss", 0.0),
                        h.get("val_aux_loss", 0.0), c.get("params", 0),
                        c.get("slot_pool"), c.get("aux_heads"), c.get("history"))))
print(f"{'arm':<28}{'best_ep':>8}{'top1':>9}{'top3':>9}{'val_v':>9}{'aux':>8}{'params':>10}  flags")
for name, v in rows:
    if v is None:
        print(f"{name:<28}{'（缺）':>8}")
        continue
    ep, t1, t3, vv, ax, ps, sp, ah, hi = v
    print(f"{name:<28}{ep:>8}{t1:>9.4f}{t3:>9.4f}{vv:>9.4f}{ax:>8.4f}{ps:>10}"
          f"  slot={sp} aux={ah} hist={hi}")
PY
echo BC_ARCH_ARMS_DONE
