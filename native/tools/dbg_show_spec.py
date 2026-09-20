"""dbg_show_spec — 打印 spec 的阵容与种子（UTF-8 安全版，可写文件）。

用法：env\\python.exe native/tools/dbg_show_spec.py <spec_id> [输出文件]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sid = sys.argv[1]
spec = json.loads((ROOT / f"native/gate_specs/spec_{sid}.json").read_text(encoding="utf-8"))
lines = [f"seed: {spec.get('seed')} max_turns: {spec.get('max_turns')}"]
for lbl, p in zip("AB", spec["players"]):
    lines.append(f"player {lbl}:")
    for i, sp in enumerate(p["sprites"]):
        skills = [s if isinstance(s, str) else s.get("name") for s in sp.get("skills", [])]
        lines.append(f"  {lbl}[{i}] {sp.get('name')} 特性={sp.get('ability')} 技能={skills}")
text = "\n".join(lines)
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(text, encoding="utf-8")
    print(f"written -> {sys.argv[2]}")
else:
    print(text)
