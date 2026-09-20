"""dbg_ls_traits — 找到名字含关键词的特性文件并打印。

用法：env\\python.exe native/tools/dbg_ls_traits.py <关键词>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

kw = sys.argv[1] if len(sys.argv) > 1 else ""
tdir = ROOT / "data" / "traits"
hits = []
for f in sorted(tdir.glob("*.json")):
    try:
        txt = f.read_text(encoding="utf-8")
    except Exception:
        continue
    if kw in txt or kw in f.name:
        hits.append(f)
for f in hits:
    print("=" * 20, f.name)
    print(f.read_text(encoding="utf-8"))
if not hits:
    print("no hits for", kw)
