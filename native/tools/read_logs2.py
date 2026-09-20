# -*- coding: utf-8 -*-
"""read_logs2.py — log 目录全览 + exp14/15/16 运行记录 + train.py 参数面 + eval CLI。"""
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
out = io.open(ROOT / "native" / "tools" / "_logs_read2.txt", "w", encoding="utf-8")


def w(*a):
    out.write(" ".join(str(x) for x in a) + "\n")


LOG = ROOT / "backend" / "engine" / "ai" / "log"
w("== log 目录 ==")
for d in sorted(LOG.iterdir()):
    if d.is_dir():
        runs = sorted(d.glob("run_*.jsonl"))
        w(f"  {d.name}: {len(runs)} runs, last={runs[-1].name if runs else '-'}")

for name in ("exp14-debug_policy_collapse", "exp15_pipeline_fix", "exp16", "formal_v1"):
    d = LOG / name
    w("=" * 25, name, "=" * 25)
    if not d.exists():
        w("  （无 log 目录）")
        continue
    for jf in sorted(d.glob("run_*.jsonl")):
        rows = [json.loads(l) for l in io.open(jf, encoding="utf-8") if l.strip()]
        starts = [r for r in rows if r.get("type") == "run_start"]
        its = [r for r in rows if r.get("type") == "iteration"]
        ends = [r for r in rows if r.get("type") == "run_end"]
        w(f"  {jf.name}: iters={len(its)}")
        if starts:
            w("    params:", json.dumps(starts[0].get("params", {}), ensure_ascii=False))
        if its:
            import statistics as st
            for k in ("win_rate", "final_val_acc", "samples", "iteration_sec"):
                v = [r.get(k) for r in its if r.get(k) is not None]
                if v:
                    w(f"    {k}: mean={st.mean(v):.3f} [{min(v):.3f},{max(v):.3f}]")
            w("    win_rate seq:", [round(r.get("win_rate", -1), 2) for r in its])
            w("    promoted:", [r["iteration"] for r in its if r.get("promoted")])
        if ends:
            w("    run_end:", json.dumps({k: v for k, v in ends[0].items()
                                          if k in ("elapsed_sec", "iterations_done", "total_promotions")},
                                         ensure_ascii=False))
out.close()
print("ok")
