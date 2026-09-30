"""dbg_bytes — 检查日志文件编码并打印关键行。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

data = Path("native/tools/d3.log").read_bytes()
print("head bytes:", data[:8])
for enc in ("utf-16", "utf-8", "gbk"):
    try:
        text = data.decode(enc)
        print(f"--- {enc} ok, lines={len(text.splitlines())}")
        hits = [row_value for row_value in text.splitlines() if "op_hit" in row_value]
        for row_value in hits[:4]:
            print("   ", row_value[-70:])
        break
    except Exception as exc:  # noqa: BLE001
        print(f"--- {enc} fail: {exc}")
