# -*- coding: utf-8 -*-
"""read_notes.py — 汇总实验 NOTES 头部。"""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for d in ("exp14-debug_policy_collapse", "exp15_pipeline_fix", "exp16", "exp11", "exp12"):
    p = ROOT / "checkpoints" / d / "NOTES.md"
    print("=" * 30, d, "=" * 30)
    if p.exists():
        lines = io.open(p, encoding="utf-8").read().splitlines()
        print("\n".join(lines[:75]))
        print(f"...（共 {len(lines)} 行）")
    else:
        print("（无 NOTES.md）")
    print()
