"""flt_t45 — 过滤 _dbg_t45_log.txt 的 turn>=45 真实战斗行。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
lines = (ROOT / "native" / "tools" / "_dbg_t45_log.txt").read_text(encoding="utf-8").splitlines()
out = []
for i, row_value in enumerate(lines):
    if "turn=45" in row_value and "sim=True" not in row_value:
        out.extend(lines[i:i + 4])
sys.stdout.write("\n".join(out) + "\n")
