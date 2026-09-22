# -*- coding: utf-8 -*-
"""汇总"全局规则消融"的结果：每队 × 每条规则 → 胜率（新口径 = 该规则 ON）。

WR < 0.5 = 这条全场规则对这支队有害（候选队级例外，如 新地武 关掉 status_counter）。
读 ablate/<队>_<mode>.json（eval_expert_change.py --json-out 的产物）。
"""
import argparse
import glob
import json
import math
from pathlib import Path

MODES = ("status", "trade", "antiloop")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="ablate")
    a = ap.parse_args()
    teams = sorted({Path(p).name.rsplit("_", 1)[0] for p in glob.glob(f"{a.dir}/*.json")})
    print(f'{"队伍":18s} {"status":>16s} {"trade":>16s} {"antiloop":>16s}   判定')
    for team in teams:
        cells, verdicts = [], []
        for mode in MODES:
            p = Path(a.dir) / f"{team}_{mode}.json"
            if not p.exists():
                cells.append(f'{"—":>16s}')
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            w, l = d["new_wins"], d["new_losses"]
            dec = w + l
            if not dec:
                cells.append(f'{"全平":>16s}')
                continue
            wr = w / dec
            half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / dec)
            mark = "↓害" if wr + half < 0.5 else ("↑好" if wr - half > 0.5 else "  ")
            cells.append(f'{wr:.3f}±{half:.3f}{mark}')
            if wr + half < 0.5:
                verdicts.append(mode)
        note = ("建议关掉: " + ",".join(verdicts)) if verdicts else "三条都不显著"
        print(f'{team[:16]:18s} {cells[0]} {cells[1]} {cells[2]}   {note}')


if __name__ == "__main__":
    main()
