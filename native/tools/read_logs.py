# -*- coding: utf-8 -*-
"""read_logs.py — 汇总 exp14/15/16 运行日志摘要 + 用户未提交训练代码 diff。"""
import io
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
out = io.open(ROOT / "native" / "tools" / "_logs_read.txt", "w", encoding="utf-8")


def w(*a):
    out.write(" ".join(str(x) for x in a) + "\n")


for d in ("exp14-debug_policy_collapse", "exp15_pipeline_fix", "exp16"):
    dd = ROOT / "checkpoints" / d
    w("=" * 28, d, "=" * 28)
    for f in sorted(dd.glob("*")):
        if f.suffix == ".pt":
            continue
        w("  file:", f.name, f"({f.stat().st_size}B)")
    jsonls = sorted(dd.glob("*.jsonl"))
    if jsonls:
        rows = [json.loads(l) for l in io.open(jsonls[-1], encoding="utf-8") if l.strip()]
        its = [r for r in rows if r.get("type") == "iteration"]
        starts = [r for r in rows if r.get("type") == "run_start"]
        if starts:
            w("  params:", json.dumps(starts[0].get("params", {}), ensure_ascii=False))
        w("  iterations:", len(its))
        if its:
            import statistics as st
            for k in ("win_rate", "final_val_acc", "samples", "iteration_sec", "draw_ratio"):
                v = [r.get(k) for r in its if r.get(k) is not None]
                if v:
                    w(f"  {k}: mean={st.mean(v):.3f} min={min(v):.3f} max={max(v):.3f}")
            promo = [(r["iteration"], r.get("gate"), r.get("reason", ""))
                     for r in its if r.get("promoted")]
            w("  promoted:", promo)
            w("  win_rate seq:", [round(r.get("win_rate", -1), 3) for r in its])
            w("  val_acc seq:", [round(r.get("final_val_acc", -1), 3) for r in its])
    w()

w("=" * 28, "git diff --stat（未提交训练代码）", "=" * 28)
r = subprocess.run(["git", "diff", "--stat"], cwd=str(ROOT), capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
w(r.stdout)
w("=" * 28, "git diff train.py/replay_buffer/selfplay_worker（摘要）", "=" * 28)
r = subprocess.run(["git", "diff", "--",
                    "backend/engine/ai/train.py", "backend/engine/ai/core/replay_buffer.py",
                    "backend/engine/ai/tests/selfplay_worker.py"],
                   cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
w(r.stdout[:12000])
out.close()
print("ok")
