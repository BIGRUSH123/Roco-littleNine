"""flt_t45 — 过滤 _dbg_t45_log.txt 的 turn>=45 真实战斗行。"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
lines = io.open(ROOT / "native" / "tools" / "_dbg_t45_log.txt", encoding="utf-8").read().splitlines()
out = []
for i, l in enumerate(lines):
    if "turn=45" in l and "sim=True" not in l:
        out.extend(lines[i:i + 4])
sys.stdout.write("\n".join(out) + "\n")
