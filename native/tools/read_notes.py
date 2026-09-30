"""read_notes.py — 汇总实验 NOTES 头部。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for d in ("exp14-debug_policy_collapse", "exp15_pipeline_fix", "exp16", "exp11", "exp12"):
    p = ROOT / "checkpoints" / d / "NOTES.md"
    print("=" * 30, d, "=" * 30)
    if p.exists():
        lines = p.read_text(encoding="utf-8").splitlines()
        print("\n".join(lines[:75]))
        print(f"...（共 {len(lines)} 行）")
    else:
        print("（无 NOTES.md）")
    print()
