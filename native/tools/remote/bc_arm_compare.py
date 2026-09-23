# -*- coding: utf-8 -*-
"""两臂 BC 训练结果对照：读 `--out` 旁边的 json sidecar 打印关键指标与差值。

用法: python bc_arm_compare.py <旧臂.json> <新臂.json> [标签A] [标签B]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FIELDS = (
    ("best_val_policy_top1", "留出队 top-1（最好 epoch）", "{:.3f}"),
    ("top1", "留出队 top-1（末轮）", "{:.3f}"),
    ("top3", "留出队 top-3", "{:.3f}"),
    ("val_acc", "胜负准确率", "{:.3f}"),
    ("policy_entropy", "策略熵 H（越小越确定）", "{:.3f}"),
    ("val_p_loss", "策略损失", "{:.4f}"),
    ("val_v_loss", "价值损失", "{:.4f}"),
)
HIST = {"best_val_policy_top1": "best_val_policy_top1",
        "top1": "val_policy_top1", "top3": "val_policy_top3",
        "val_acc": "val_acc", "policy_entropy": "val_policy_entropy",
        "val_p_loss": "val_p_loss", "val_v_loss": "val_v_loss"}


def load(path: str) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {"best_val_policy_top1": d.get("best_val_policy_top1") or float("nan"),
           "best_epoch": d["best_epoch"], "samples": d["samples"],
           "holdout_teams": d["holdout_teams"]}
    for k in HIST.values():
        out[k] = d["final_history"].get(k, float("nan"))
    return out


def main() -> None:
    a, b = load(sys.argv[1]), load(sys.argv[2])
    la = sys.argv[3] if len(sys.argv) > 3 else "A"
    lb = sys.argv[4] if len(sys.argv) > 4 else "B"
    print(f"{'指标':<26}{la:>12}{lb:>12}{'差':>10}")
    for key, label, fmt in FIELDS:
        va, vb = a[HIST[key]], b[HIST[key]]
        print(f"{label:<26}{fmt.format(va):>12}{fmt.format(vb):>12}"
              f"{fmt.format(vb - va):>10}")
    print(f"{'样本量':<26}{a['samples']:>12}{b['samples']:>12}"
          f"{b['samples'] - a['samples']:>+10}")
    print(f"留出队 {a['holdout_teams']} vs {b['holdout_teams']}"
          f"｜best_epoch {a['best_epoch']} vs {b['best_epoch']}")


if __name__ == "__main__":
    main()
