# -*- coding: utf-8 -*-
"""汇总逐规则矩阵的结果：expert/<prefix>_<tag>.json → 表。"""
import argparse
import glob
import json
import math
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="expert")
    ap.add_argument("--prefix", required=True)
    a = ap.parse_args()
    rows = []
    for p in sorted(glob.glob(f"{a.dir}/{a.prefix}_*.json")):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        tag = Path(p).stem[len(a.prefix) + 1:]
        w, l, dr = d["wins"], d["losses"], d["draws"]
        dec = w + l
        wr = w / dec if dec else float("nan")
        half = 1.96 * math.sqrt(max(1e-9, wr * (1 - wr)) / dec) if dec else 0.0
        rows.append((wr, tag, w, l, dr, half, d.get("mean_turns", 0),
                     d.get("rule_hits", {})))
    print(f'{"规则":22s} {"胜":>4s} {"负":>4s} {"平":>4s} {"胜率":>7s} ±CI     均回合  命中')
    for wr, tag, w, l, dr, half, mt, hits in sorted(rows, key=lambda x: -(x[0] if x[0] == x[0] else -1)):
        hit_txt = ",".join(f"{k}:{v}" for k, v in list(hits.items())[:3])
        print(f"{tag:22s} {w:4d} {l:4d} {dr:4d} {wr:7.3f} ±{half:.3f}  {mt:6.1f}  {hit_txt}")


if __name__ == "__main__":
    main()
