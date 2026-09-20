# -*- coding: utf-8 -*-
"""native/tools/fixture_summary.py — 打印夹具的结局/回合/阵容摘要。

用法: python native/tools/fixture_summary.py [目录] [输出文件]
输出文件省略时打印到 stdout（注意 PowerShell 控制台默认 GBK，
中文会乱码——建议总是指定输出文件，UTF-8 写入）。
"""
import json
import sys
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/engine/differential/fixtures")
lines = []
for p in sorted(d.glob("battle_*.json")):
    data = json.loads(p.read_text(encoding="utf-8"))
    ta = [s["name"] for s in data.get("team_a", [])]
    tb = [s["name"] for s in data.get("team_b", [])]
    lines.append(f"{p.stem}  winner={data.get('winner') or 'draw':<4} "
                 f"turns={data.get('turns_count'):<3} "
                 f"A=[{','.join(ta)}] vs B=[{','.join(tb)}]")

text = "\n".join(lines) + "\n"
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(text, encoding="utf-8")
else:
    sys.stdout.reconfigure(encoding="utf-8")
    print(text, end="")
